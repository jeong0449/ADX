#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adc-orn-writer.py 260807c

Create ORN v1.0 sidecar files from a reviewed ADC PatternLab CSV and the
original, unsplit MIDI file. Supports both FLAM grace events and ordinary
off-grid NOTE events that cannot be represented by the selected ADT grid.

The CSV is the catalog authority. Only rows with EXPORT=YES and ORN=YES are
processed. START_BAR..END_BAR selects the pattern range in the original MIDI.
Flam candidates are detected by adc_rhythm_analysis.py; adc_flam.py is not used.

Default output:
    PatternLab CSV + original MIDI -> ./ADP/NAME.ORN

ORN timing uses the ADX canonical PPQN=240 coordinate system. ORN does not
store PPQN because it inherits the tick base of the matching ADP/ADT pattern.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import mido
from mido import Message, MetaMessage, MidiFile

from adc_rhythm_analysis import ADT_DRUM_FAMILIES, detect_flams

SCRIPT_NAME = "adc-orn-writer.py"
VERSION = "260807c"
VERSION_TEXT = f"{SCRIPT_NAME} {VERSION}"
ORN_VERSION_LINE = "; ORN v1.0"
CANONICAL_PPQN = 240
VALID_SUBDIV = {"16", "32", "8T", "16T"}
STEPS_PER_QUARTER = {"16": 4, "32": 8, "8T": 3, "16T": 6}


@dataclass(frozen=True)
class CatalogRow:
    row_number: int
    file: str
    start_bar: int
    end_bar: int
    name: str
    time_sig: str
    subdiv: str
    slot_map: str
    export: bool
    orn: bool
    source: str


@dataclass(frozen=True)
class BarInfo:
    number: int
    start_tick: int
    end_tick: int
    numerator: int
    denominator: int


@dataclass(frozen=True)
class DrumEvent:
    tick: int
    note: int
    velocity: int
    track: int


@dataclass(frozen=True)
class OrnEvent:
    kind: str
    target_step: int
    slot: str
    offset_ticks: int
    velocity: int
    loop_wrap: bool
    confidence: str


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"[ERROR] {message}")


def parse_yes_no(value: str, *, field: str, row_number: int) -> bool:
    normalized = value.strip().upper()
    if normalized == "YES":
        return True
    if normalized == "NO":
        return False
    fail(f"CSV row {row_number}: {field} must be YES or NO, got {value!r}")


def parse_time_signature(value: str, *, row_number: int) -> Tuple[int, int]:
    text = value.strip()
    parts = text.split("/", 1)
    if len(parts) != 2:
        fail(f"CSV row {row_number}: invalid TIME_SIG {value!r}")
    try:
        numerator, denominator = int(parts[0]), int(parts[1])
    except ValueError:
        fail(f"CSV row {row_number}: invalid TIME_SIG {value!r}")
    if numerator < 1 or denominator < 1 or denominator & (denominator - 1):
        fail(f"CSV row {row_number}: invalid TIME_SIG {value!r}")
    return numerator, denominator


def load_catalog(path: Path) -> List[CatalogRow]:
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        fail(f"cannot open CSV {path}: {exc}")

    required = {
        "FILE", "START_BAR", "END_BAR", "NAME", "TIME_SIG", "SLOT_MAP",
        "EXPORT", "SUBDIV", "ORN", "SOURCE",
    }
    rows: List[CatalogRow] = []
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            fail(f"CSV has no header: {path}")
        reader.fieldnames = [str(field).strip().upper() for field in reader.fieldnames]
        missing = sorted(required - set(reader.fieldnames))
        if missing:
            fail(f"CSV missing required column(s): {', '.join(missing)}")

        for row_number, raw in enumerate(reader, start=2):
            row = {str(k).strip().upper(): str(v or "").strip() for k, v in raw.items() if k is not None}
            if not any(row.values()):
                continue
            try:
                start_bar = int(row["START_BAR"])
                end_bar = int(row["END_BAR"])
            except ValueError:
                fail(f"CSV row {row_number}: START_BAR and END_BAR must be integers")
            if start_bar < 1 or end_bar < start_bar:
                fail(f"CSV row {row_number}: invalid bar range {start_bar}-{end_bar}")

            subdiv = row["SUBDIV"].upper()
            if subdiv not in VALID_SUBDIV:
                fail(f"CSV row {row_number}: SUBDIV must be one of 16, 32, 8T, 16T")
            parse_time_signature(row["TIME_SIG"], row_number=row_number)

            rows.append(CatalogRow(
                row_number=row_number,
                file=row["FILE"],
                start_bar=start_bar,
                end_bar=end_bar,
                name=row["NAME"].upper(),
                time_sig=row["TIME_SIG"],
                subdiv=subdiv,
                slot_map=row["SLOT_MAP"].upper(),
                export=parse_yes_no(row["EXPORT"], field="EXPORT", row_number=row_number),
                orn=parse_yes_no(row["ORN"], field="ORN", row_number=row_number),
                source=row["SOURCE"],
            ))
    if not rows:
        fail("CSV contains no pattern rows")
    return rows


def merged_absolute_messages(mid: MidiFile) -> List[Tuple[int, int, Message | MetaMessage]]:
    merged = mido.merge_tracks(mid.tracks) if mid.type == 1 else mid.tracks[0]
    out: List[Tuple[int, int, Message | MetaMessage]] = []
    tick = 0
    for order, msg in enumerate(merged):
        tick += msg.time
        out.append((tick, order, msg.copy(time=0)))
    return out


def collect_time_signatures(messages: Iterable[Tuple[int, int, Message | MetaMessage]]) -> List[Tuple[int, int, int]]:
    values: Dict[int, Tuple[int, int]] = {0: (4, 4)}
    for tick, _order, msg in messages:
        if isinstance(msg, MetaMessage) and msg.type == "time_signature":
            values[tick] = (int(msg.numerator), int(msg.denominator))
    return [(tick, num, den) for tick, (num, den) in sorted(values.items())]


def build_bar_map(tpq: int, time_signatures: Sequence[Tuple[int, int, int]], total_tick: int) -> List[BarInfo]:
    if tpq <= 0:
        fail(f"invalid ticks_per_beat: {tpq}")
    bars: List[BarInfo] = []
    ts_index = 0
    tick = 0
    number = 1
    while tick < total_tick:
        while ts_index + 1 < len(time_signatures) and time_signatures[ts_index + 1][0] <= tick:
            ts_index += 1
        _, numerator, denominator = time_signatures[ts_index]
        end_tick = tick + max(1, round(tpq * numerator * 4 / denominator))
        if ts_index + 1 < len(time_signatures):
            next_change = time_signatures[ts_index + 1][0]
            if tick < next_change < end_tick:
                end_tick = next_change
        bars.append(BarInfo(number, tick, end_tick, numerator, denominator))
        tick = end_tick
        number += 1
    return bars


def collect_pattern_events(
    messages: Sequence[Tuple[int, int, Message | MetaMessage]],
    start_tick: int,
    end_tick: int,
) -> List[dict]:
    events: List[dict] = []
    for tick, order, msg in messages:
        if tick < start_tick:
            continue
        if tick >= end_tick:
            break
        if (
            isinstance(msg, Message)
            and msg.type == "note_on"
            and msg.velocity > 0
            and getattr(msg, "channel", -1) == 9
        ):
            events.append({
                "tick": tick - start_tick,
                "note": int(msg.note),
                "velocity": int(msg.velocity),
                "track": 0,
                "order": order,
            })
    return events


def canonical_tick(tick: int, source_ppqn: int) -> int:
    return int(round(tick * CANONICAL_PPQN / source_ppqn))


def pattern_length_steps(row: CatalogRow, bars: Sequence[BarInfo]) -> int:
    selected = bars[row.start_bar - 1:row.end_bar]
    quarter_notes = sum(bar.numerator * 4 / bar.denominator for bar in selected)
    value = quarter_notes * STEPS_PER_QUARTER[row.subdiv]
    rounded = round(value)
    if not math.isclose(value, rounded, abs_tol=1e-9):
        fail(f"CSV row {row.row_number}: selected meter and SUBDIV do not produce an integer LENGTH")
    return int(rounded)


def build_orn_events(
    row: CatalogRow,
    bars: Sequence[BarInfo],
    messages: Sequence[Tuple[int, int, Message | MetaMessage]],
    source_ppqn: int,
) -> Tuple[int, int, List[OrnEvent]]:
    """Build ORN events from reviewed pattern data.

    Two event classes are emitted:
      * FLAM: removable grace notes identified by detect_flams()
      * NOTE: ordinary note-ons that do not lie exactly on the selected ADT grid

    ADX never quantizes the time axis.  ADT/ADP contains only exact on-grid
    note-ons; ORN restores the off-grid performance events at their original
    timing by storing a signed tick offset from a reference grid step.
    """
    start_tick = bars[row.start_bar - 1].start_tick
    end_tick = bars[row.end_bar - 1].end_tick
    source_loop_ticks = end_tick - start_tick
    events = collect_pattern_events(messages, start_tick, end_tick)
    if not events:
        fail(f"CSV row {row.row_number}: no CH10 note_on events in bars {row.start_bar}-{row.end_bar}")

    analysis = detect_flams(events, source_ppqn, loop_ticks=source_loop_ticks, loop_start=0)
    removable_grace_keys = {
        (int(item["grace_tick"]), int(item["grace_note"]))
        for item in analysis["flams"]
        if item.get("remove_from_subdivision")
    }

    length = pattern_length_steps(row, bars)
    steps_per_quarter = STEPS_PER_QUARTER[row.subdiv]
    step_ticks = CANONICAL_PPQN / steps_per_quarter
    loop_ticks = int(round(length * step_ticks))
    orn_events: List[OrnEvent] = []

    # 1) Preserve confirmed flam/grace notes using the existing FLAM event type.
    for item in analysis["flams"]:
        if not item.get("remove_from_subdivision"):
            continue
        grace_tick = canonical_tick(int(item["grace_tick"]), source_ppqn)
        main_tick = canonical_tick(int(item["main_tick"]), source_ppqn)
        loop_wrap = bool(item.get("across_loop"))

        if loop_wrap:
            target_step = 0
            offset_ticks = grace_tick - loop_ticks
        else:
            target_step = int(round(main_tick / step_ticks))
            if target_step >= length:
                target_step = 0
                loop_wrap = True
            target_grid_tick = int(round(target_step * step_ticks))
            offset_ticks = grace_tick - target_grid_tick

        orn_events.append(OrnEvent(
            kind="FLAM",
            target_step=target_step,
            slot=str(item["family"]),
            offset_ticks=offset_ticks,
            velocity=int(item["grace_velocity"]),
            loop_wrap=loop_wrap,
            confidence=str(item["confidence"]),
        ))

    # 2) Preserve ordinary off-grid notes that ADT/ADP cannot encode.
    #    Grid membership is an exact test: no note-on is ever snapped in time.
    #    The nearest regular step is used only as an ORN reference point.
    for event in events:
        source_tick = int(event["tick"])
        note = int(event["note"])
        if (source_tick, note) in removable_grace_keys:
            # Already represented above as FLAM; avoid a duplicate NOTE event.
            continue

        tick = canonical_tick(source_tick, source_ppqn)
        step_pos = tick / step_ticks
        nearest_step = int(round(step_pos))

        # Exact on-grid hits belong in ADT/ADP, not ORN.
        if math.isclose(step_pos, nearest_step, abs_tol=1e-9):
            continue

        loop_wrap = False
        if nearest_step >= length:
            target_step = 0
            target_grid_tick = loop_ticks
            loop_wrap = True
        else:
            target_step = nearest_step
            target_grid_tick = int(round(target_step * step_ticks))

        offset_ticks = tick - target_grid_tick
        slot = ADT_DRUM_FAMILIES.get(note, f"N{note}")
        orn_events.append(OrnEvent(
            kind="NOTE",
            target_step=target_step,
            slot=slot,
            offset_ticks=offset_ticks,
            velocity=int(event["velocity"]),
            loop_wrap=loop_wrap,
            confidence="EXACT",
        ))

    orn_events.sort(key=lambda event: (event.target_step, event.slot, event.offset_ticks, event.velocity, event.kind))
    return length, loop_ticks, orn_events

def render_orn(row: CatalogRow, length: int, loop_ticks: int, events: Sequence[OrnEvent]) -> str:
    lines = [
        ORN_VERSION_LINE,
        f"; NAME={row.name}",
        f"; SOURCE={row.source}",
        "UNIT=TICK",
        f"SUBDIV={row.subdiv}",
        f"LENGTH={length}",
        f"LOOP_TICKS={loop_ticks}",
        "",
        "[EVENTS]",
    ]
    for event in events:
        line = (
            f"{event.kind} TARGET_STEP={event.target_step} SLOT={event.slot} "
            f"OFFSET_TICKS={event.offset_ticks} VELOCITY={event.velocity}"
        )
        if event.loop_wrap:
            line += " LOOP_WRAP=1"
        line += f" ; confidence={event.confidence}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description="Create ORN sidecars from a PatternLab CSV and the original unsplit MIDI file.",
    )
    parser.add_argument("catalog_csv", type=Path, help="Reviewed PatternLab CSV")
    parser.add_argument("source_midi", type=Path, help="Original unsplit MIDI file")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("ADP"),
        help="ORN output directory (default: ./ADP)",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing ORN files")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the plan without writing files")
    parser.add_argument("--version", action="version", version=VERSION_TEXT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not args.catalog_csv.is_file():
        fail(f"CSV not found: {args.catalog_csv}")
    if not args.source_midi.is_file():
        fail(f"MIDI not found: {args.source_midi}")

    rows = load_catalog(args.catalog_csv)
    selected = [row for row in rows if row.export and row.orn]
    if not selected:
        fail("CSV has no rows with EXPORT=YES and ORN=YES")

    try:
        mid = MidiFile(str(args.source_midi))
    except Exception as exc:
        fail(f"cannot read MIDI {args.source_midi}: {exc}")
    if mid.type not in (0, 1):
        fail(f"only SMF Type 0 or 1 is supported, got Type {mid.type}")

    messages = merged_absolute_messages(mid)
    total_tick = max((tick for tick, _order, _msg in messages), default=0)
    bars = build_bar_map(mid.ticks_per_beat, collect_time_signatures(messages), total_tick)
    expected_file = args.source_midi.name.casefold()

    print(VERSION_TEXT)
    print(f"[OK] CSV        : {args.catalog_csv}")
    print(f"[OK] source MIDI: {args.source_midi}")
    print(f"[OK] output     : {args.out_dir}")
    print(f"[OK] TPQ        : {mid.ticks_per_beat}")
    print(f"[OK] bars       : {len(bars)}")
    print(f"[OK] ORN rows   : {len(selected)}")

    success = 0
    failures = 0
    planned_paths: set[Path] = set()
    for row in selected:
        try:
            if Path(row.file).name.casefold() != expected_file:
                raise ValueError(f"FILE={row.file!r} does not match source MIDI {args.source_midi.name!r}")
            if row.end_bar > len(bars):
                raise ValueError(f"bar range {row.start_bar}-{row.end_bar} exceeds MIDI bar count {len(bars)}")
            meters = {(bar.numerator, bar.denominator) for bar in bars[row.start_bar - 1:row.end_bar]}
            expected_meter = parse_time_signature(row.time_sig, row_number=row.row_number)
            if meters != {expected_meter}:
                actual = "→".join(f"{bar.numerator}/{bar.denominator}" for bar in bars[row.start_bar - 1:row.end_bar])
                raise ValueError(f"TIME_SIG={row.time_sig} does not match selected bars ({actual})")

            length, loop_ticks, orn_events = build_orn_events(row, bars, messages, mid.ticks_per_beat)
            output_path = args.out_dir / f"{row.name}.ORN"
            if output_path in planned_paths:
                raise ValueError(f"duplicate output NAME {row.name}")
            planned_paths.add(output_path)
            if not orn_events:
                raise ValueError("ORN=YES but no FLAM or off-grid NOTE event was found")
            if output_path.exists() and not args.overwrite:
                raise ValueError(f"exists: {output_path} (use --overwrite)")

            if args.dry_run:
                print(
                    f"[PLAN] {row.name}.ORN <- bars {row.start_bar}-{row.end_bar}; "
                    f"SUBDIV={row.subdiv}, LENGTH={length}, events={len(orn_events)}"
                )
            else:
                args.out_dir.mkdir(parents=True, exist_ok=True)
                output_path.write_text(render_orn(row, length, loop_ticks, orn_events), encoding="utf-8")
                print(
                    f"[ORN] {output_path} <- bars {row.start_bar}-{row.end_bar}; "
                    f"SUBDIV={row.subdiv}, LENGTH={length}, events={len(orn_events)}"
                )
            success += 1
        except (OSError, ValueError, SystemExit) as exc:
            failures += 1
            message = str(exc).removeprefix("[ERROR] ")
            print(f"[SKIP] CSV row {row.row_number} {row.name}: {message}")

    label = "DRY RUN" if args.dry_run else "DONE"
    print(f"[{label}] created/planned={success}, skipped/errors={failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
