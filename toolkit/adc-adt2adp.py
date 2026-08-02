#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adc-adt2adp.py 260801c

Convert ADT v2.3 drum-pattern text files into ADP v2.3 binary cache files.

Default workflow
----------------
    ./ADT/*.ADT  ->  ./ADP/*.ADP

Inputs
------
1. ADT v2.3 file(s)
2. slot_map_definitions.json (default: beside this script)

ADP v2.3 header (12 bytes, little-endian)
------------------------------------------------
Offset  Size  Field
0x00    4     Magic: b"ADP3"
0x04    1     Version: 23
0x05    1     SUBDIV code: 0=16, 1=8T, 2=16T
0x06    1     LENGTH in steps
0x07    1     SLOT_MAP_ID (0..254 registered, 255=INLINE)
0x08    2     Payload byte count
0x0A    2     Payload CRC16-CCITT

Payload encoding is unchanged from ADP v2.2:
    for each step:
        u8 hit_count
        hit_count * u8 packed_hit

    packed_hit = (slot_index << 2) | accent
    slot_index: 0..15
    accent: 1..3 for stored hits (0 is rest and is omitted)

INLINE policy
-------------
If SLOT_MAP_ID=INLINE, the generated ADP stores slot-map ID 255 and the source
ADT is copied beside the ADP using the same basename. The companion ADT is
therefore available to a player that needs the inline SLOT definitions.

Dependencies
------------
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

SCRIPT_NAME = "adc-adt2adp.py"
VERSION = "260801c"
VERSION_TEXT = f"{SCRIPT_NAME} {VERSION}"

ADT_VERSION_LINE = "; ADT v2.3"
ADP_MAGIC = b"ADP3"
ADP_VERSION = 23
INLINE_SLOT_MAP_ID = 255
DEFAULT_SLOT_MAP = "LEGACY"
DEFAULT_ORIENTATION = "STEP"

SUBDIV_CODE = {"16": 0, "8T": 1, "16T": 2}
BODY_OK = {".", "-", "x", "X", "o", "O", "^"}
NAME_RE = re.compile(r"^[A-Z0-9]{3}_[0-9]{4}$")
SLOT_KEY_RE = re.compile(r"^SLOT([0-9]+)$")


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


@dataclass(frozen=True)
class ParsedADT:
    name: str
    subdiv: str
    length: int
    slot_map_name: str
    slot_map_id: int
    orientation: str
    slots: Tuple[SlotDefinition, ...]
    grid: Tuple[Tuple[int, ...], ...]  # STEP-major, accent levels 0..3


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"[ERROR] {message}")


def crc16_ccitt(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def accent_from_char(ch: str) -> int:
    c = ch.lower()
    if c == ".":
        return 0
    if c == "-":
        return 1
    if c == "x":
        return 2
    if c in {"o", "^"}:
        return 3
    raise ValueError(f"invalid ADT data symbol: {ch!r}")


def load_slot_maps(path: Path) -> Dict[str, SlotMapDefinition]:
    try:
        raw_root = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"slot-map definition not found: {path}")
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read slot-map definition {path}: {exc}")

    if not isinstance(raw_root, list) or not raw_root:
        fail("slot-map JSON root must be a non-empty array")

    maps: Dict[str, SlotMapDefinition] = {}
    seen_ids: Set[int] = set()

    for raw_map in raw_root:
        if not isinstance(raw_map, dict):
            fail("each slot-map entry must be an object")

        map_id = raw_map.get("slot_map_id")
        name = raw_map.get("name")
        raw_slots = raw_map.get("slots")

        if not isinstance(map_id, int) or not (0 <= map_id <= 254):
            fail(f"registered slot_map_id must be an integer in 0..254: {map_id!r}")
        if map_id in seen_ids:
            fail(f"duplicate slot_map_id: {map_id}")
        if not isinstance(name, str) or not name.strip():
            fail(f"invalid slot-map name: {name!r}")
        name = name.strip().upper()
        if name == "INLINE":
            fail("INLINE is reserved and must not appear as a registered slot-map name")
        if name in maps:
            fail(f"duplicate slot-map name: {name}")
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

            if not isinstance(index, int) or not (0 <= index <= 15):
                fail(f"slot map {name}: slot index must be in 0..15: {index!r}")
            if index in seen_indices:
                fail(f"slot map {name}: duplicate slot index {index}")
            if not isinstance(abbrev, str) or not abbrev.strip():
                fail(f"slot map {name}, slot {index}: missing abbrev")
            if not isinstance(extended, str) or not extended.strip():
                fail(f"slot map {name}, slot {index}: missing extended name")
            if not isinstance(representative, int) or not (0 <= representative <= 127):
                fail(f"slot map {name}, slot {index}: invalid representative_midi")
            if not isinstance(allowed, list) or not allowed or any(
                not isinstance(note, int) or not (0 <= note <= 127) for note in allowed
            ):
                fail(f"slot map {name}, slot {index}: invalid midi_input_allowed")
            if representative not in allowed:
                fail(f"slot map {name}, slot {index}: representative_midi must be allowed")

            seen_indices.add(index)
            slots.append(
                SlotDefinition(
                    index=index,
                    abbrev=abbrev.strip().upper(),
                    extended=extended.strip(),
                    representative_midi=representative,
                    allowed_notes=tuple(int(note) for note in allowed),
                )
            )

        slots.sort(key=lambda slot: slot.index)
        if [slot.index for slot in slots] != list(range(len(slots))):
            fail(f"slot map {name}: slot indices must be contiguous 0..{len(slots)-1}")
        if len(slots) > 16:
            fail(f"slot map {name}: ADP packed-hit format supports at most 16 slots")

        seen_ids.add(map_id)
        maps[name] = SlotMapDefinition(map_id, name, tuple(slots))

    if DEFAULT_SLOT_MAP not in maps:
        fail(f"default slot map {DEFAULT_SLOT_MAP!r} is not defined in {path}")

    return maps


def parse_inline_slot(value: str, index: int) -> SlotDefinition:
    # SLOTn=ABBR@NOTE,EXTENDED
    match = re.fullmatch(r"\s*([^@,\s]+)\s*@\s*([0-9]{1,3})\s*(?:,\s*(.+?)\s*)?", value)
    if not match:
        raise ValueError(f"invalid SLOT{index} definition: {value!r}")

    abbrev = match.group(1).upper()
    note = int(match.group(2))
    extended = (match.group(3) or abbrev).strip()
    if not (0 <= note <= 127):
        raise ValueError(f"SLOT{index} MIDI note must be in 0..127")

    return SlotDefinition(index, abbrev, extended, note, (note,))


def parse_adt(path: Path, slot_maps: Dict[str, SlotMapDefinition]) -> ParsedADT:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"cannot read ADT: {exc}") from exc

    raw_lines = text.splitlines()
    if not raw_lines:
        raise ValueError("empty ADT file")
    if raw_lines[0].strip() != ADT_VERSION_LINE:
        raise ValueError(f"first line must be exactly {ADT_VERSION_LINE!r}")

    metadata: Dict[str, str] = {}
    inline_slots_raw: Dict[int, str] = {}
    data_lines: List[str] = []
    in_data = False

    for line_number, raw in enumerate(raw_lines[1:], start=2):
        # Semicolon begins an inline or full-line comment.
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue

        if not in_data:
            if line.upper() == "[DATA]":
                in_data = True
                continue

            match = re.fullmatch(r"([A-Za-z0-9_]+)\s*=\s*(.*)", line)
            if not match:
                raise ValueError(f"line {line_number}: expected FIELD=VALUE or [DATA]")

            key = match.group(1).upper()
            value = match.group(2).strip()
            slot_match = SLOT_KEY_RE.fullmatch(key)
            if slot_match:
                slot_index = int(slot_match.group(1))
                if slot_index in inline_slots_raw:
                    raise ValueError(f"line {line_number}: duplicate SLOT{slot_index}")
                inline_slots_raw[slot_index] = value
            else:
                if key in metadata:
                    raise ValueError(f"line {line_number}: duplicate field {key}")
                metadata[key] = value
        else:
            normalized = "".join(ch for ch in line if not ch.isspace())
            if not normalized:
                continue
            invalid = sorted(set(normalized) - BODY_OK)
            if invalid:
                raise ValueError(f"line {line_number}: invalid data symbol(s) {invalid}")
            data_lines.append(normalized)

    if not in_data:
        raise ValueError("missing [DATA] marker")

    name = metadata.get("NAME", path.stem).strip().upper()
    if not NAME_RE.fullmatch(name):
        raise ValueError(f"NAME must match ABC_0001, got {name!r}")
    if path.stem.upper() != name:
        raise ValueError(f"ADT filename stem {path.stem!r} does not match NAME={name}")

    subdiv = metadata.get("SUBDIV", "").strip().upper()
    if subdiv not in SUBDIV_CODE:
        raise ValueError(f"SUBDIV must be one of 16, 8T, 16T, got {subdiv!r}")

    try:
        length = int(metadata.get("LENGTH", ""))
    except ValueError as exc:
        raise ValueError("LENGTH must be an integer") from exc
    if not (1 <= length <= 255):
        raise ValueError(f"LENGTH must be in 1..255, got {length}")

    orientation = metadata.get("ORIENTATION", DEFAULT_ORIENTATION).strip().upper()
    if orientation not in {"STEP", "SLOT"}:
        raise ValueError(f"ORIENTATION must be STEP or SLOT, got {orientation!r}")

    slot_map_name = metadata.get("SLOT_MAP_ID", DEFAULT_SLOT_MAP).strip().upper()
    if slot_map_name == "INLINE":
        if not inline_slots_raw:
            raise ValueError("SLOT_MAP_ID=INLINE requires SLOT0...SLOTn definitions")
        indices = sorted(inline_slots_raw)
        if indices != list(range(len(indices))):
            raise ValueError(f"INLINE slot indices must be contiguous 0..{len(indices)-1}")
        if len(indices) > 16:
            raise ValueError("ADP packed-hit format supports at most 16 INLINE slots")
        slots = tuple(parse_inline_slot(inline_slots_raw[index], index) for index in indices)
        slot_map_id = INLINE_SLOT_MAP_ID
    else:
        if inline_slots_raw:
            raise ValueError("SLOT definitions are allowed only with SLOT_MAP_ID=INLINE")
        if slot_map_name not in slot_maps:
            raise ValueError(f"unknown SLOT_MAP_ID {slot_map_name!r}")
        slot_map = slot_maps[slot_map_name]
        slots = slot_map.slots
        slot_map_id = slot_map.map_id

    slot_count = len(slots)
    if orientation == "STEP":
        if len(data_lines) != length:
            raise ValueError(f"STEP data line count must equal LENGTH={length}, got {len(data_lines)}")
        for row_index, row in enumerate(data_lines, start=1):
            if len(row) != slot_count:
                raise ValueError(
                    f"STEP data row {row_index} length must equal slot count {slot_count}, got {len(row)}"
                )
        grid = tuple(tuple(accent_from_char(ch) for ch in row) for row in data_lines)
    else:
        if len(data_lines) != slot_count:
            raise ValueError(f"SLOT data line count must equal slot count {slot_count}, got {len(data_lines)}")
        for slot_index, row in enumerate(data_lines):
            if len(row) != length:
                raise ValueError(
                    f"SLOT data row {slot_index} length must equal LENGTH={length}, got {len(row)}"
                )
        grid = tuple(
            tuple(accent_from_char(data_lines[slot_index][step]) for slot_index in range(slot_count))
            for step in range(length)
        )

    return ParsedADT(
        name=name,
        subdiv=subdiv,
        length=length,
        slot_map_name=slot_map_name,
        slot_map_id=slot_map_id,
        orientation=orientation,
        slots=slots,
        grid=grid,
    )



def encode_payload(parsed: ParsedADT) -> bytes:
    payload = bytearray()
    slot_count = len(parsed.slots)

    for step_index, row in enumerate(parsed.grid):
        if len(row) != slot_count:
            raise ValueError(f"internal grid error at step {step_index}")

        hits: List[int] = []
        for slot_index, accent in enumerate(row):
            if accent == 0:
                continue
            if not (0 <= slot_index <= 15):
                raise ValueError(f"slot index out of packed range: {slot_index}")
            if not (1 <= accent <= 3):
                raise ValueError(f"accent out of range at step {step_index}, slot {slot_index}: {accent}")
            hits.append((slot_index << 2) | accent)

        if len(hits) > 255:
            raise ValueError(f"too many hits at step {step_index}: {len(hits)}")
        payload.append(len(hits))
        payload.extend(hits)

    if len(payload) > 0xFFFF:
        raise ValueError(f"payload too large for 16-bit header field: {len(payload)} bytes")
    return bytes(payload)


def encode_adp(parsed: ParsedADT) -> bytes:
    payload = encode_payload(parsed)
    payload_crc = crc16_ccitt(payload)
    header = struct.pack(
        "<4sBBBBHH",
        ADP_MAGIC,
        ADP_VERSION,
        SUBDIV_CODE[parsed.subdiv],
        parsed.length,
        parsed.slot_map_id,
        len(payload),
        payload_crc,
    )
    if len(header) != 12:
        raise AssertionError(f"internal error: ADP v2.3 header is {len(header)} bytes, expected 12")
    return header + payload


def iter_adt_files(path: Path, recursive: bool) -> Iterable[Path]:
    if path.is_file():
        if path.suffix.lower() == ".adt":
            yield path
        return

    iterator = path.rglob("*") if recursive else path.glob("*")
    files = [item for item in iterator if item.is_file() and item.suffix.lower() == ".adt"]
    yield from sorted(files, key=lambda item: str(item).casefold())


def convert_one(
    input_path: Path,
    output_dir: Path,
    slot_maps: Dict[str, SlotMapDefinition],
    *,
    overwrite: bool,
    dry_run: bool,
) -> Tuple[bool, str]:
    try:
        parsed = parse_adt(input_path, slot_maps)
        blob = encode_adp(parsed)
    except (OSError, ValueError, struct.error) as exc:
        return False, f"{input_path.name}: {exc}"

    output_path = output_dir / f"{parsed.name}.ADP"
    companion_path = output_dir / f"{parsed.name}.ADT"

    needs_companion_adt = parsed.slot_map_id == INLINE_SLOT_MAP_ID

    conflicts = [output_path]
    if needs_companion_adt:
        conflicts.append(companion_path)
    existing = [path for path in conflicts if path.exists()]
    if existing and not overwrite:
        return False, f"exists: {existing[0]} (use --overwrite)"

    payload_bytes = len(blob) - 12
    payload_crc = struct.unpack_from("<H", blob, 10)[0]
    detail = (
        f"{input_path.name} -> {output_path.name} "
        f"(SUBDIV={parsed.subdiv}, LENGTH={parsed.length}, "
        f"SLOT_MAP={parsed.slot_map_name}:{parsed.slot_map_id}, "
        f"payload={payload_bytes} bytes, CRC16=0x{payload_crc:04X})"
    )

    if dry_run:
        if needs_companion_adt:
            detail += f"; copy companion -> {companion_path.name}"
        return True, "plan: " + detail

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(blob)
    if needs_companion_adt:
        shutil.copy2(input_path, companion_path)

    return True, detail


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description="Convert ADT v2.3 patterns to ADP v2.3 binary cache files.",
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=Path("ADT"),
        help="ADT file or directory (default: ./ADT)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: ./ADP for directory/default mode; input directory for single-file mode)",
    )
    parser.add_argument(
        "--slot-maps",
        type=Path,
        default=None,
        help="slot_map_definitions.json (default: beside this script)",
    )
    parser.add_argument("--recursive", action="store_true", help="Process ADT files in subdirectories")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing ADP and INLINE companion ADT files")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the conversion plan without writing files")
    parser.add_argument("--version", action="version", version=VERSION_TEXT)
    return parser.parse_args(argv)


def resolve_output_dir(input_path: Path, explicit: Optional[Path]) -> Path:
    if explicit is not None:
        return explicit
    if input_path.is_file():
        return input_path.parent
    return input_path.parent / "ADP"


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    input_path: Path = args.input
    if not input_path.exists():
        fail(f"input not found: {input_path}")

    slot_map_path = args.slot_maps or Path(__file__).with_name("slot_map_definitions.json")
    slot_maps = load_slot_maps(slot_map_path)

    output_dir = resolve_output_dir(input_path, args.out_dir)
    adt_files = list(iter_adt_files(input_path, args.recursive))
    if not adt_files:
        fail(f"no ADT files found in {input_path}")

    print(VERSION_TEXT)
    print(f"[OK] input      : {input_path}")
    print(f"[OK] output     : {output_dir}")
    print(f"[OK] slot maps  : {slot_map_path}")
    print(f"[OK] ADT files  : {len(adt_files)}")

    success_count = 0
    failure_count = 0
    for adt_path in adt_files:
        success, message = convert_one(
            adt_path,
            output_dir,
            slot_maps,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
        if success:
            success_count += 1
            print(f"[OK] {message}")
        else:
            failure_count += 1
            print(f"[SKIP] {message}")

    label = "DRY RUN" if args.dry_run else "DONE"
    print(f"[{label}] converted={success_count}, skipped/errors={failure_count}")
    return 0 if failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
