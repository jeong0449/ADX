#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adx-drum-player-win 260806a — Windows FluidSynth CLI player for ADX and MIDI files.

Supports:
- ADT v2.3 text patterns
- ADP v2.3 12-byte binary caches (ADP3)
- Legacy ADP v2.2 caches (ADP2)
- Optional same-basename ORN sidecars
- Registered slot maps from slot_map_definitions.json
- ADP3 SLOT_MAP_ID=255 (INLINE) via a same-basename companion ADT

ADT/ADP/ORN are rendered to a temporary Standard MIDI File and played through FluidSynth.
Standard MIDI Files are passed directly to FluidSynth.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

try:
    import mido
except ImportError:
    mido = None

SCRIPT_NAME = "adx-drum-player-win.py"
VERSION = "260806a"
VERSION_TEXT = f"{SCRIPT_NAME} {VERSION}"

ADT_VERSION_LINE = "; ADT v2.3"
DEFAULT_SLOT_MAP = "LEGACY"
DEFAULT_ORIENTATION = "STEP"
DEFAULT_PPQN = 240
INLINE_SLOT_MAP_ID = 255

SUBDIV_CODE_TO_STR = {0: "16", 1: "32", 2: "8T", 3: "16T"}
VALID_SUBDIV = set(SUBDIV_CODE_TO_STR.values())
BODY_OK: Set[str] = set()
SLOT_KEY_RE = re.compile(r"^SLOT([0-9]+)$")

ADP3_HEADER_FMT = "<4sBBBBHH"
ADP3_HEADER_SIZE = struct.calcsize(ADP3_HEADER_FMT)  # 12
ADP2_HEADER_FMT = "<4sBBBBH B H B H I"
ADP2_HEADER_SIZE = struct.calcsize(ADP2_HEADER_FMT)  # 20


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


@dataclass
class Pattern:
    name: str
    source_format: str
    length: int
    slots: int
    grid_type: str
    steps: List[List[int]]
    slot_notes: List[int]
    slot_abbr: List[str]
    slot_full_names: List[str]
    slot_map_name: str
    time_sig: Optional[str] = None
    tempo: Optional[int] = None
    ppqn: int = DEFAULT_PPQN
    legacy_ppqn: Optional[int] = None


def crc16_ccitt(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def load_accent_scheme(path: Path) -> Tuple[Dict[str, int], Dict[int, int]]:
    """Load ADT symbols and representative velocities from the 6-accent scheme."""
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Accent-level definition not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read accent-level definition {path}: {exc}") from exc

    schemes = root.get("schemes") if isinstance(root, dict) else None
    scheme = schemes.get("6-accent") if isinstance(schemes, dict) else None
    levels = scheme.get("levels") if isinstance(scheme, dict) else None
    if not isinstance(levels, list) or len(levels) != 6:
        raise ValueError(f"{path}: 6-accent must define exactly six levels including Rest")

    symbol_to_level: Dict[str, int] = {}
    velocities: Dict[int, int] = {}
    expected_min = 0
    for position, level in enumerate(levels):
        if not isinstance(level, dict):
            raise ValueError(f"{path}: 6-accent level {position} must be an object")
        index = level.get("index")
        symbol = level.get("symbol")
        lo = level.get("min_velocity")
        hi = level.get("max_velocity")
        rep = level.get("representative_velocity")
        if index != position:
            raise ValueError(f"{path}: level {position} index must equal its array position")
        if not isinstance(symbol, str) or len(symbol) != 1 or symbol in symbol_to_level:
            raise ValueError(f"{path}: invalid or duplicate symbol at level {position}")
        if not all(isinstance(v, int) for v in (lo, hi, rep)):
            raise ValueError(f"{path}: level {position} velocity values must be integers")
        if lo != expected_min or not 0 <= lo <= hi <= 127:
            raise ValueError(f"{path}: velocity ranges must be contiguous and cover 0..127")
        if not lo <= rep <= hi:
            raise ValueError(f"{path}: representative velocity is outside level {position} range")
        if position == 0 and not (symbol == "." and lo == hi == rep == 0):
            raise ValueError(f"{path}: level 0 must be Rest with symbol '.' and velocity 0")
        symbol_to_level[symbol] = index
        velocities[index] = rep
        expected_min = hi + 1

    if expected_min != 128 or set(velocities) != set(range(6)):
        raise ValueError(f"{path}: 6-accent levels must cover indices 0..5 and velocity 0..127")
    return symbol_to_level, velocities


def accent_from_char(ch: str, symbol_to_level: Dict[str, int]) -> int:
    try:
        return symbol_to_level[ch]
    except KeyError as exc:
        raise ValueError(f"Invalid ADT data symbol: {ch!r}") from exc


def load_slot_maps(path: Path) -> Tuple[Dict[str, SlotMapDefinition], Dict[int, SlotMapDefinition]]:
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Slot-map definition not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read slot-map definition {path}: {exc}") from exc

    if not isinstance(root, list) or not root:
        raise ValueError("Slot-map JSON root must be a non-empty array")

    by_name: Dict[str, SlotMapDefinition] = {}
    by_id: Dict[int, SlotMapDefinition] = {}
    for raw_map in root:
        if not isinstance(raw_map, dict):
            raise ValueError("Each slot-map entry must be an object")
        map_id = raw_map.get("slot_map_id")
        name = raw_map.get("name")
        raw_slots = raw_map.get("slots")
        if not isinstance(map_id, int) or not 0 <= map_id <= 254 or map_id in by_id:
            raise ValueError(f"Invalid or duplicate slot_map_id: {map_id!r}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid slot-map name: {name!r}")
        name = name.strip().upper()
        if name == "INLINE" or name in by_name:
            raise ValueError(f"Reserved or duplicate slot-map name: {name}")
        if not isinstance(raw_slots, list) or not raw_slots:
            raise ValueError(f"Slot map {name}: slots must be a non-empty list")

        slots: List[SlotDefinition] = []
        seen: Set[int] = set()
        for raw_slot in raw_slots:
            if not isinstance(raw_slot, dict):
                raise ValueError(f"Slot map {name}: every slot must be an object")
            index = raw_slot.get("slot")
            abbrev = raw_slot.get("abbrev")
            extended = raw_slot.get("extended", abbrev)
            representative = raw_slot.get("representative_midi")
            allowed = raw_slot.get("midi_input_allowed")
            if not isinstance(index, int) or not 0 <= index <= 15 or index in seen:
                raise ValueError(f"Slot map {name}: invalid or duplicate slot index {index!r}")
            if not isinstance(abbrev, str) or not abbrev.strip():
                raise ValueError(f"Slot map {name}, slot {index}: missing abbrev")
            if not isinstance(extended, str) or not extended.strip():
                raise ValueError(f"Slot map {name}, slot {index}: missing extended name")
            if not isinstance(representative, int) or not 0 <= representative <= 127:
                raise ValueError(f"Slot map {name}, slot {index}: invalid representative_midi")
            if not isinstance(allowed, list) or not allowed or any(
                not isinstance(note, int) or not 0 <= note <= 127 for note in allowed
            ):
                raise ValueError(f"Slot map {name}, slot {index}: invalid midi_input_allowed")
            if representative not in allowed:
                raise ValueError(f"Slot map {name}, slot {index}: representative_midi must be allowed")
            seen.add(index)
            slots.append(SlotDefinition(index, abbrev.strip().upper(), extended.strip(), representative, tuple(allowed)))

        slots.sort(key=lambda item: item.index)
        if [slot.index for slot in slots] != list(range(len(slots))):
            raise ValueError(f"Slot map {name}: slot indices must be contiguous")
        slot_map = SlotMapDefinition(map_id, name, tuple(slots))
        by_name[name] = slot_map
        by_id[map_id] = slot_map

    if DEFAULT_SLOT_MAP not in by_name:
        raise ValueError(f"Default slot map {DEFAULT_SLOT_MAP!r} is absent from {path}")
    return by_name, by_id


def parse_inline_slot(value: str, index: int) -> SlotDefinition:
    match = re.fullmatch(r"\s*([^@,\s]+)\s*@\s*([0-9]{1,3})\s*(?:,\s*(.+?)\s*)?", value)
    if not match:
        raise ValueError(f"Invalid SLOT{index} definition: {value!r}")
    note = int(match.group(2))
    if not 0 <= note <= 127:
        raise ValueError(f"SLOT{index} MIDI note must be in 0..127")
    abbrev = match.group(1).upper()
    return SlotDefinition(index, abbrev, (match.group(3) or abbrev).strip(), note, (note,))


def parse_adt_v23(path: Path, slot_maps_by_name: Dict[str, SlotMapDefinition], symbol_to_level: Dict[str, int]) -> Pattern:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"Cannot read ADT {path}: {exc}") from exc
    body_ok = set(symbol_to_level)
    raw_lines = text.splitlines()
    if not raw_lines or raw_lines[0].strip() != ADT_VERSION_LINE:
        raise ValueError(f"First line must be exactly {ADT_VERSION_LINE!r}")

    metadata: Dict[str, str] = {}
    inline_raw: Dict[int, str] = {}
    data_lines: List[str] = []
    in_data = False
    for line_no, raw in enumerate(raw_lines[1:], start=2):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.upper() == "[DATA]":
            if in_data:
                raise ValueError(f"{path.name}:{line_no}: duplicate [DATA]")
            in_data = True
            continue
        if in_data:
            compact = "".join(ch for ch in line if not ch.isspace())
            if any(ch not in body_ok for ch in compact):
                raise ValueError(f"{path.name}:{line_no}: invalid pattern data")
            data_lines.append(compact)
            continue
        if "=" not in line:
            raise ValueError(f"{path.name}:{line_no}: expected FIELD=VALUE or [DATA]")
        key, value = line.split("=", 1)
        key = key.strip().upper()
        value = value.strip()
        slot_match = SLOT_KEY_RE.fullmatch(key)
        if slot_match:
            inline_raw[int(slot_match.group(1))] = value
        else:
            metadata[key] = value

    if not in_data:
        raise ValueError("Missing [DATA] section")
    for required in ("NAME", "SUBDIV", "LENGTH"):
        if not metadata.get(required):
            raise ValueError(f"Missing required field {required}")
    subdiv = metadata["SUBDIV"].upper()
    if subdiv not in VALID_SUBDIV:
        raise ValueError(f"Unsupported SUBDIV: {subdiv}")
    try:
        length = int(metadata["LENGTH"])
    except ValueError as exc:
        raise ValueError("LENGTH must be an integer") from exc
    if not 1 <= length <= 255:
        raise ValueError("LENGTH must be in 1..255")

    slot_map_name = metadata.get("SLOT_MAP_ID", DEFAULT_SLOT_MAP).upper()
    if slot_map_name == "INLINE":
        if not inline_raw:
            raise ValueError("SLOT_MAP_ID=INLINE requires SLOT0... definitions")
        indices = sorted(inline_raw)
        if indices != list(range(len(indices))):
            raise ValueError("INLINE slot indices must be contiguous from SLOT0")
        slots = tuple(parse_inline_slot(inline_raw[i], i) for i in indices)
    else:
        if inline_raw:
            raise ValueError("SLOT definitions are only valid with SLOT_MAP_ID=INLINE")
        if slot_map_name not in slot_maps_by_name:
            raise ValueError(f"Unknown SLOT_MAP_ID: {slot_map_name}")
        slots = slot_maps_by_name[slot_map_name].slots

    orientation = metadata.get("ORIENTATION", DEFAULT_ORIENTATION).upper()
    if orientation not in {"STEP", "SLOT"}:
        raise ValueError(f"Unsupported ORIENTATION: {orientation}")
    slot_count = len(slots)
    if orientation == "STEP":
        if len(data_lines) != length:
            raise ValueError(f"STEP data has {len(data_lines)} rows; LENGTH={length}")
        if any(len(row) != slot_count for row in data_lines):
            raise ValueError(f"Every STEP row must contain {slot_count} slot characters")
        steps = [[accent_from_char(ch, symbol_to_level) for ch in row] for row in data_lines]
    else:
        if len(data_lines) != slot_count:
            raise ValueError(f"SLOT data has {len(data_lines)} rows; slots={slot_count}")
        if any(len(row) != length for row in data_lines):
            raise ValueError(f"Every SLOT row must contain LENGTH={length} characters")
        steps = [[0] * slot_count for _ in range(length)]
        for slot_index, row in enumerate(data_lines):
            for step_index, ch in enumerate(row):
                steps[step_index][slot_index] = accent_from_char(ch, symbol_to_level)

    ppqn = int(metadata.get("PPQN", str(DEFAULT_PPQN)))
    if ppqn != DEFAULT_PPQN:
        raise ValueError(f"Unsupported ADT PPQN={ppqn}; expected {DEFAULT_PPQN}")
    tempo = None
    for key in ("BPM", "TEMPO"):
        if key in metadata:
            tempo = int(metadata[key])
            break
    return Pattern(
        name=metadata["NAME"], source_format="ADT v2.3", length=length, slots=slot_count,
        grid_type=subdiv, steps=steps,
        slot_notes=[slot.representative_midi for slot in slots],
        slot_abbr=[slot.abbrev for slot in slots],
        slot_full_names=[slot.extended for slot in slots],
        slot_map_name=slot_map_name,
        time_sig=metadata.get("TIME_SIG", "4/4"), tempo=tempo, ppqn=ppqn,
    )


def decode_payload_v23(payload: bytes, length: int, slots: int) -> List[List[int]]:
    """Decode ADP v2.3 Final packed hits: bit7 reserved, slot bits 6..3, accent bits 2..0."""
    steps = [[0] * slots for _ in range(length)]
    offset = 0
    for step_index in range(length):
        if offset >= len(payload):
            raise ValueError(f"Payload ended before step {step_index}")
        hit_count = payload[offset]
        offset += 1
        if offset + hit_count > len(payload):
            raise ValueError(f"Truncated hit list at step {step_index}")
        seen_slots: Set[int] = set()
        for _ in range(hit_count):
            hit = payload[offset]
            offset += 1
            if hit & 0x80:
                raise ValueError(f"Step {step_index}: reserved packed-hit bit 7 is not zero")
            slot = (hit >> 3) & 0x0F
            accent = hit & 0x07
            if slot >= slots:
                raise ValueError(f"Step {step_index}: slot {slot} outside slot map ({slots})")
            if accent not in {1, 2, 3, 4, 5}:
                raise ValueError(f"Step {step_index}: invalid stored accent {accent}")
            if slot in seen_slots:
                raise ValueError(f"Step {step_index}: duplicate slot index {slot}")
            seen_slots.add(slot)
            steps[step_index][slot] = accent
    if offset != len(payload):
        raise ValueError(f"ADP payload has {len(payload) - offset} unused byte(s)")
    return steps


def decode_payload_v22(payload: bytes, length: int, slots: int) -> List[List[int]]:
    """Decode legacy ADP v2.2 packed hits: slot bits 5..2 and accent bits 1..0."""
    steps = [[0] * slots for _ in range(length)]
    offset = 0
    for step_index in range(length):
        if offset >= len(payload):
            raise ValueError(f"Payload ended before step {step_index}")
        hit_count = payload[offset]
        offset += 1
        if offset + hit_count > len(payload):
            raise ValueError(f"Truncated hit list at step {step_index}")
        for _ in range(hit_count):
            hit = payload[offset]
            offset += 1
            if hit & 0xC0:
                raise ValueError(f"Step {step_index}: legacy reserved packed-hit bits are not zero")
            slot = (hit >> 2) & 0x0F
            accent = hit & 0x03
            if slot >= slots:
                raise ValueError(f"Step {step_index}: slot {slot} outside slot map ({slots})")
            if accent == 0:
                raise ValueError(f"Step {step_index}: stored legacy hit has accent 0")
            steps[step_index][slot] = max(steps[step_index][slot], accent)
    if offset != len(payload):
        raise ValueError(f"ADP payload has {len(payload) - offset} unused byte(s)")
    return steps


def find_companion_adt(path: Path) -> Optional[Path]:
    for suffix in (".ADT", ".adt"):
        candidate = path.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def load_adp3(path: Path, data: bytes, by_name: Dict[str, SlotMapDefinition], by_id: Dict[int, SlotMapDefinition], symbol_to_level: Dict[str, int]) -> Pattern:
    if len(data) < ADP3_HEADER_SIZE:
        raise ValueError("ADP3 file is shorter than the 12-byte header")
    magic, version, subdiv_code, length, slot_map_id, payload_bytes, payload_crc = struct.unpack(
        ADP3_HEADER_FMT, data[:ADP3_HEADER_SIZE]
    )
    if magic != b"ADP3" or version != 23:
        raise ValueError("Invalid ADP v2.3 header")
    if subdiv_code not in SUBDIV_CODE_TO_STR:
        raise ValueError(f"Unsupported ADP3 SUBDIV code: {subdiv_code}")
    payload = data[ADP3_HEADER_SIZE:]
    if len(payload) != payload_bytes:
        raise ValueError(f"ADP3 payload length mismatch: header={payload_bytes}, actual={len(payload)}")
    calculated_crc = crc16_ccitt(payload)
    if calculated_crc != payload_crc:
        raise ValueError(f"ADP3 payload CRC mismatch: header=0x{payload_crc:04X}, calculated=0x{calculated_crc:04X}")

    if slot_map_id == INLINE_SLOT_MAP_ID:
        companion = find_companion_adt(path)
        if companion is None:
            raise ValueError(f"ADP3 INLINE slot map requires companion {path.stem}.ADT beside the ADP")
        inline_pattern = parse_adt_v23(companion, by_name, symbol_to_level)
        if inline_pattern.slot_map_name != "INLINE":
            raise ValueError(f"Companion {companion.name} must declare SLOT_MAP_ID=INLINE")
        if inline_pattern.length != length or inline_pattern.grid_type != SUBDIV_CODE_TO_STR[subdiv_code]:
            raise ValueError("Companion ADT LENGTH/SUBDIV does not match ADP3 header")
        slot_notes = inline_pattern.slot_notes
        slot_abbr = inline_pattern.slot_abbr
        slot_full_names = inline_pattern.slot_full_names
        slot_map_name = "INLINE"
    else:
        if slot_map_id not in by_id:
            raise ValueError(f"Unknown registered SLOT_MAP_ID: {slot_map_id}")
        slot_map = by_id[slot_map_id]
        slot_notes = [slot.representative_midi for slot in slot_map.slots]
        slot_abbr = [slot.abbrev for slot in slot_map.slots]
        slot_full_names = [slot.extended for slot in slot_map.slots]
        slot_map_name = slot_map.name

    steps = decode_payload_v23(payload, length, len(slot_notes))
    return Pattern(
        name=path.stem, source_format="ADP v2.3", length=length, slots=len(slot_notes),
        grid_type=SUBDIV_CODE_TO_STR[subdiv_code], steps=steps,
        slot_notes=slot_notes, slot_abbr=slot_abbr,
        slot_full_names=slot_full_names, slot_map_name=slot_map_name,
        time_sig=None, tempo=None, ppqn=DEFAULT_PPQN,
    )


def load_adp2(path: Path, data: bytes, by_name: Dict[str, SlotMapDefinition]) -> Pattern:
    if len(data) < ADP2_HEADER_SIZE:
        raise ValueError("ADP2 file is shorter than the 20-byte header")
    (magic, version, grid_code, length, slots, ppqn, _swing, tempo, _reserved,
     _adt_crc, payload_bytes) = struct.unpack(ADP2_HEADER_FMT, data[:ADP2_HEADER_SIZE])
    if magic != b"ADP2" or version != 22:
        raise ValueError("Invalid ADP v2.2 header")
    if grid_code not in SUBDIV_CODE_TO_STR:
        raise ValueError(f"Unsupported ADP2 grid code: {grid_code}")
    legacy = by_name[DEFAULT_SLOT_MAP]
    if not 1 <= slots <= len(legacy.slots):
        raise ValueError(f"Unsupported legacy ADP2 slot count: {slots}")
    payload = data[ADP2_HEADER_SIZE:ADP2_HEADER_SIZE + payload_bytes]
    if len(payload) != payload_bytes or ADP2_HEADER_SIZE + payload_bytes != len(data):
        raise ValueError("ADP2 payload length mismatch")
    legacy_ppqn = None
    if ppqn == 96:
        legacy_ppqn = 96
        ppqn = DEFAULT_PPQN
    elif ppqn != DEFAULT_PPQN:
        raise ValueError(f"Unsupported ADP2 PPQN: {ppqn}")
    selected_slots = legacy.slots[:slots]
    return Pattern(
        name=path.stem, source_format="ADP v2.2", length=length, slots=slots,
        grid_type=SUBDIV_CODE_TO_STR[grid_code], steps=decode_payload_v22(payload, length, slots),
        slot_notes=[slot.representative_midi for slot in selected_slots],
        slot_abbr=[slot.abbrev for slot in selected_slots],
        slot_full_names=[slot.extended for slot in selected_slots],
        slot_map_name=DEFAULT_SLOT_MAP,
        time_sig=None, tempo=tempo or None, ppqn=ppqn,
        legacy_ppqn=legacy_ppqn,
    )


def load_adp(path: Path, by_name: Dict[str, SlotMapDefinition], by_id: Dict[int, SlotMapDefinition], symbol_to_level: Dict[str, int]) -> Pattern:
    data = path.read_bytes()
    magic = data[:4]
    if magic == b"ADP3":
        return load_adp3(path, data, by_name, by_id, symbol_to_level)
    if magic == b"ADP2":
        return load_adp2(path, data, by_name)
    raise ValueError("Not an ADP2 or ADP3 file")


def load_pattern(path: Path, by_name: Dict[str, SlotMapDefinition], by_id: Dict[int, SlotMapDefinition], symbol_to_level: Dict[str, int]) -> Pattern:
    if path.suffix.lower() == ".adt":
        return parse_adt_v23(path, by_name, symbol_to_level)
    if path.suffix.lower() == ".adp":
        return load_adp(path, by_name, by_id, symbol_to_level)
    raise ValueError("Input must have .ADT or .ADP extension")


@dataclass(frozen=True)
class OrnamentEvent:
    kind: str
    target_step: int
    slot: int
    offset_ticks: int
    velocity: int
    loop_wrap: bool = False


@dataclass
class OrnamentSidecar:
    path: Path
    events: List[OrnamentEvent]
    metadata: Dict[str, str]


def _step_ticks(pattern: Pattern) -> int:
    steps_per_whole = {"16": 16, "32": 32, "8T": 12, "16T": 24}[pattern.grid_type]
    numerator = pattern.ppqn * 4
    if numerator % steps_per_whole:
        raise ValueError(f"PPQN={pattern.ppqn} cannot represent SUBDIV={pattern.grid_type} with integer ticks")
    return numerator // steps_per_whole


def _slot_index(pattern: Pattern, token: str) -> int:
    token = token.strip()
    if token.isdigit():
        index = int(token)
        if 0 <= index < pattern.slots:
            return index
        raise ValueError(f"ORN SLOT index outside slots={pattern.slots}: {index}")
    matches = [i for i, abbr in enumerate(pattern.slot_abbr) if abbr.upper() == token.upper()]
    if len(matches) != 1:
        raise ValueError(f"ORN SLOT does not match exactly one slot: {token!r}")
    return matches[0]


def load_orn(path: Path, pattern: Pattern) -> OrnamentSidecar:
    metadata: Dict[str, str] = {}
    events: List[OrnamentEvent] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line or line.upper() == "[EVENTS]":
            continue
        if line.startswith("[") and line.endswith("]"):
            raise ValueError(f"{path.name}:{line_no}: unsupported section {line}")
        if " " not in line and "=" in line:
            key, value = line.split("=", 1)
            metadata[key.strip().upper()] = value.strip()
            continue
        parts = line.split()
        kind = parts[0].upper()
        fields: Dict[str, str] = {}
        for part in parts[1:]:
            if "=" not in part:
                raise ValueError(f"{path.name}:{line_no}: malformed ORN field: {part!r}")
            key, value = part.split("=", 1)
            fields[key.upper()] = value
        try:
            target_step = int(fields["TARGET_STEP"])
            slot = _slot_index(pattern, fields["SLOT"])
            offset_ticks = int(fields.get("OFFSET_TICKS", fields.get("OFFSET", "0")))
            velocity = int(fields["VELOCITY"])
        except KeyError as exc:
            raise ValueError(f"{path.name}:{line_no}: missing field {exc.args[0]}") from exc
        if not 0 <= target_step < pattern.length:
            raise ValueError(f"{path.name}:{line_no}: TARGET_STEP outside pattern: {target_step}")
        if not 1 <= velocity <= 127:
            raise ValueError(f"{path.name}:{line_no}: VELOCITY must be 1..127")
        if kind not in {"FLAM", "GHOST", "DRAG", "RUFF", "NOTE"}:
            raise ValueError(f"{path.name}:{line_no}: unsupported ornament type: {kind}")
        events.append(OrnamentEvent(kind, target_step, slot, offset_ticks, velocity,
                                    fields.get("LOOP_WRAP", "0").lower() in {"1", "true", "yes"}))

    if "PPQN" in metadata:
        raise ValueError("ORN must not store PPQN; it inherits the ADP/ADT tick base")
    if metadata.get("UNIT", "TICK").upper() not in {"TICK", "TICKS"}:
        raise ValueError("ORN UNIT must be TICK")
    for key, expected in (("GRID", pattern.grid_type), ("SUBDIV", pattern.grid_type), ("LENGTH", str(pattern.length))):
        if key in metadata and metadata[key].upper() != expected.upper():
            raise ValueError(f"ORN {key}={metadata[key]} does not match pattern {expected}")
    expected_loop_ticks = pattern.length * _step_ticks(pattern)
    if "LOOP_TICKS" in metadata and int(metadata["LOOP_TICKS"]) != expected_loop_ticks:
        raise ValueError(f"ORN LOOP_TICKS={metadata['LOOP_TICKS']} does not match derived {expected_loop_ticks}")
    return OrnamentSidecar(path, events, metadata)


def find_orn_path(pattern_path: Path, requested: Optional[Path]) -> Optional[Path]:
    if requested is not None:
        return requested
    for suffix in (".ORN", ".orn"):
        candidate = pattern_path.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None



DEFAULT_FLUIDSYNTH = Path(r"C:\Tools\FluidSynth\bin\fluidsynth.exe")
DEFAULT_SOUNDFONT = Path(r"C:\SoundFonts\GeneralUser-GS.sf2")
DEFAULT_AUDIO_DRIVER = "dsound"
DEFAULT_ACCENT_FILE = "accent_levels.json"
DEFAULT_SLOT_MAP_FILE = "slot_map_definitions.json"


def existing_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"File not found: {path}")
    return path


def resolve_definition_file(explicit: Optional[Path], filename: str) -> Path:
    """Prefer an explicit path, then current directory, then beside the script."""
    if explicit is not None:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Definition file not found: {path}")
        return path
    candidates = [Path.cwd() / filename, Path(__file__).resolve().with_name(filename)]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError(f"{filename} not found in the current directory or beside this script")


def load_accent_velocities(path: Path) -> Tuple[Dict[str, int], Dict[int, int]]:
    """Return the authoritative 6-accent symbol map and representative velocities."""
    return load_accent_scheme(path)


def resolve_fluidsynth(explicit: Optional[Path]) -> Tuple[Path, str]:
    if explicit is not None:
        return explicit.resolve(), "command-line override"
    found = shutil.which("fluidsynth.exe") or shutil.which("fluidsynth")
    if found and Path(found).is_file():
        return Path(found).resolve(), "PATH"
    if DEFAULT_FLUIDSYNTH.is_file():
        return DEFAULT_FLUIDSYNTH.resolve(), "embedded default"
    raise ValueError(
        "FluidSynth was not found. Use --fluidsynth, add fluidsynth.exe to PATH, "
        f"or install it at {DEFAULT_FLUIDSYNTH}"
    )


def resolve_soundfont(explicit: Optional[Path]) -> Tuple[Path, str]:
    if explicit is not None:
        return explicit.resolve(), "command-line override"
    if DEFAULT_SOUNDFONT.is_file():
        return DEFAULT_SOUNDFONT.resolve(), "embedded default"
    raise ValueError(f"SoundFont not found. Use --sf2 or place it at {DEFAULT_SOUNDFONT}")


def pattern_to_midi(
    pattern: Pattern,
    ornament: Optional[OrnamentSidecar],
    bpm: float,
    note_length: float,
    accent_velocities: Dict[int, int],
) -> "mido.MidiFile":
    if mido is None:
        raise RuntimeError("mido is not installed. Install it with: py -m pip install mido")
    if bpm <= 0:
        raise ValueError("BPM must be greater than zero")

    midi = mido.MidiFile(type=0, ticks_per_beat=pattern.ppqn)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name=pattern.name, time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    if pattern.time_sig and re.fullmatch(r"\d+/\d+", pattern.time_sig):
        numerator, denominator = map(int, pattern.time_sig.split("/", 1))
        track.append(mido.MetaMessage("time_signature", numerator=numerator, denominator=denominator, time=0))

    step_ticks = _step_ticks(pattern)
    loop_ticks = pattern.length * step_ticks
    tick_seconds = 60.0 / (bpm * pattern.ppqn)
    duration_ticks = max(1, round(max(0.005, note_length) / tick_seconds))
    events: List[Tuple[int, int, object]] = []

    for step_index, row in enumerate(pattern.steps):
        tick = step_index * step_ticks
        for slot, accent in enumerate(row):
            if accent <= 0:
                continue
            note = pattern.slot_notes[slot]
            velocity = accent_velocities.get(int(accent), 80)
            events.append((tick, 1, mido.Message("note_on", channel=9, note=note, velocity=velocity, time=0)))
            events.append((min(loop_ticks, tick + duration_ticks), 0,
                           mido.Message("note_off", channel=9, note=note, velocity=0, time=0)))

    if ornament:
        for event in ornament.events:
            raw_tick = event.target_step * step_ticks + event.offset_ticks
            if event.loop_wrap:
                # Keep wrap events at their logical position around the loop boundary.
                raw_tick %= loop_ticks
            elif not 0 <= raw_tick < loop_ticks:
                raise ValueError(f"ORN event falls outside loop without LOOP_WRAP: {raw_tick}")
            note = pattern.slot_notes[event.slot]
            events.append((raw_tick, 1, mido.Message("note_on", channel=9, note=note,
                                                    velocity=event.velocity, time=0)))
            events.append((min(loop_ticks, raw_tick + duration_ticks), 0,
                           mido.Message("note_off", channel=9, note=note, velocity=0, time=0)))

    events.sort(key=lambda item: (item[0], item[1], getattr(item[2], "note", 0)))
    last_tick = 0
    for tick, _order, message in events:
        message.time = max(0, tick - last_tick)
        track.append(message)
        last_tick = tick
    track.append(mido.MetaMessage("end_of_track", time=max(0, loop_ticks - last_tick)))
    return midi


def run_fluidsynth(
    fluidsynth: Path,
    soundfont: Path,
    audio_driver: str,
    midi_path: Path,
    loop_count: Optional[int],
    gain: Optional[float],
    quiet: bool,
    verbose: bool,
) -> int:
    """Run FluidSynth and configure player looping through its live shell.

    FluidSynth's -f configuration file is executed too early for player_loop:
    the MIDI player does not yet exist at that stage.  Therefore looping
    commands are written to stdin after the process has started.
    """
    command = [str(fluidsynth), "-a", audio_driver, "-n"]
    if quiet:
        command.append("-q")
    if gain is not None:
        command.extend(["-g", str(gain)])

    # Do not use -i here: the shell must remain available so player_loop can
    # be issued after the command-line MIDI player has been created.
    command.extend([str(soundfont), str(midi_path)])
    if verbose:
        print(f"Temp MIDI   : {midi_path}")
        print("Command     :", subprocess.list2cmdline(command))

    print()
    print("Ready.")
    print("Playing...")

    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        if loop_count not in (None, 0):
            if process.stdin is None:
                raise RuntimeError("FluidSynth shell input is unavailable")
            # Turn off synth reset between repetitions for smoother looping,
            # then set the player's remaining loop count (-1 = forever).
            process.stdin.write("set player.reset-synth 0\n")
            process.stdin.write(f"player_loop {loop_count}\n")
            process.stdin.flush()

        return process.wait()

    except KeyboardInterrupt:
        print("\nStopping...")
        if process is not None and process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write("player_stop\nquit\n")
                    process.stdin.flush()
                    process.wait(timeout=2)
                else:
                    process.terminate()
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=2)
        print("Stopped.")
        return 130



ACCENT_TO_CHAR = {0: ".", 1: "-", 2: "x", 3: "o", 4: "^", 5: "@"}


def _steps_per_beat(grid_type: str) -> int:
    return {"16": 4, "32": 8, "8T": 3, "16T": 6}[grid_type]


def _meter_numerator(time_sig: Optional[str]) -> int:
    if time_sig:
        match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", time_sig)
        if match:
            return max(1, int(match.group(1)))
    return 4


def _beat_header(grid_type: str, beat_number: int) -> str:
    if grid_type == "16":
        return f"{beat_number}e&a"
    if grid_type == "32":
        return f"{beat_number}e&a...."
    if grid_type == "8T":
        return f"{beat_number}ta"
    # Six 16th-triplet positions per beat. Keep it compact and unambiguous.
    return f"{beat_number}tata"


def _pattern_grid_lines(pattern: Pattern) -> tuple[str, list[str]]:
    steps_per_beat = _steps_per_beat(pattern.grid_type)
    beats_per_bar = _meter_numerator(pattern.time_sig)
    steps_per_bar = steps_per_beat * beats_per_bar

    beat_cells: list[str] = []
    for step_start in range(0, pattern.length, steps_per_beat):
        beat_in_bar = (step_start // steps_per_beat) % beats_per_bar + 1
        label = _beat_header(pattern.grid_type, beat_in_bar)
        width = min(steps_per_beat, pattern.length - step_start)
        beat_cells.append(label[:width].ljust(width))

    header_parts: list[str] = []
    for beat_index, cell in enumerate(beat_cells):
        if beat_index == 0:
            header_parts.append("|")
        elif (beat_index * steps_per_beat) % steps_per_bar == 0:
            header_parts.append("||")
        else:
            header_parts.append("|")
        header_parts.append(cell)
    header_parts.append("|")
    header = "".join(header_parts)

    row_grids: list[str] = []
    for slot_index in range(pattern.slots):
        chars = [ACCENT_TO_CHAR.get(int(pattern.steps[step][slot_index]), "?")
                 for step in range(pattern.length)]
        parts: list[str] = []
        for step_index, ch in enumerate(chars):
            if step_index == 0:
                parts.append("|")
            elif step_index % steps_per_bar == 0:
                parts.append("||")
            elif step_index % steps_per_beat == 0:
                parts.append("|")
            parts.append(ch)
        parts.append("|")
        row_grids.append("".join(parts))
    return header, row_grids


def print_ascii_pattern(pattern: Pattern, show_all_slots: bool = False) -> None:
    visible_slots = [
        slot for slot in range(pattern.slots)
        if show_all_slots or any(pattern.steps[step][slot] > 0 for step in range(pattern.length))
    ]
    header, row_grids = _pattern_grid_lines(pattern)

    full_width = max(
        [len(pattern.slot_full_names[i]) for i in visible_slots] or [len("FULL NAME")]
    )
    full_width = max(full_width, len("FULL NAME"))
    label_width = 2 + 2 + full_width + 2 + 3

    print()
    print(f"Visible  : {len(visible_slots)} / {pattern.slots} slots")
    print(" " * (label_width + 1) + header)

    if not visible_slots:
        print(" " * (label_width + 1) + "(no events)")
        return

    # Reverse vertical order so the kick and other low-numbered slots appear below.
    for slot in reversed(visible_slots):
        abbrev = pattern.slot_abbr[slot][:2].upper().ljust(2)
        full_name = pattern.slot_full_names[slot]
        note = pattern.slot_notes[slot]
        label = f"{abbrev}  {full_name:<{full_width}}  {note:>3}"
        print(f"{label} {row_grids[slot]}")

def print_pattern_info(pattern: Pattern, bpm: float, repeat_text: str,
                       ornament: Optional[OrnamentSidecar]) -> None:
    display_format = pattern.source_format
    if pattern.source_format == "ADP v2.2":
        display_format += " (legacy)"

    print("=" * 61)
    print(f"ADX Player Win {VERSION}")
    print("=" * 61)
    print()
    print(f"File      : {pattern.name}")
    print(f"Format    : {display_format}")
    print(f"Resolution: {pattern.grid_type}")
    print(f"Length    : {pattern.length} steps")
    print(f"Tempo     : {bpm:g} BPM")
    print(f"Repeat    : {repeat_text}")
    print(f"ORN       : {ornament.path.name if ornament else 'none'}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Windows FluidSynth player for ADT v2.3, ADP v2.3, ORN v1.0, and Standard MIDI Files."
    )
    parser.add_argument("file", type=Path, help=".ADT, .ADP, .MID, or .MIDI file")
    parser.add_argument("--slot-maps", type=Path,
                        help=f"slot-map JSON (default: .\\{DEFAULT_SLOT_MAP_FILE}, then beside script)")
    parser.add_argument("--accent-levels", type=Path,
                        help=f"accent JSON (default: .\\{DEFAULT_ACCENT_FILE}, then beside script)")
    parser.add_argument("--orn", type=Path, help="ORN sidecar (default: same basename as ADT/ADP)")
    parser.add_argument("--no-orn", action="store_true", help="ignore automatically discovered ORN")
    parser.add_argument("--fluidsynth", type=existing_file,
                        help="override fluidsynth.exe; otherwise search PATH, then embedded default")
    parser.add_argument("--sf2", type=existing_file, help=f"override SoundFont; default: {DEFAULT_SOUNDFONT}")
    parser.add_argument("--audio-driver", default=DEFAULT_AUDIO_DRIVER,
                        help=f"FluidSynth audio driver (default: {DEFAULT_AUDIO_DRIVER})")
    parser.add_argument("--bpm", type=float, help="override ADT/ADP playback tempo")
    repeat = parser.add_mutually_exclusive_group()
    repeat.add_argument("--loop", action="store_true", help="loop indefinitely until Ctrl+C")
    repeat.add_argument("--count", type=int, default=1, help="total plays (default: 1)")
    parser.add_argument("--note-length", type=float, default=0.05, metavar="SECONDS",
                        help="rendered ADX drum-note duration (default: 0.05)")
    parser.add_argument("--gain", type=float, help="FluidSynth gain, e.g. 0.4")
    parser.add_argument("--quiet", action="store_true", help="pass -q to FluidSynth")
    parser.add_argument("--show-all-slots", action="store_true",
                        help="show empty slots too; default display omits rows with no events")
    parser.add_argument("--verbose", action="store_true",
                        help="show full paths, temporary MIDI path, and FluidSynth command")
    parser.add_argument("--validate", action="store_true", help="validate input without playback")
    parser.add_argument("--export-midi", type=Path,
                        help="write rendered ADT/ADP/ORN as a Standard MIDI File")
    parser.add_argument("--version", action="version", version=VERSION_TEXT)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    input_path = args.file.expanduser().resolve()
    if not input_path.is_file():
        print(f"File not found: {input_path}", file=sys.stderr)
        return 2
    if input_path.suffix.lower() not in {".adt", ".adp", ".mid", ".midi"}:
        print("Input must be ADT, ADP, MID, or MIDI.", file=sys.stderr)
        return 2
    if args.count < 1:
        print("--count must be at least 1", file=sys.stderr)
        return 2
    if args.bpm is not None and args.bpm <= 0:
        print("--bpm must be greater than zero", file=sys.stderr)
        return 2
    if args.note_length <= 0:
        print("--note-length must be greater than zero", file=sys.stderr)
        return 2
    if args.gain is not None and args.gain <= 0:
        print("--gain must be greater than zero", file=sys.stderr)
        return 2

    temporary_midi: Optional[Path] = None
    try:
        is_midi = input_path.suffix.lower() in {".mid", ".midi"}
        if is_midi:
            midi_path = input_path
            if args.validate:
                if mido is None:
                    raise RuntimeError("mido is required for MIDI validation")
                mido.MidiFile(midi_path)
                print(f"Validation: OK ({midi_path.name})")
                return 0
            if args.export_midi:
                raise ValueError("--export-midi is only needed for ADT/ADP input")
        else:
            slot_map_path = resolve_definition_file(args.slot_maps, DEFAULT_SLOT_MAP_FILE)
            accent_path = resolve_definition_file(args.accent_levels, DEFAULT_ACCENT_FILE)
            by_name, by_id = load_slot_maps(slot_map_path)
            symbol_to_level, accents = load_accent_velocities(accent_path)
            global BODY_OK
            BODY_OK = set(symbol_to_level)
            pattern = load_pattern(input_path, by_name, by_id, symbol_to_level)
            orn_path = None if args.no_orn else find_orn_path(input_path, args.orn)
            if orn_path is not None and not orn_path.is_file():
                raise ValueError(f"ORN file not found: {orn_path}")
            ornament = load_orn(orn_path, pattern) if orn_path else None
            bpm = args.bpm or pattern.tempo or 120.0
            repeat_text = "infinite" if args.loop else str(args.count)
            print_pattern_info(pattern, bpm, repeat_text, ornament)
            print_ascii_pattern(pattern, show_all_slots=args.show_all_slots)
            print()
            if args.verbose:
                print(f"Slot JSON   : {slot_map_path}")
                print(f"Accent JSON : {accent_path}")
                if pattern.legacy_ppqn is not None:
                    print(f"Legacy PPQN : {pattern.legacy_ppqn}")
                    print(f"Normalized  : {pattern.ppqn}")
            rendered = pattern_to_midi(pattern, ornament, bpm, args.note_length, accents)
            if args.export_midi:
                output = args.export_midi.expanduser().resolve()
                rendered.save(output)
                print(f"MIDI saved: {output}")
                if args.validate:
                    print("Validation: OK")
                    return 0
            elif args.validate:
                print("Validation: OK")
                return 0
            else:
                handle = tempfile.NamedTemporaryFile(prefix="adx_player_", suffix=".mid", delete=False)
                handle.close()
                temporary_midi = Path(handle.name)
                rendered.save(temporary_midi)
            midi_path = args.export_midi.expanduser().resolve() if args.export_midi else temporary_midi

        fluidsynth, fs_source = resolve_fluidsynth(args.fluidsynth)
        soundfont, sf_source = resolve_soundfont(args.sf2)
        if args.verbose:
            print(f"FluidSynth  : {fluidsynth} ({fs_source})")
            print(f"SoundFont   : {soundfont} ({sf_source})")
        else:
            print(f"FluidSynth : {fs_source}")
            print(f"SoundFont  : {soundfont.name}")
        # player_loop counts additional loops: 0 = once, -1 = infinite.
        loop_count = -1 if args.loop else max(0, args.count - 1)
        return run_fluidsynth(fluidsynth, soundfont, args.audio_driver, midi_path,
                              loop_count, args.gain, args.quiet, args.verbose)
    except (ValueError, RuntimeError, OSError, struct.error, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary_midi is not None:
            try:
                temporary_midi.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
