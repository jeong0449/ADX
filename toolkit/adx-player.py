#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adx-player 260802b — ADT/ADP reference playback CLI for Raspberry Pi.

Supports:
- ADT v2.3 text patterns
- ADP v2.3 12-byte binary caches (ADP3)
- Legacy ADP v2.2 caches (ADP2)
- Optional same-basename ORN sidecars
- Registered slot maps from slot_map_definitions.json
- ADP3 SLOT_MAP_ID=255 (INLINE) via a same-basename companion ADT

FluidSynth (or another ALSA MIDI synthesizer) is started separately.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

try:
    import mido
except ImportError:  # handled cleanly in main
    mido = None

SCRIPT_NAME = "adx-player.py"
VERSION = "260802b"
VERSION_TEXT = f"{SCRIPT_NAME} {VERSION}"

ADT_VERSION_LINE = "; ADT v2.3"
DEFAULT_SLOT_MAP = "LEGACY"
DEFAULT_ORIENTATION = "STEP"
DEFAULT_PPQN = 240
INLINE_SLOT_MAP_ID = 255

SUBDIV_CODE_TO_STR = {0: "16", 1: "8T", 2: "16T"}
VALID_SUBDIV = set(SUBDIV_CODE_TO_STR.values())
VELOCITY = {0: 0, 1: 32, 2: 80, 3: 120}
BODY_OK = {".", "-", "x", "X", "o", "O", "^"}
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
    slot_map_name: str
    time_sig: Optional[str] = None
    tempo: Optional[int] = None
    ppqn: int = DEFAULT_PPQN


def crc16_ccitt(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
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
    raise ValueError(f"Invalid ADT data symbol: {ch!r}")


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


def parse_adt_v23(path: Path, slot_maps_by_name: Dict[str, SlotMapDefinition]) -> Pattern:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"Cannot read ADT {path}: {exc}") from exc
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
            if any(ch not in BODY_OK for ch in compact):
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
        steps = [[accent_from_char(ch) for ch in row] for row in data_lines]
    else:
        if len(data_lines) != slot_count:
            raise ValueError(f"SLOT data has {len(data_lines)} rows; slots={slot_count}")
        if any(len(row) != length for row in data_lines):
            raise ValueError(f"Every SLOT row must contain LENGTH={length} characters")
        steps = [[0] * slot_count for _ in range(length)]
        for slot_index, row in enumerate(data_lines):
            for step_index, ch in enumerate(row):
                steps[step_index][slot_index] = accent_from_char(ch)

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
        slot_abbr=[slot.abbrev for slot in slots], slot_map_name=slot_map_name,
        time_sig=metadata.get("TIME_SIG", "4/4"), tempo=tempo, ppqn=ppqn,
    )


def decode_payload(payload: bytes, length: int, slots: int) -> List[List[int]]:
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
                raise ValueError(f"Step {step_index}: reserved packed-hit bits are not zero")
            slot = (hit >> 2) & 0x0F
            accent = hit & 0x03
            if slot >= slots:
                raise ValueError(f"Step {step_index}: slot {slot} outside slot map ({slots})")
            if accent == 0:
                raise ValueError(f"Step {step_index}: stored hit has accent 0")
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


def load_adp3(path: Path, data: bytes, by_name: Dict[str, SlotMapDefinition], by_id: Dict[int, SlotMapDefinition]) -> Pattern:
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
        inline_pattern = parse_adt_v23(companion, by_name)
        if inline_pattern.slot_map_name != "INLINE":
            raise ValueError(f"Companion {companion.name} must declare SLOT_MAP_ID=INLINE")
        if inline_pattern.length != length or inline_pattern.grid_type != SUBDIV_CODE_TO_STR[subdiv_code]:
            raise ValueError("Companion ADT LENGTH/SUBDIV does not match ADP3 header")
        slot_notes = inline_pattern.slot_notes
        slot_abbr = inline_pattern.slot_abbr
        slot_map_name = "INLINE"
    else:
        if slot_map_id not in by_id:
            raise ValueError(f"Unknown registered SLOT_MAP_ID: {slot_map_id}")
        slot_map = by_id[slot_map_id]
        slot_notes = [slot.representative_midi for slot in slot_map.slots]
        slot_abbr = [slot.abbrev for slot in slot_map.slots]
        slot_map_name = slot_map.name

    steps = decode_payload(payload, length, len(slot_notes))
    return Pattern(
        name=path.stem, source_format="ADP v2.3", length=length, slots=len(slot_notes),
        grid_type=SUBDIV_CODE_TO_STR[subdiv_code], steps=steps,
        slot_notes=slot_notes, slot_abbr=slot_abbr, slot_map_name=slot_map_name,
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
    if ppqn == 96:
        print(f"Warning: {path.name} uses legacy PPQN=96; normalized to 240.", file=sys.stderr)
        ppqn = DEFAULT_PPQN
    elif ppqn != DEFAULT_PPQN:
        raise ValueError(f"Unsupported ADP2 PPQN: {ppqn}")
    selected_slots = legacy.slots[:slots]
    return Pattern(
        name=path.stem, source_format="ADP v2.2", length=length, slots=slots,
        grid_type=SUBDIV_CODE_TO_STR[grid_code], steps=decode_payload(payload, length, slots),
        slot_notes=[slot.representative_midi for slot in selected_slots],
        slot_abbr=[slot.abbrev for slot in selected_slots], slot_map_name=DEFAULT_SLOT_MAP,
        time_sig=None, tempo=tempo or None, ppqn=ppqn,
    )


def load_adp(path: Path, by_name: Dict[str, SlotMapDefinition], by_id: Dict[int, SlotMapDefinition]) -> Pattern:
    data = path.read_bytes()
    magic = data[:4]
    if magic == b"ADP3":
        return load_adp3(path, data, by_name, by_id)
    if magic == b"ADP2":
        return load_adp2(path, data, by_name)
    raise ValueError("Not an ADP2 or ADP3 file")


def load_pattern(path: Path, by_name: Dict[str, SlotMapDefinition], by_id: Dict[int, SlotMapDefinition]) -> Pattern:
    if path.suffix.lower() == ".adt":
        return parse_adt_v23(path, by_name)
    if path.suffix.lower() == ".adp":
        return load_adp(path, by_name, by_id)
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
    steps_per_whole = {"16": 16, "8T": 12, "16T": 24}[pattern.grid_type]
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


def list_ports() -> int:
    if mido is None:
        print("mido/python-rtmidi is not installed.", file=sys.stderr)
        print("Install: sudo apt install python3-mido python3-rtmidi", file=sys.stderr)
        return 2
    names = mido.get_output_names()
    if not names:
        print("No MIDI output ports found.")
        return 1
    for i, name in enumerate(names):
        print(f"{i}: {name}")
    return 0


def choose_port(requested: Optional[str]):
    names = mido.get_output_names()
    if not names:
        raise RuntimeError("No MIDI output ports found")
    if requested is None:
        if len(names) == 1:
            return names[0]
        fluid = [name for name in names if "fluid" in name.lower()]
        if len(fluid) == 1:
            return fluid[0]
        raise RuntimeError("Multiple MIDI ports found; specify --port. Use --list-ports.")
    if requested.isdigit():
        index = int(requested)
        if not 0 <= index < len(names):
            raise RuntimeError(f"Port index {index} is out of range")
        return names[index]
    exact = [name for name in names if name == requested]
    if exact:
        return exact[0]
    partial = [name for name in names if requested.lower() in name.lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise RuntimeError(f"Port name is ambiguous: {requested!r}")
    raise RuntimeError(f"MIDI port not found: {requested!r}")


def print_pattern_info(pattern: Pattern, bpm: float, repeat_text: str, ornament: Optional[OrnamentSidecar]) -> None:
    print(f"File     : {pattern.name}")
    print(f"Format   : {pattern.source_format}")
    print(f"Meter    : {pattern.time_sig or 'not stored'}")
    print(f"SUBDIV   : {pattern.grid_type}")
    print(f"Length   : {pattern.length} steps")
    print(f"Slot map : {pattern.slot_map_name} ({pattern.slots} slots)")
    print(f"Tempo    : {bpm:g} BPM")
    print(f"PPQN     : {pattern.ppqn}")
    print(f"ORN      : {ornament.path.name if ornament else 'none'}")
    print(f"Repeat   : {repeat_text}")


def play(pattern: Pattern, ornament: Optional[OrnamentSidecar], port_name: str, bpm: float,
         repeats: Optional[int], note_length: float, verbose: bool) -> None:
    """Play one pattern, applying ORN events with correct loop-boundary semantics.

    A LOOP_WRAP ornament with a negative offset targets the first step of the
    *next* cycle.  It is therefore omitted after the final cycle of a finite
    playback, avoiding an orphan grace note when --count 1 is used.
    """
    tick_seconds = 60.0 / (bpm * pattern.ppqn)
    step_ticks = _step_ticks(pattern)
    loop_ticks = pattern.length * step_ticks
    channel = 9
    note_length = max(0.005, note_length)
    duration_ticks = max(1, round(note_length / tick_seconds))

    regular_on: List[Tuple[int, int, str, int, int, str]] = []
    wrap_on: List[Tuple[int, int, str, int, int, str]] = []

    for step_index, row in enumerate(pattern.steps):
        tick = step_index * step_ticks
        for slot, accent in enumerate(row):
            if accent > 0:
                regular_on.append((tick, 1, "note_on", pattern.slot_notes[slot],
                                   VELOCITY.get(int(accent), 80),
                                   f"MAIN {pattern.slot_abbr[slot]}:{accent}"))

    if ornament:
        for event in ornament.events:
            raw_tick = event.target_step * step_ticks + event.offset_ticks
            note = pattern.slot_notes[event.slot]
            item = (raw_tick % loop_ticks, 0, "note_on", note, event.velocity,
                    f"{event.kind} {pattern.slot_abbr[event.slot]} offset={event.offset_ticks}")
            if event.loop_wrap:
                if 0 <= raw_tick < loop_ticks:
                    raise ValueError(
                        f"ORN LOOP_WRAP is unnecessary for in-range event: {raw_tick}"
                    )
                wrap_on.append(item)
            else:
                if not 0 <= raw_tick < loop_ticks:
                    raise ValueError(f"ORN event falls outside loop without LOOP_WRAP: {raw_tick}")
                regular_on.append(item)

    def make_timeline(on_events: List[Tuple[int, int, str, int, int, str]]):
        timeline: List[Tuple[int, int, str, int, int, str]] = []
        for event in on_events:
            tick, _order, _typ, note, _velocity, label = event
            timeline.append(event)
            timeline.append((min(tick + duration_ticks, loop_ticks - 1),
                             2, "note_off", note, 0, label))
        timeline.sort(key=lambda item: (item[0], item[1], item[3]))
        return timeline

    normal_timeline = make_timeline(regular_on)
    wrap_timeline = make_timeline(wrap_on)

    with mido.open_output(port_name) as port:
        print(f"MIDI out: {port_name}")
        print("Playing. Press Ctrl+C to stop.")
        cycle = 0
        cycle_deadline = time.monotonic()
        try:
            while repeats is None or cycle < repeats:
                # LOOP_WRAP grace events belong before the first step of the next
                # cycle.  Play them near the end of this cycle only when another
                # cycle will actually follow.
                has_next_cycle = repeats is None or cycle + 1 < repeats
                timeline = normal_timeline + (wrap_timeline if has_next_cycle else [])
                timeline.sort(key=lambda item: (item[0], item[1], item[3]))
                for tick, _order, message_type, note, velocity, label in timeline:
                    deadline = cycle_deadline + tick * tick_seconds
                    wait = deadline - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                    elif wait < -(step_ticks * tick_seconds):
                        cycle_deadline = time.monotonic() - tick * tick_seconds
                    port.send(mido.Message(message_type, channel=channel, note=note, velocity=velocity))
                    if verbose and message_type == "note_on":
                        print(f"loop {cycle + 1:>3} tick {tick:>5}/{loop_ticks}: {label}")
                cycle += 1
                cycle_deadline += loop_ticks * tick_seconds
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            port.send(mido.Message("control_change", channel=channel, control=123, value=0))
            port.send(mido.Message("control_change", channel=channel, control=120, value=0))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reference CLI player for ADT v2.3 / ADP v2.2-v2.3 drum patterns with ORN sidecars."
    )
    parser.add_argument("file", nargs="?", type=Path, help="ADT or ADP pattern file")
    parser.add_argument("--slot-maps", type=Path, default=None,
                        help="slot_map_definitions.json (default: beside this script)")
    parser.add_argument("--orn", type=Path, help="ORN sidecar path (default: same basename as pattern)")
    parser.add_argument("--no-orn", action="store_true", help="ignore an automatically discovered ORN sidecar")
    parser.add_argument("--port", help="MIDI output port name, substring, or --list-ports index")
    parser.add_argument("--list-ports", action="store_true", help="list MIDI output ports and exit")
    parser.add_argument("--bpm", type=float, help="override pattern/header tempo")
    repeat = parser.add_mutually_exclusive_group()
    repeat.add_argument("--loop", action="store_true", help="repeat until Ctrl+C")
    repeat.add_argument("--count", type=int, default=1, help="number of pattern repetitions (default: 1)")
    parser.add_argument("--note-length", type=float, default=0.05, metavar="SECONDS",
                        help="drum note duration (default: 0.05)")
    parser.add_argument("--verbose", action="store_true", help="print each note-on event")
    parser.add_argument("--validate", action="store_true", help="parse and validate without MIDI playback")
    parser.add_argument("--version", action="version", version=VERSION_TEXT)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.list_ports:
        return list_ports()
    if args.file is None:
        print("A .ADT or .ADP file is required.", file=sys.stderr)
        return 2
    if not args.file.is_file():
        print(f"File not found: {args.file}", file=sys.stderr)
        return 2
    if args.count is not None and args.count < 1:
        print("--count must be at least 1", file=sys.stderr)
        return 2
    if args.bpm is not None and args.bpm <= 0:
        print("--bpm must be greater than zero", file=sys.stderr)
        return 2

    try:
        slot_map_path = args.slot_maps or Path(__file__).with_name("slot_map_definitions.json")
        by_name, by_id = load_slot_maps(slot_map_path)
        pattern = load_pattern(args.file, by_name, by_id)
        orn_path = None if args.no_orn else find_orn_path(args.file, args.orn)
        if orn_path is not None and not orn_path.is_file():
            raise ValueError(f"ORN file not found: {orn_path}")
        ornament = load_orn(orn_path, pattern) if orn_path else None
        bpm = args.bpm or pattern.tempo or 120.0
        repeat_text = "infinite" if args.loop else str(args.count)
        print_pattern_info(pattern, bpm, repeat_text, ornament)
        if args.validate:
            print("Validation: OK")
            return 0
        if mido is None:
            raise RuntimeError("mido/python-rtmidi is not installed")
        port_name = choose_port(args.port)
        play(pattern, ornament, port_name, bpm, None if args.loop else args.count,
             args.note_length, args.verbose)
        return 0
    except (ValueError, RuntimeError, OSError, struct.error, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
