#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adc-mid2adt.py 260806c

Convert split drum-pattern MIDI files to ADT v2.3 text files.

Default workflow
----------------
1. adc-midi-split.py writes selected MIDI patterns into ./split-midi
2. Run this script with the reviewed PatternLab CSV as the required positional input
3. MIDI files are read from ./split-midi by default; use --input-dir to override
4. ADT files are written to ./ADT by default

ADT v2.3 output
---------------
- First line: ; ADT v2.3
- NAME, TIME_SIG, SUBDIV, LENGTH
- Default KIT=GM_STD, SLOT_MAP_ID=LEGACY, ORIENTATION=STEP are omitted
- SLOT_MAP_ID=INLINE requires matching SLOT0...SLOTn definitions
- [DATA] marker
- STEP-major data by default (one row per step, one character per slot)
- SUBDIV values: 16, 32, 8T, 16T
- Accent symbols are loaded from accent_levels.json (6-accent)

Required PatternLab CSV
-----------------------
A reviewed PatternLab CSV is the required positional input.
The split-MIDI input directory is optional and defaults to ./split-midi.

Requirements
------------
    pip install mido
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from mido import Message, MetaMessage, MidiFile

from adc_rhythm_analysis import SUPPORTED_RESOLUTIONS, detect_flams

SCRIPT_NAME = "adc-mid2adt.py"
VERSION = "260806c"
VERSION_TEXT = f"{SCRIPT_NAME} {VERSION}"
ADT_VERSION = "ADT v2.3"
DEFAULT_PPQN = 240
DEFAULT_KIT = "GM_STD"
DEFAULT_SLOT_MAP = "LEGACY"
DEFAULT_ORIENTATION = "STEP"
DEFAULT_ACCENT_SCHEME = "6-accent"
VALID_SUBDIV = {"16", "32", "8T", "16T"}
NAME_RE = re.compile(r"^[A-Z0-9]{3}_[0-9]{4}$")
SUBDIV_PER_QUARTER = {"16": 4, "32": 8, "8T": 3, "16T": 6}

if tuple(SUPPORTED_RESOLUTIONS) != ("16", "32", "8T", "16T"):
    raise RuntimeError(
        "Straight-32 capable adc_rhythm_analysis.py is required in the same directory "
        "(supported resolutions must be 16, 32, 8T, 16T)."
    )


@dataclass(frozen=True)
class SlotDefinition:
    index: int
    abbrev: str
    extended: str
    representative_midi: int
    allowed_notes: Tuple[int, ...]


@dataclass(frozen=True)
class SlotMapDefinition:
    map_id: int
    name: str
    slots: Tuple[SlotDefinition, ...]

    @property
    def accepted_notes(self) -> Set[int]:
        notes: Set[int] = set()
        for slot in self.slots:
            notes.update(slot.allowed_notes)
        return notes

    def slot_for_note(self, note: int) -> Optional[int]:
        for slot in self.slots:
            if note in slot.allowed_notes:
                return slot.index
        return None


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    subdiv: str
    slot_map: str
    source: str
    time_sig: str


@dataclass(frozen=True)
class DrumHit:
    tick: int
    note: int
    velocity: int


@dataclass(frozen=True)
class MidiPattern:
    tpq: int
    time_sig: Tuple[int, int]
    total_ticks: int
    hits: Tuple[DrumHit, ...]


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"[ERROR] {message}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description="Convert split CH10 MIDI patterns to ADT v2.3.",
    )
    parser.add_argument(
        "catalog_csv",
        type=Path,
        help="Reviewed PatternLab CSV (required)",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("split-midi"),
        help="Directory containing split MIDI files (default: ./split-midi)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: <input-dir parent>/ADT)",
    )
    parser.add_argument(
        "--slot-maps",
        type=Path,
        default=None,
        help="slot_map_definitions.json (default: beside this script)",
    )
    parser.add_argument(
        "--subdiv",
        choices=sorted(VALID_SUBDIV),
        default=None,
        help="Override CSV SUBDIV for every input file",
    )
    parser.add_argument(
        "--slot-map",
        default=None,
        help="Override CSV SLOT_MAP for every input file",
    )
    parser.add_argument("--kit", default=DEFAULT_KIT, help=f"KIT value (default: {DEFAULT_KIT})")
    parser.add_argument(
        "--orientation",
        choices=["STEP", "SLOT"],
        default=DEFAULT_ORIENTATION,
        help=f"Data orientation (default: {DEFAULT_ORIENTATION})",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=10,
        help="Drum MIDI channel, 1-based (default: 10)",
    )
    parser.add_argument(
        "--accent-levels",
        type=Path,
        default=None,
        help="accent_levels.json (default: beside this script)",
    )
    parser.add_argument(
        "--write-ppqn",
        action="store_true",
        help="Always write PPQN=<MIDI TPQ>; otherwise omit when TPQ is the default 240",
    )
    parser.add_argument("--recursive", action="store_true", help="Process subdirectories in directory mode")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing ADT files")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the conversion plan only")
    parser.add_argument("--version", action="version", version=VERSION_TEXT)
    args = parser.parse_args(argv)
    if not 1 <= args.channel <= 16:
        parser.error("--channel must be 1..16")
    return args


def load_accent_scheme(path: Path, scheme_name: str = DEFAULT_ACCENT_SCHEME) -> Tuple[dict, ...]:
    """Load and validate the authoritative ADX accent scheme."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"accent-level definition not found: {path}")
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read accent-level definition {path}: {exc}")

    schemes = data.get("schemes") if isinstance(data, dict) else None
    scheme = schemes.get(scheme_name) if isinstance(schemes, dict) else None
    levels = scheme.get("levels") if isinstance(scheme, dict) else None
    if not isinstance(levels, list) or len(levels) != 6:
        fail(f"{scheme_name}: exactly 6 levels are required, including rest")

    validated = []
    expected_min = 0
    seen_symbols = set()
    for index, level in enumerate(levels):
        if not isinstance(level, dict):
            fail(f"{scheme_name} level {index}: must be an object")
        if level.get("index") != index:
            fail(f"{scheme_name} level {index}: index must equal its array position")
        lo = level.get("min_velocity")
        hi = level.get("max_velocity")
        rep = level.get("representative_velocity")
        symbol = level.get("symbol")
        if not all(isinstance(value, int) for value in (lo, hi, rep)):
            fail(f"{scheme_name} level {index}: velocity values must be integers")
        if lo != expected_min or not 0 <= lo <= hi <= 127:
            fail(f"{scheme_name} level {index}: ranges must be contiguous and cover 0..127")
        if not lo <= rep <= hi:
            fail(f"{scheme_name} level {index}: representative_velocity must lie within its range")
        if not isinstance(symbol, str) or len(symbol) != 1 or symbol in seen_symbols:
            fail(f"{scheme_name} level {index}: symbol must be one unique character")
        if index == 0 and not (lo == hi == rep == 0 and symbol == "."):
            fail(f"{scheme_name}: level 0 must be Rest with velocity 0 and symbol '.'")
        seen_symbols.add(symbol)
        expected_min = hi + 1
        validated.append(level)
    if expected_min != 128:
        fail(f"{scheme_name}: ranges must end at velocity 127")
    return tuple(validated)


def accent_symbol(velocity: int, levels: Tuple[dict, ...]) -> str:
    value = max(0, min(127, int(velocity)))
    for level in levels:
        if level["min_velocity"] <= value <= level["max_velocity"]:
            symbol = str(level["symbol"])
            if level["index"] == 0:
                fail("a present MIDI note cannot map to Rest")
            return symbol
    fail(f"velocity {value} is not covered by {DEFAULT_ACCENT_SCHEME}")


def load_slot_maps(path: Path) -> Dict[str, SlotMapDefinition]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"slot-map definition not found: {path}")
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read slot-map definition {path}: {exc}")

    if not isinstance(data, list) or not data:
        fail("slot-map JSON root must be a non-empty array")

    maps: Dict[str, SlotMapDefinition] = {}
    seen_ids: Set[int] = set()
    for raw_map in data:
        if not isinstance(raw_map, dict):
            fail("each slot-map entry must be an object")
        map_id = raw_map.get("slot_map_id")
        name = raw_map.get("name")
        raw_slots = raw_map.get("slots")
        if not isinstance(map_id, int) or map_id in seen_ids:
            fail(f"invalid or duplicate slot_map_id: {map_id!r}")
        if not isinstance(name, str) or not name.strip() or name in maps:
            fail(f"invalid or duplicate slot-map name: {name!r}")
        if not isinstance(raw_slots, list) or not raw_slots:
            fail(f"slot map {name}: slots must be a non-empty list")

        slots: List[SlotDefinition] = []
        seen_indices: Set[int] = set()
        for raw_slot in raw_slots:
            if not isinstance(raw_slot, dict):
                fail(f"slot map {name}: every slot must be an object")
            index = raw_slot.get("slot")
            abbrev = raw_slot.get("abbrev")
            extended = raw_slot.get("extended", abbrev)
            representative = raw_slot.get("representative_midi")
            allowed = raw_slot.get("midi_input_allowed")
            if not isinstance(index, int) or index in seen_indices:
                fail(f"slot map {name}: invalid or duplicate slot number {index!r}")
            if not isinstance(abbrev, str) or not abbrev.strip():
                fail(f"slot map {name}, slot {index}: missing abbrev")
            if not isinstance(extended, str) or not extended.strip():
                fail(f"slot map {name}, slot {index}: missing extended")
            if not isinstance(representative, int):
                fail(f"slot map {name}, slot {index}: invalid representative_midi")
            if not isinstance(allowed, list) or not allowed or any(not isinstance(n, int) for n in allowed):
                fail(f"slot map {name}, slot {index}: invalid midi_input_allowed")
            if representative not in allowed:
                fail(f"slot map {name}, slot {index}: representative_midi must be allowed")
            seen_indices.add(index)
            slots.append(
                SlotDefinition(
                    index=index,
                    abbrev=abbrev.strip(),
                    extended=extended.strip(),
                    representative_midi=representative,
                    allowed_notes=tuple(int(n) for n in allowed),
                )
            )

        slots.sort(key=lambda slot: slot.index)
        if [slot.index for slot in slots] != list(range(len(slots))):
            fail(f"slot map {name}: slot numbers must be contiguous 0..{len(slots)-1}")
        seen_ids.add(map_id)
        maps[name] = SlotMapDefinition(map_id, name.strip(), tuple(slots))

    return maps


def load_catalog(path: Path) -> Dict[str, CatalogEntry]:
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        fail(f"cannot open catalog CSV {path}: {exc}")

    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            fail(f"catalog CSV has no header: {path}")
        fields = [str(field).strip().upper() for field in reader.fieldnames]
        reader.fieldnames = fields
        required = {"NAME", "SUBDIV", "SLOT_MAP", "SOURCE", "TIME_SIG", "EXPORT"}
        missing = sorted(required - set(fields))
        if missing:
            fail(f"catalog CSV missing column(s): {', '.join(missing)}")

        entries: Dict[str, CatalogEntry] = {}
        for row_number, raw in enumerate(reader, start=2):
            row = {str(k).strip().upper(): str(v or "").strip() for k, v in raw.items() if k is not None}
            if row.get("EXPORT", "").upper() != "YES":
                continue
            name = row.get("NAME", "").upper()
            if not NAME_RE.fullmatch(name):
                fail(f"catalog CSV row {row_number}: invalid NAME {name!r}")
            subdiv = row.get("SUBDIV", "").upper()
            if subdiv not in VALID_SUBDIV:
                fail(f"catalog CSV row {row_number}: invalid SUBDIV {subdiv!r}")
            if name in entries:
                fail(f"catalog CSV row {row_number}: duplicate NAME {name}")
            entries[name] = CatalogEntry(
                name=name,
                subdiv=subdiv,
                slot_map=row.get("SLOT_MAP", ""),
                source=row.get("SOURCE", ""),
                time_sig=row.get("TIME_SIG", ""),
            )
    return entries


def read_midi_pattern(path: Path, channel_one_based: int) -> MidiPattern:
    try:
        mid = MidiFile(str(path))
    except Exception as exc:
        fail(f"cannot read MIDI {path}: {exc}")
    if mid.type not in (0, 1):
        fail(f"{path.name}: only SMF Type 0 or 1 is supported")
    if mid.ticks_per_beat <= 0:
        fail(f"{path.name}: invalid ticks_per_beat {mid.ticks_per_beat}")

    merged = mid.tracks[0] if mid.type == 0 else __import__("mido").merge_tracks(mid.tracks)
    tick = 0
    total_tick = 0
    time_sig = (4, 4)
    channel = channel_one_based - 1
    hits: List[DrumHit] = []
    for msg in merged:
        tick += msg.time
        total_tick = max(total_tick, tick)
        if isinstance(msg, MetaMessage) and msg.type == "time_signature" and tick == 0:
            time_sig = (int(msg.numerator), int(msg.denominator))
        elif (
            isinstance(msg, Message)
            and msg.type == "note_on"
            and msg.velocity > 0
            and getattr(msg, "channel", -1) == channel
        ):
            hits.append(DrumHit(tick, int(msg.note), int(msg.velocity)))

    if not hits:
        fail(f"{path.name}: no note_on events on MIDI channel {channel_one_based}")
    if total_tick <= 0:
        total_tick = max(hit.tick for hit in hits) + 1
    return MidiPattern(mid.ticks_per_beat, time_sig, total_tick, tuple(hits))


def infer_slot_map(notes: Set[int], maps: Dict[str, SlotMapDefinition]) -> SlotMapDefinition:
    exact = [slot_map for slot_map in maps.values() if notes <= slot_map.accepted_notes]
    if exact:
        return min(exact, key=lambda item: item.map_id)

    def score(slot_map: SlotMapDefinition) -> Tuple[int, int, int, int]:
        covered = len(notes & slot_map.accepted_notes)
        missing = len(notes - slot_map.accepted_notes)
        unused = len(slot_map.accepted_notes - notes)
        return covered, -missing, -slot_map.map_id, -unused

    chosen = max(maps.values(), key=score)
    missing = sorted(notes - chosen.accepted_notes)
    fail(f"no SLOT_MAP accepts all MIDI notes; nearest {chosen.name}, missing {missing}")


def expected_length(pattern: MidiPattern, subdiv: str) -> int:
    ticks_per_step = pattern.tpq / SUBDIV_PER_QUARTER[subdiv]
    if ticks_per_step <= 0:
        fail("internal error: non-positive ticks per step")
    value = pattern.total_ticks / ticks_per_step
    rounded = round(value)
    if not math.isclose(value, rounded, abs_tol=0.08):
        fail(
            f"MIDI length {pattern.total_ticks} ticks is not compatible with SUBDIV={subdiv} "
            f"at PPQN={pattern.tpq} (calculated steps={value:.3f})"
        )
    return max(1, int(rounded))


def build_grid(
    pattern: MidiPattern,
    slot_map: SlotMapDefinition,
    subdiv: str,
    accent_levels: Tuple[dict, ...],
) -> Tuple[List[List[str]], int]:
    length = expected_length(pattern, subdiv)
    grid = [["." for _ in slot_map.slots] for _ in range(length)]
    ticks_per_step = pattern.tpq / SUBDIV_PER_QUARTER[subdiv]
    strength = {str(level["symbol"]): int(level["index"]) for level in accent_levels}

    # Resolution was selected and reviewed in PatternLab. Apply the shared flam
    # policy using that selected resolution: grid-representable notes remain in
    # ADT, and only genuinely off-grid grace notes are excluded for ORN.
    flam_events = [
        {"tick": hit.tick, "note": hit.note, "velocity": hit.velocity, "track": 0}
        for hit in pattern.hits
    ]
    flam_analysis = detect_flams(
        flam_events,
        pattern.tpq,
        loop_ticks=pattern.total_ticks,
        loop_start=0,
        selected_resolution=subdiv,
    )
    excluded_indices = {
        int(item["grace_index"])
        for item in flam_analysis.get("flams", [])
        if item.get("remove_from_subdivision") and "grace_index" in item
    }

    for hit_index, hit in enumerate(pattern.hits):
        if hit_index in excluded_indices:
            continue
        slot = slot_map.slot_for_note(hit.note)
        if slot is None:
            fail(f"SLOT_MAP {slot_map.name} does not accept MIDI note {hit.note}")
        step = int(round(hit.tick / ticks_per_step))
        step = max(0, min(length - 1, step))
        char = accent_symbol(hit.velocity, accent_levels)
        if strength[char] > strength[grid[step][slot]]:
            grid[step][slot] = char
    return grid, len(excluded_indices)


def write_adt(
    output_path: Path,
    *,
    name: str,
    source: str,
    pattern: MidiPattern,
    subdiv: str,
    kit: str,
    orientation: str,
    slot_map: SlotMapDefinition,
    grid: List[List[str]],
    write_ppqn: bool,
) -> None:
    num, den = pattern.time_sig
    lines: List[str] = [
        f"; {ADT_VERSION}",
        "; Drum Pattern Exchange Format",
        "; Lines beginning with ';' are comments.",
        "; Blank lines shall be ignored.",
        "; The first line shall declare the ADT version.",
        "",
        f"NAME={name}",
    ]
    if source:
        lines.append(f"SOURCE={source}")
    if write_ppqn or pattern.tpq != DEFAULT_PPQN:
        lines.append(f"PPQN={pattern.tpq}")
    lines.extend(
        [
            "",
            f"TIME_SIG={num}/{den}",
            f"SUBDIV={subdiv}",
            f"LENGTH={len(grid)}",
        ]
    )
    if kit and kit.upper() != DEFAULT_KIT:
        lines.append(f"KIT={kit}")

    optional_format_lines: List[str] = []
    if slot_map.name.upper() != DEFAULT_SLOT_MAP:
        optional_format_lines.append(f"SLOT_MAP_ID={slot_map.name}")
    if orientation != DEFAULT_ORIENTATION:
        optional_format_lines.append(f"ORIENTATION={orientation}")
    if slot_map.name.upper() == "INLINE":
        for slot in slot_map.slots:
            optional_format_lines.append(
                f"SLOT{slot.index}={slot.abbrev}@{slot.representative_midi},{slot.extended}"
            )
    if optional_format_lines:
        lines.extend(["", *optional_format_lines])
    lines.extend(["", "[DATA]"])

    if orientation == "STEP":
        lines.extend("".join(row) for row in grid)
    else:
        for slot_index in range(len(slot_map.slots)):
            lines.append("".join(grid[step][slot_index] for step in range(len(grid))))

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def iter_midi_files(path: Path, recursive: bool) -> Iterable[Path]:
    if path.is_file():
        if path.suffix.lower() in {".mid", ".midi"}:
            yield path
        return
    iterator = path.rglob("*") if recursive else path.glob("*")
    files = [item for item in iterator if item.is_file() and item.suffix.lower() in {".mid", ".midi"}]
    yield from sorted(files, key=lambda item: item.name.casefold())


def resolve_paths(args: argparse.Namespace) -> Tuple[Path, Path]:
    input_path = args.input_dir
    if not input_path.exists():
        fail(f"input directory not found: {input_path}")
    if not input_path.is_dir():
        fail(f"--input-dir must be a directory: {input_path}")
    output_dir = args.out_dir or input_path.parent / "ADT"
    return input_path, output_dir


def convert_one(
    midi_path: Path,
    output_dir: Path,
    *,
    maps: Dict[str, SlotMapDefinition],
    catalog: Dict[str, CatalogEntry],
    args: argparse.Namespace,
    accent_levels: Tuple[dict, ...],
) -> Tuple[bool, str]:
    name = midi_path.stem.upper()
    if not NAME_RE.fullmatch(name):
        return False, f"invalid NAME from filename: {midi_path.name} (expected ABC_0001.MID)"

    entry = catalog.get(name)
    if entry is None:
        return False, f"NAME not found among EXPORT=YES rows in catalog: {name}"
    pattern = read_midi_pattern(midi_path, args.channel)

    if entry and entry.time_sig:
        actual_ts = f"{pattern.time_sig[0]}/{pattern.time_sig[1]}"
        if entry.time_sig != actual_ts:
            return False, f"catalog TIME_SIG={entry.time_sig} but MIDI contains {actual_ts}: {midi_path.name}"

    subdiv = args.subdiv or entry.subdiv
    slot_map_name = args.slot_map or entry.slot_map
    if slot_map_name:
        if slot_map_name not in maps:
            return False, f"unknown SLOT_MAP {slot_map_name!r}: {midi_path.name}"
        slot_map = maps[slot_map_name]
    else:
        slot_map = infer_slot_map({hit.note for hit in pattern.hits}, maps)

    source = entry.source
    grid, excluded_flam_graces = build_grid(pattern, slot_map, subdiv, accent_levels)
    output_path = output_dir / f"{name}.ADT"
    if output_path.exists() and not args.overwrite:
        return False, f"exists: {output_path} (use --overwrite)"

    if args.dry_run:
        return True, (
            f"plan: {midi_path.name} -> {output_path.name} "
            f"({pattern.time_sig[0]}/{pattern.time_sig[1]}, SUBDIV={subdiv}, "
            f"LENGTH={len(grid)}, SLOT_MAP={slot_map.name}, FLAM_GRACE_EXCLUDED={excluded_flam_graces})"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_adt(
        output_path,
        name=name,
        source=source,
        pattern=pattern,
        subdiv=subdiv,
        kit=args.kit,
        orientation=args.orientation,
        slot_map=slot_map,
        grid=grid,
        write_ppqn=args.write_ppqn,
    )
    return True, (
        f"{midi_path.name} -> {output_path.name} "
        f"({pattern.time_sig[0]}/{pattern.time_sig[1]}, SUBDIV={subdiv}, "
        f"LENGTH={len(grid)}, SLOT_MAP={slot_map.name}, FLAM_GRACE_EXCLUDED={excluded_flam_graces})"
    )



def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    input_path, output_dir = resolve_paths(args)
    slot_map_path = args.slot_maps or Path(__file__).with_name("slot_map_definitions.json")
    maps = load_slot_maps(slot_map_path)
    accent_levels_path = args.accent_levels or Path(__file__).with_name("accent_levels.json")
    accent_levels = load_accent_scheme(accent_levels_path)
    catalog_csv = args.catalog_csv
    if not catalog_csv.is_file():
        fail(f"catalog CSV not found: {catalog_csv}")
    catalog = load_catalog(catalog_csv)
    midi_files = list(iter_midi_files(input_path, args.recursive))
    if not midi_files:
        fail(f"no MIDI files found in {input_path}")

    print(VERSION_TEXT)
    print(f"[OK] input      : {input_path}")
    print(f"[OK] output     : {output_dir}")
    print(f"[OK] slot maps  : {slot_map_path}")
    print(f"[OK] accents    : {accent_levels_path} ({DEFAULT_ACCENT_SCHEME})")
    print(f"[OK] MIDI files : {len(midi_files)}")
    print(f"[OK] catalog    : {catalog_csv} ({len(catalog)} EXPORT=YES entries)")

    success_count = 0
    failure_count = 0
    for midi_path in midi_files:
        try:
            success, message = convert_one(
                midi_path,
                output_dir,
                maps=maps,
                catalog=catalog,
                args=args,
                accent_levels=accent_levels,
            )
        except SystemExit as exc:
            success, message = False, str(exc).removeprefix("[ERROR] ")
        if success:
            success_count += 1
            print(f"[OK] {message}")
        else:
            failure_count += 1
            print(f"[SKIP] {message}")

    midi_names = {path.stem.upper() for path in midi_files}
    missing_files = sorted(name for name in catalog if name not in midi_names)
    for name in missing_files:
        print(f"[WARN] catalog NAME has no matching MIDI file in input: {name}")

    label = "DRY RUN" if args.dry_run else "DONE"
    print(f"[{label}] converted={success_count}, skipped/errors={failure_count}, missing-midi={len(missing_files)}")
    return 0 if failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
