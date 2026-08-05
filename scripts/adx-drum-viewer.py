#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adx-drum-viewer.py 260802a

Render ADT/ADP patterns and optional same-basename ORN sidecars as one
self-contained interactive HTML/SVG catalog.

Input forms
-----------
    python adx-viewer.py WLZ_0005.ADP
    python adx-viewer.py WLZ_0005.ADP,RCK_0001.ADT
    python adx-viewer.py WLZ_0005.ADP RCK_0001.ADT
    python adx-viewer.py ./ADP
    python adx-viewer.py ./ADT ./ADP --recursive

Rules
-----
- Primary pattern files are ADT or ADP.
- An ORN argument is resolved to a same-basename ADP first, then ADT.
- Directory scans prefer ADP over ADT when both share the same basename.
- Same-basename ORN is loaded automatically.
- ADP3 SLOT_MAP_ID=255 requires a same-basename companion ADT.
- Registered slot maps are read from slot_map_definitions.json.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

SCRIPT_NAME = "adx-drum-viewer.py"
VERSION = "260802d"
VERSION_TEXT = f"{SCRIPT_NAME} {VERSION}"
ADT_VERSION_LINE = "; ADT v2.3"
DEFAULT_SLOT_MAP = "LEGACY"
DEFAULT_ORIENTATION = "STEP"
DEFAULT_PPQN = 240
INLINE_SLOT_MAP_ID = 255
SUBDIV_CODE_TO_STR = {0: "16", 1: "8T", 2: "16T"}
VALID_SUBDIV = set(SUBDIV_CODE_TO_STR.values())
STEPS_PER_QUARTER = {"16": 4, "8T": 3, "16T": 6}
BODY_OK = {".", "-", "x", "X", "o", "O", "^"}
SLOT_KEY_RE = re.compile(r"^SLOT([0-9]+)$")
NAME_RE = re.compile(r"^[A-Z0-9]{3}_[0-9]{4}$")
ADP3_HEADER_FMT = "<4sBBBBHH"
ADP3_HEADER_SIZE = struct.calcsize(ADP3_HEADER_FMT)


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
    path: Path
    name: str
    source_format: str
    length: int
    subdiv: str
    steps: List[List[int]]
    slots: Tuple[SlotDefinition, ...]
    slot_map_name: str
    slot_map_id: int
    time_sig: Optional[str] = None
    source: Optional[str] = None
    ppqn: int = DEFAULT_PPQN
    ornaments: List["OrnamentEvent"] = field(default_factory=list)

    @property
    def slot_count(self) -> int:
        return len(self.slots)


@dataclass(frozen=True)
class OrnamentEvent:
    kind: str
    target_step: int
    slot: int
    offset_ticks: int
    velocity: int
    loop_wrap: bool = False
    confidence: Optional[str] = None


def crc16_ccitt(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def accent_from_char(ch: str) -> int:
    c = ch.lower()
    if c == ".": return 0
    if c == "-": return 1
    if c == "x": return 2
    if c in {"o", "^"}: return 3
    raise ValueError(f"invalid ADT data symbol: {ch!r}")


def load_slot_maps(path: Path) -> Tuple[Dict[str, SlotMapDefinition], Dict[int, SlotMapDefinition]]:
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"slot-map definition not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read slot-map definition {path}: {exc}") from exc
    if not isinstance(root, list) or not root:
        raise ValueError("slot-map JSON root must be a non-empty array")

    by_name: Dict[str, SlotMapDefinition] = {}
    by_id: Dict[int, SlotMapDefinition] = {}
    for raw_map in root:
        if not isinstance(raw_map, dict):
            raise ValueError("each slot-map entry must be an object")
        map_id, name, raw_slots = raw_map.get("slot_map_id"), raw_map.get("name"), raw_map.get("slots")
        if not isinstance(map_id, int) or not 0 <= map_id <= 254 or map_id in by_id:
            raise ValueError(f"invalid or duplicate slot_map_id: {map_id!r}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"invalid slot-map name: {name!r}")
        name = name.strip().upper()
        if name == "INLINE" or name in by_name:
            raise ValueError(f"reserved or duplicate slot-map name: {name}")
        if not isinstance(raw_slots, list) or not raw_slots:
            raise ValueError(f"slot map {name}: slots must be a non-empty list")

        slots: List[SlotDefinition] = []
        seen: Set[int] = set()
        for raw_slot in raw_slots:
            if not isinstance(raw_slot, dict):
                raise ValueError(f"slot map {name}: every slot must be an object")
            index = raw_slot.get("slot")
            abbrev = raw_slot.get("abbrev")
            extended = raw_slot.get("extended", abbrev)
            representative = raw_slot.get("representative_midi")
            allowed = raw_slot.get("midi_input_allowed")
            if not isinstance(index, int) or not 0 <= index <= 15 or index in seen:
                raise ValueError(f"slot map {name}: invalid or duplicate slot index {index!r}")
            if not isinstance(abbrev, str) or not abbrev.strip():
                raise ValueError(f"slot map {name}, slot {index}: missing abbrev")
            if not isinstance(extended, str) or not extended.strip():
                raise ValueError(f"slot map {name}, slot {index}: missing extended name")
            if not isinstance(representative, int) or not 0 <= representative <= 127:
                raise ValueError(f"slot map {name}, slot {index}: invalid representative_midi")
            if not isinstance(allowed, list) or not allowed or any(not isinstance(n, int) or not 0 <= n <= 127 for n in allowed):
                raise ValueError(f"slot map {name}, slot {index}: invalid midi_input_allowed")
            if representative not in allowed:
                raise ValueError(f"slot map {name}, slot {index}: representative_midi must be allowed")
            seen.add(index)
            slots.append(SlotDefinition(index, abbrev.strip().upper(), extended.strip(), representative, tuple(allowed)))
        slots.sort(key=lambda item: item.index)
        if [slot.index for slot in slots] != list(range(len(slots))):
            raise ValueError(f"slot map {name}: slot indices must be contiguous")
        slot_map = SlotMapDefinition(map_id, name, tuple(slots))
        by_name[name], by_id[map_id] = slot_map, slot_map
    if DEFAULT_SLOT_MAP not in by_name:
        raise ValueError(f"default slot map {DEFAULT_SLOT_MAP!r} is absent from {path}")
    return by_name, by_id


def parse_inline_slot(value: str, index: int) -> SlotDefinition:
    match = re.fullmatch(r"\s*([^@,\s]+)\s*@\s*([0-9]{1,3})\s*(?:,\s*(.+?)\s*)?", value)
    if not match:
        raise ValueError(f"invalid SLOT{index} definition: {value!r}")
    note = int(match.group(2))
    if not 0 <= note <= 127:
        raise ValueError(f"SLOT{index} MIDI note must be in 0..127")
    abbrev = match.group(1).upper()
    return SlotDefinition(index, abbrev, (match.group(3) or abbrev).strip(), note, (note,))


def parse_adt_v23(path: Path, by_name: Dict[str, SlotMapDefinition]) -> Pattern:
    raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not raw_lines or raw_lines[0].strip() != ADT_VERSION_LINE:
        raise ValueError(f"first line must be exactly {ADT_VERSION_LINE!r}")
    metadata: Dict[str, str] = {}
    inline_raw: Dict[int, str] = {}
    data_lines: List[str] = []
    in_data = False
    for line_no, raw in enumerate(raw_lines[1:], start=2):
        line = raw.split(";", 1)[0].strip()
        if not line: continue
        if line.upper() == "[DATA]":
            if in_data: raise ValueError(f"{path.name}:{line_no}: duplicate [DATA]")
            in_data = True; continue
        if in_data:
            compact = "".join(ch for ch in line if not ch.isspace())
            if any(ch not in BODY_OK for ch in compact):
                raise ValueError(f"{path.name}:{line_no}: invalid pattern data")
            data_lines.append(compact); continue
        if "=" not in line:
            raise ValueError(f"{path.name}:{line_no}: expected FIELD=VALUE or [DATA]")
        key, value = line.split("=", 1); key, value = key.strip().upper(), value.strip()
        slot_match = SLOT_KEY_RE.fullmatch(key)
        if slot_match:
            index = int(slot_match.group(1))
            if index in inline_raw: raise ValueError(f"{path.name}:{line_no}: duplicate SLOT{index}")
            inline_raw[index] = value
        else:
            if key in metadata: raise ValueError(f"{path.name}:{line_no}: duplicate field {key}")
            metadata[key] = value
    if not in_data: raise ValueError("missing [DATA] section")
    for required in ("NAME", "SUBDIV", "LENGTH"):
        if not metadata.get(required): raise ValueError(f"missing required field {required}")
    name = metadata["NAME"].strip().upper()
    if not NAME_RE.fullmatch(name): raise ValueError(f"NAME must match ABC_0001, got {name!r}")
    subdiv = metadata["SUBDIV"].upper()
    if subdiv not in VALID_SUBDIV: raise ValueError(f"unsupported SUBDIV: {subdiv}")
    try: length = int(metadata["LENGTH"])
    except ValueError as exc: raise ValueError("LENGTH must be an integer") from exc
    if not 1 <= length <= 255: raise ValueError("LENGTH must be in 1..255")

    slot_map_name = metadata.get("SLOT_MAP_ID", DEFAULT_SLOT_MAP).upper()
    if slot_map_name == "INLINE":
        if not inline_raw: raise ValueError("SLOT_MAP_ID=INLINE requires SLOT0... definitions")
        indices = sorted(inline_raw)
        if indices != list(range(len(indices))): raise ValueError("INLINE slot indices must be contiguous from SLOT0")
        slots = tuple(parse_inline_slot(inline_raw[i], i) for i in indices)
        slot_map_id = INLINE_SLOT_MAP_ID
    else:
        if inline_raw: raise ValueError("SLOT definitions are only valid with SLOT_MAP_ID=INLINE")
        if slot_map_name not in by_name: raise ValueError(f"unknown SLOT_MAP_ID: {slot_map_name}")
        slot_map = by_name[slot_map_name]; slots, slot_map_id = slot_map.slots, slot_map.map_id

    orientation = metadata.get("ORIENTATION", DEFAULT_ORIENTATION).upper()
    if orientation not in {"STEP", "SLOT"}: raise ValueError(f"unsupported ORIENTATION: {orientation}")
    slot_count = len(slots)
    if orientation == "STEP":
        if len(data_lines) != length: raise ValueError(f"STEP data has {len(data_lines)} rows; LENGTH={length}")
        if any(len(row) != slot_count for row in data_lines): raise ValueError(f"every STEP row must contain {slot_count} slot characters")
        steps = [[accent_from_char(ch) for ch in row] for row in data_lines]
    else:
        if len(data_lines) != slot_count: raise ValueError(f"SLOT data has {len(data_lines)} rows; slots={slot_count}")
        if any(len(row) != length for row in data_lines): raise ValueError(f"every SLOT row must contain LENGTH={length} characters")
        steps = [[0] * slot_count for _ in range(length)]
        for slot_index, row in enumerate(data_lines):
            for step_index, ch in enumerate(row): steps[step_index][slot_index] = accent_from_char(ch)
    ppqn = int(metadata.get("PPQN", str(DEFAULT_PPQN)))
    return Pattern(path, name, "ADT v2.3", length, subdiv, steps, slots, slot_map_name, slot_map_id,
                   metadata.get("TIME_SIG"), metadata.get("SOURCE"), ppqn)


def decode_payload(payload: bytes, length: int, slots: int) -> List[List[int]]:
    steps = [[0] * slots for _ in range(length)]; offset = 0
    for step_index in range(length):
        if offset >= len(payload): raise ValueError(f"payload ended before step {step_index}")
        hit_count = payload[offset]; offset += 1
        if offset + hit_count > len(payload): raise ValueError(f"truncated hit list at step {step_index}")
        for _ in range(hit_count):
            hit = payload[offset]; offset += 1
            if hit & 0xC0: raise ValueError(f"step {step_index}: reserved packed-hit bits are not zero")
            slot, accent = (hit >> 2) & 0x0F, hit & 0x03
            if slot >= slots: raise ValueError(f"step {step_index}: slot {slot} outside slot map ({slots})")
            if accent == 0: raise ValueError(f"step {step_index}: stored hit has accent 0")
            steps[step_index][slot] = max(steps[step_index][slot], accent)
    if offset != len(payload): raise ValueError(f"ADP payload has {len(payload) - offset} unused byte(s)")
    return steps


def find_same_basename(path: Path, suffixes: Sequence[str]) -> Optional[Path]:
    for suffix in suffixes:
        candidate = path.with_suffix(suffix)
        if candidate.is_file(): return candidate
    return None


def load_adp3(path: Path, by_name: Dict[str, SlotMapDefinition], by_id: Dict[int, SlotMapDefinition]) -> Pattern:
    data = path.read_bytes()
    if len(data) < ADP3_HEADER_SIZE: raise ValueError("ADP3 file is shorter than the 12-byte header")
    magic, version, subdiv_code, length, slot_map_id, payload_bytes, payload_crc = struct.unpack(ADP3_HEADER_FMT, data[:ADP3_HEADER_SIZE])
    if magic != b"ADP3" or version != 23: raise ValueError("invalid ADP v2.3 header")
    if subdiv_code not in SUBDIV_CODE_TO_STR: raise ValueError(f"unsupported ADP3 SUBDIV code: {subdiv_code}")
    payload = data[ADP3_HEADER_SIZE:]
    if len(payload) != payload_bytes: raise ValueError(f"ADP3 payload length mismatch: header={payload_bytes}, actual={len(payload)}")
    calculated_crc = crc16_ccitt(payload)
    if calculated_crc != payload_crc: raise ValueError(f"ADP3 CRC mismatch: header=0x{payload_crc:04X}, calculated=0x{calculated_crc:04X}")

    companion = None
    companion_path = find_same_basename(path, (".ADT", ".adt"))
    if companion_path is not None: companion = parse_adt_v23(companion_path, by_name)
    if slot_map_id == INLINE_SLOT_MAP_ID:
        if companion is None: raise ValueError(f"INLINE ADP requires companion {path.stem}.ADT")
        if companion.slot_map_name != "INLINE": raise ValueError(f"companion {companion_path.name} must declare SLOT_MAP_ID=INLINE")
        if companion.length != length or companion.subdiv != SUBDIV_CODE_TO_STR[subdiv_code]: raise ValueError("companion ADT LENGTH/SUBDIV does not match ADP3")
        slots, slot_map_name = companion.slots, "INLINE"
    else:
        if slot_map_id not in by_id: raise ValueError(f"unknown registered SLOT_MAP_ID: {slot_map_id}")
        slot_map = by_id[slot_map_id]; slots, slot_map_name = slot_map.slots, slot_map.name
        if companion is not None:
            if companion.length != length or companion.subdiv != SUBDIV_CODE_TO_STR[subdiv_code]: raise ValueError("same-basename ADT LENGTH/SUBDIV does not match ADP3")
            if companion.slot_map_id != slot_map_id: raise ValueError("same-basename ADT SLOT_MAP does not match ADP3")
    return Pattern(path, path.stem.upper(), "ADP v2.3", length, SUBDIV_CODE_TO_STR[subdiv_code],
                   decode_payload(payload, length, len(slots)), slots, slot_map_name, slot_map_id,
                   companion.time_sig if companion else None, companion.source if companion else None,
                   companion.ppqn if companion else DEFAULT_PPQN)


def load_pattern(path: Path, by_name: Dict[str, SlotMapDefinition], by_id: Dict[int, SlotMapDefinition]) -> Pattern:
    if path.suffix.lower() == ".adt": return parse_adt_v23(path, by_name)
    if path.suffix.lower() == ".adp": return load_adp3(path, by_name, by_id)
    raise ValueError("primary input must be ADT or ADP")


def step_ticks(pattern: Pattern) -> int:
    divisor = STEPS_PER_QUARTER[pattern.subdiv]
    if pattern.ppqn % divisor: raise ValueError(f"PPQN={pattern.ppqn} cannot represent SUBDIV={pattern.subdiv}")
    return pattern.ppqn // divisor


def slot_index(pattern: Pattern, token: str) -> int:
    token = token.strip()
    if token.isdigit():
        index = int(token)
        if 0 <= index < pattern.slot_count: return index
        raise ValueError(f"ORN SLOT index outside slots={pattern.slot_count}: {index}")
    matches = [slot.index for slot in pattern.slots if slot.abbrev.upper() == token.upper()]
    if len(matches) != 1: raise ValueError(f"ORN SLOT does not match exactly one slot: {token!r}")
    return matches[0]


def load_orn(path: Path, pattern: Pattern) -> List[OrnamentEvent]:
    raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not raw_lines or raw_lines[0].strip() != "; ORN v1.0": raise ValueError("first line must be exactly '; ORN v1.0'")
    metadata: Dict[str, str] = {}; events: List[OrnamentEvent] = []; in_events = False
    for line_no, raw in enumerate(raw_lines[1:], start=2):
        comment = ""
        if ";" in raw: raw, comment = raw.split(";", 1); comment = comment.strip()
        line = raw.strip()
        if not line: continue
        if line.upper() == "[EVENTS]": in_events = True; continue
        if not in_events:
            if "=" not in line: raise ValueError(f"{path.name}:{line_no}: expected FIELD=VALUE or [EVENTS]")
            key, value = line.split("=", 1); metadata[key.strip().upper()] = value.strip(); continue
        parts = line.split(); kind = parts[0].upper(); fields: Dict[str, str] = {}
        for part in parts[1:]:
            if "=" not in part: raise ValueError(f"{path.name}:{line_no}: malformed ORN field {part!r}")
            key, value = part.split("=", 1); fields[key.upper()] = value
        if kind != "FLAM": raise ValueError(f"{path.name}:{line_no}: ORN v1.0 supports FLAM only")
        try:
            target_step, slot = int(fields["TARGET_STEP"]), slot_index(pattern, fields["SLOT"])
            offset_ticks, velocity = int(fields["OFFSET_TICKS"]), int(fields["VELOCITY"])
        except KeyError as exc: raise ValueError(f"{path.name}:{line_no}: missing field {exc.args[0]}") from exc
        if not 0 <= target_step < pattern.length: raise ValueError(f"{path.name}:{line_no}: TARGET_STEP outside pattern")
        if not 1 <= velocity <= 127: raise ValueError(f"{path.name}:{line_no}: VELOCITY must be 1..127")
        match = re.search(r"\bconfidence\s*=\s*([A-Za-z0-9_-]+)", comment, re.I)
        events.append(OrnamentEvent(kind, target_step, slot, offset_ticks, velocity,
                                     fields.get("LOOP_WRAP", "0").lower() in {"1", "true", "yes"},
                                     match.group(1).upper() if match else None))
    if not in_events: raise ValueError("missing [EVENTS] section")
    if metadata.get("UNIT", "TICK").upper() not in {"TICK", "TICKS"}: raise ValueError("ORN UNIT must be TICK")
    if metadata.get("SUBDIV", pattern.subdiv).upper() != pattern.subdiv: raise ValueError("ORN SUBDIV does not match pattern")
    if int(metadata.get("LENGTH", pattern.length)) != pattern.length: raise ValueError("ORN LENGTH does not match pattern")
    expected_loop_ticks = pattern.length * step_ticks(pattern)
    if int(metadata.get("LOOP_TICKS", expected_loop_ticks)) != expected_loop_ticks: raise ValueError("ORN LOOP_TICKS does not match pattern")
    return events


def split_input_tokens(values: Sequence[str]) -> List[str]:
    tokens: List[str] = []
    for value in values:
        for part in value.split(","):
            item = part.strip().strip('"')
            if item: tokens.append(item)
    return tokens


def resolve_orn_primary(path: Path) -> Path:
    for suffix in (".ADP", ".adp", ".ADT", ".adt"):
        candidate = path.with_suffix(suffix)
        if candidate.is_file(): return candidate
    raise ValueError(f"{path.name}: no same-basename ADP or ADT found")


def iter_directory(directory: Path, recursive: bool) -> Iterable[Path]:
    iterator = directory.rglob("*") if recursive else directory.glob("*")
    for path in sorted(iterator, key=lambda item: str(item).casefold()):
        if path.is_file() and path.suffix.lower() in {".adt", ".adp"}: yield path


def collect_primary_paths(tokens: Sequence[str], recursive: bool) -> List[Path]:
    candidates: List[Path] = []
    for token in tokens:
        path = Path(token).expanduser()
        if not path.exists(): raise ValueError(f"input not found: {path}")
        if path.is_dir(): candidates.extend(iter_directory(path, recursive))
        elif path.suffix.lower() == ".orn": candidates.append(resolve_orn_primary(path))
        elif path.suffix.lower() in {".adt", ".adp"}: candidates.append(path)
        else: raise ValueError(f"unsupported input: {path}")
    selected: Dict[Tuple[str, str], Path] = {}
    for path in candidates:
        key = (str(path.parent.resolve()).casefold(), path.stem.casefold())
        previous = selected.get(key)
        if previous is None or (previous.suffix.lower() == ".adt" and path.suffix.lower() == ".adp"):
            selected[key] = path
    return sorted(selected.values(), key=lambda item: (item.stem.casefold(), str(item).casefold()))


def find_orn(path: Path) -> Optional[Path]: return find_same_basename(path, (".ORN", ".orn"))
def esc(value: object) -> str: return html.escape(str(value), quote=True)
def svg_text(x: float, y: float, value: str, cls: str = "", anchor: str = "start") -> str:
    return f'<text x="{x:.2f}" y="{y:.2f}" class="{cls}" text-anchor="{anchor}">{esc(value)}</text>'


def parse_time_sig(value: Optional[str]) -> Optional[Tuple[int, int]]:
    if not value: return None
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", value)
    if not match: return None
    n, d = int(match.group(1)), int(match.group(2))
    return (n, d) if n > 0 and d > 0 else None


def card_dimensions(pattern: Pattern) -> Tuple[int, int]:
    return max(430, 128 + pattern.length * 21), max(290, 116 + pattern.slot_count * 20)


def render_card(pattern: Pattern, x: float, y: float, width: int, height: int) -> str:
    left, top, bottom, right = 104, 64, 30, 10
    gx, gy, gw, gh = x + left, y + top, width - left - right, height - top - bottom
    cell_w, row_h = gw / pattern.length, gh / pattern.slot_count
    p = [f'<g class="card"><rect x="{x}" y="{y}" width="{width}" height="{height}" rx="8" class="panel"/>']
    p += [svg_text(x + 12, y + 22, pattern.name, "title"), svg_text(x + width - 12, y + 22, pattern.source_format, "format", "end")]
    meta = f"{pattern.subdiv} · LENGTH {pattern.length} · {pattern.slot_map_name}"
    if pattern.time_sig: meta = f"{pattern.time_sig} · " + meta
    p += [svg_text(x + 12, y + 42, meta, "meta")]
    hits = sum(1 for row in pattern.steps for accent in row if accent)
    p += [svg_text(x + width - 12, y + 42, f"{hits} hits" + (f" · ORN {len(pattern.ornaments)}" if pattern.ornaments else ""), "meta", "end")]

    major_every = STEPS_PER_QUARTER[pattern.subdiv]
    for step in range(pattern.length + 1):
        xx = gx + step * cell_w; cls = "guide major" if step % major_every == 0 else "guide"
        p.append(f'<line x1="{xx:.2f}" y1="{gy}" x2="{xx:.2f}" y2="{gy + gh}" class="{cls}"/>')
    ts = parse_time_sig(pattern.time_sig)
    if ts:
        n, d = ts; bar_steps = n * 4 * major_every / d
        if bar_steps > 0 and math.isclose(bar_steps, round(bar_steps), abs_tol=1e-9):
            every = int(round(bar_steps))
            for step in range(0, pattern.length + 1, every):
                xx = gx + step * cell_w
                p.append(f'<line x1="{xx:.2f}" y1="{gy - 4}" x2="{xx:.2f}" y2="{gy + gh}" class="barline"/>')

    display_slots = list(range(pattern.slot_count - 1, -1, -1)); display_row = {slot: row for row, slot in enumerate(display_slots)}
    for row, slot_index_ in enumerate(display_slots):
        slot = pattern.slots[slot_index_]; yy = gy + row * row_h
        p += [svg_text(x + 9, yy + row_h * .68, f"{slot_index_:02d} {slot.abbrev}", "row"),
              f'<line x1="{gx}" y1="{yy + row_h:.2f}" x2="{gx + gw}" y2="{yy + row_h:.2f}" class="rguide"/>']

    orn_map: Dict[Tuple[int, int], List[OrnamentEvent]] = {}
    for event in pattern.ornaments: orn_map.setdefault((event.target_step, event.slot), []).append(event)
    for step_index, row in enumerate(pattern.steps):
        for slot_index_, accent in enumerate(row):
            if not accent: continue
            yy = gy + display_row[slot_index_] * row_h; xx = gx + step_index * cell_w
            tooltip = f"step {step_index}; slot {slot_index_} {pattern.slots[slot_index_].abbrev}; accent {accent}"
            p.append(f'<rect x="{xx + .6:.2f}" y="{yy + .6:.2f}" width="{max(.6, cell_w - 1.2):.2f}" height="{max(.6, row_h - 1.2):.2f}" rx="1.2" class="cell accent{accent}"><title>{esc(tooltip)}</title></rect>')
            events = orn_map.get((step_index, slot_index_), [])
            if events:
                marker = max(4., min(8., cell_w * .25, row_h * .35)); details = "; ".join(
                    f"{e.kind} offset {e.offset_ticks}, velocity {e.velocity}" + (" loop-wrap" if e.loop_wrap else "") + (f", confidence {e.confidence}" if e.confidence else "") for e in events)
                p.append(f'<rect x="{xx + 2.2:.2f}" y="{yy + 2.2:.2f}" width="{marker:.2f}" height="{marker:.2f}" rx=".7" class="ornmark"><title>{esc(details)}</title></rect>')
    if pattern.source: p.append(svg_text(x + 10, y + height - 10, pattern.source, "footer"))
    elif pattern.ornaments:
        wraps = sum(1 for event in pattern.ornaments if event.loop_wrap)
        p.append(svg_text(x + 10, y + height - 10, f"ORN events {len(pattern.ornaments)}" + (f" · loop-wrap {wraps}" if wraps else ""), "footer"))
    p.append("</g>"); return "".join(p)


def render_html(patterns: Sequence[Pattern], title: str) -> str:
    margin, gap_x, gap_y = 18, 18, 18; columns = 2 if len(patterns) > 1 else 1
    sizes = [card_dimensions(p) for p in patterns]; col_widths = [0] * columns
    for i, (w, _) in enumerate(sizes): col_widths[i % columns] = max(col_widths[i % columns], w)
    row_heights = [max(h for _, h in sizes[start:start + columns]) for start in range(0, len(patterns), columns)]
    xs, cursor = [], margin
    for w in col_widths: xs.append(cursor); cursor += w + gap_x
    ys, cursor = [], margin
    for h in row_heights: ys.append(cursor); cursor += h + gap_y
    svg_w = margin * 2 + sum(col_widths) + gap_x * max(0, columns - 1)
    svg_h = margin * 2 + sum(row_heights) + gap_y * max(0, len(row_heights) - 1)
    cards = [render_card(p, xs[i % columns], ys[i // columns], *sizes[i]) for i, p in enumerate(patterns)]
    hit_count = sum(sum(1 for a in row if a) for p in patterns for row in p.steps)
    orn_count = sum(len(p.ornaments) for p in patterns)
    safe_svg_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", title) + ".svg"
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} — ADX Viewer</title><style>
:root{{--bg:#f4f6f8;--panel:#fff;--ink:#17202a;--muted:#65717e;--line:#d9dee4;--major:#9aa6b2;--a1:rgb(70,130,255);--a2:rgb(55,170,95);--a3:rgb(220,55,55)}}@media(prefers-color-scheme:dark){{:root{{--bg:#11151a;--panel:#1a2027;--ink:#e6edf3;--muted:#9da9b5;--line:#303843;--major:#66717d;--a1:rgb(70,130,255);--a2:rgb(55,170,95);--a3:rgb(220,55,55)}}}}*{{box-sizing:border-box}}body{{margin:0;font-family:system-ui,sans-serif;background:var(--bg);color:var(--ink)}}header{{position:sticky;top:0;z-index:3;padding:13px 18px;background:var(--panel);border-bottom:1px solid var(--line)}}h1{{margin:0 0 5px;font-size:20px}}.summary{{color:var(--muted);font-size:13px}}button{{margin-top:8px;padding:7px 11px;border:1px solid var(--line);border-radius:7px;background:var(--panel);color:var(--ink);font-weight:700;cursor:pointer}}.legend{{margin-left:12px;color:var(--muted);font-size:12px}}.swatch{{display:inline-block;width:12px;height:12px;margin:0 3px 0 7px;vertical-align:-2px;border:1px solid var(--line)}}.s1{{background:var(--a1)}}.s2{{background:var(--a2)}}.s3{{background:var(--a3)}}.flam{{position:relative;background:var(--a2)}}.flam::after{{content:"";position:absolute;left:1px;top:1px;width:4px;height:4px;background:#fff}}main{{overflow:auto;padding:12px}}svg{{display:block}}.panel{{fill:var(--panel);stroke:var(--line)}}.title{{fill:var(--ink);font-size:18px;font-weight:800}}.format{{fill:var(--muted);font-size:13px;font-weight:700}}.meta,.footer{{fill:var(--muted);font-size:12px}}.row{{fill:var(--ink);font-size:11px}}.guide,.rguide{{stroke:var(--line);stroke-width:.7}}.major{{stroke:var(--major);stroke-width:1.45}}.barline{{stroke:var(--ink);stroke-width:2.1;opacity:.72}}.cell{{stroke:var(--panel);stroke-width:.35}}.accent1{{fill:var(--a1)}}.accent2{{fill:var(--a2)}}.accent3{{fill:var(--a3)}}.ornmark{{fill:#fff;stroke:#5b6470;stroke-width:.55}}@media print{{header{{position:static}}button{{display:none}}main{{padding:0}}}}
</style></head><body><header><h1>{esc(title)} — ADX Viewer <small style="font-size:12px;color:var(--muted)">{VERSION}</small></h1><div class="summary">{len(patterns)} pattern(s) · {hit_count} hits · {orn_count} ornament event(s)</div><button id="download-svg">Download SVG</button> <button id="print">Print / PDF</button><span class="legend">Accent:<i class="swatch s1"></i>1 weak<i class="swatch s2"></i>2 medium<i class="swatch s3"></i>3 strong<i class="swatch flam"></i>flam</span></header><main><svg id="catalog" xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}">{''.join(cards)}</svg></main><script>(()=>{{const svg=document.getElementById('catalog');document.getElementById('download-svg').addEventListener('click',()=>{{const clone=svg.cloneNode(true);clone.setAttribute('xmlns','http://www.w3.org/2000/svg');const defs=document.createElementNS('http://www.w3.org/2000/svg','defs');const st=document.createElementNS('http://www.w3.org/2000/svg','style');st.textContent=document.querySelector('style').textContent;defs.appendChild(st);clone.insertBefore(defs,clone.firstChild);const blob=new Blob([new XMLSerializer().serializeToString(clone)],{{type:'image/svg+xml;charset=utf-8'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download={json.dumps(safe_svg_name)};document.body.appendChild(a);a.click();setTimeout(()=>{{URL.revokeObjectURL(a.href);a.remove()}},0)}});document.getElementById('print').addEventListener('click',()=>window.print())}})();</script></body></html>'''


def default_output(tokens: Sequence[str], primary_paths: Sequence[Path]) -> Path:
    if len(primary_paths) == 1 and len(tokens) == 1 and Path(tokens[0]).is_file():
        path = primary_paths[0]; return path.with_name(path.stem + "_adx-viewer.html")
    if len(tokens) == 1 and Path(tokens[0]).is_dir(): return Path(tokens[0]) / "adx_catalog.html"
    return Path("adx_catalog.html")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=SCRIPT_NAME, description="Render ADT/ADP patterns and optional ORN sidecars as HTML/SVG.")
    parser.add_argument("inputs", nargs="+", help="ADT/ADP/ORN files or directories; multiple values may also be comma-separated")
    parser.add_argument("-o", "--output", type=Path, help="output HTML path")
    parser.add_argument("--slot-maps", type=Path, help="slot_map_definitions.json (default: beside this script)")
    parser.add_argument("--recursive", action="store_true", help="scan input directories recursively")
    parser.add_argument("--strict", action="store_true", help="stop on the first invalid pattern instead of skipping it")
    parser.add_argument("--version", action="version", version=VERSION_TEXT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv); tokens = split_input_tokens(args.inputs)
    if not tokens: print("[ERROR] no input was provided", file=sys.stderr); return 2
    slot_map_path = args.slot_maps or Path(__file__).with_name("slot_map_definitions.json")
    try:
        by_name, by_id = load_slot_maps(slot_map_path); primary_paths = collect_primary_paths(tokens, args.recursive)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr); return 2
    if not primary_paths: print("[ERROR] no ADT or ADP files found", file=sys.stderr); return 2
    patterns: List[Pattern] = []; skipped = 0
    for path in primary_paths:
        try:
            pattern = load_pattern(path, by_name, by_id); orn_path = find_orn(path)
            if orn_path is not None: pattern.ornaments = load_orn(orn_path, pattern)
            patterns.append(pattern); print(f"[OK] {path}" + (f" + {orn_path.name}" if orn_path else ""))
        except (OSError, ValueError, struct.error) as exc:
            skipped += 1; print(f"[SKIP] {path}: {exc}", file=sys.stderr)
            if args.strict: return 1
    if not patterns: print("[ERROR] no valid patterns to render", file=sys.stderr); return 1
    output = args.output or default_output(tokens, primary_paths); output.parent.mkdir(parents=True, exist_ok=True)
    title = patterns[0].name if len(patterns) == 1 else "ADX Pattern Catalog"
    output.write_text(render_html(patterns, title), encoding="utf-8")
    print(VERSION_TEXT); print(f"[DONE] output={output}"); print(f"[DONE] rendered={len(patterns)}, skipped={skipped}")
    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
