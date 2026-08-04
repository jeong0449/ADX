#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adc-midi-inspector.py

Lightweight Standard MIDI File inspector for the ADX Toolkit.

Default behavior is read-only:
- SMF summary, including tempo/time-signature/key-signature/marker maps
- channel/program summary
- drum-channel summary

Optional:
- --dump
- --dump-drums
- --write-type0
- --extract-drums
- --write-drum-roll

Created: 2026-08-02
Version: 260805a
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from mido import Message, MetaMessage, MidiFile, MidiTrack, merge_tracks, tempo2bpm


SCRIPT_NAME = "adc-midi-inspector.py"
VERSION = "260805b"
VERSION_TEXT = f"{SCRIPT_NAME} {VERSION}"

DRUM_CHANNEL = 9  # MIDI channel 10, zero-based

GM_PROGRAM_NAMES = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
    "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2", "Harpsichord",
    "Clavinet", "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ",
    "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ", "Accordion",
    "Harmonica", "Tango Accordion", "Acoustic Guitar (nylon)",
    "Acoustic Guitar (steel)", "Electric Guitar (jazz)",
    "Electric Guitar (clean)", "Electric Guitar (muted)", "Overdriven Guitar",
    "Distortion Guitar", "Guitar Harmonics", "Acoustic Bass",
    "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2", "Violin",
    "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings",
    "Orchestral Harp", "Timpani", "String Ensemble 1", "String Ensemble 2",
    "SynthStrings 1", "SynthStrings 2", "Choir Aahs", "Voice Oohs", "Synth Voice",
    "Orchestra Hit", "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn",
    "Brass Section", "SynthBrass 1", "SynthBrass 2", "Soprano Sax", "Alto Sax",
    "Tenor Sax", "Baritone Sax", "Oboe", "English Horn", "Bassoon", "Clarinet",
    "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi",
    "Whistle", "Ocarina", "Lead 1 (square)", "Lead 2 (sawtooth)",
    "Lead 3 (calliope)", "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)",
    "Lead 7 (fifths)", "Lead 8 (bass + lead)", "Pad 1 (new age)", "Pad 2 (warm)",
    "Pad 3 (polysynth)", "Pad 4 (choir)", "Pad 5 (bowed)", "Pad 6 (metallic)",
    "Pad 7 (halo)", "Pad 8 (sweep)", "FX 1 (rain)", "FX 2 (soundtrack)",
    "FX 3 (crystal)", "FX 4 (atmosphere)", "FX 5 (brightness)", "FX 6 (goblins)",
    "FX 7 (echoes)", "FX 8 (sci-fi)", "Sitar", "Banjo", "Shamisen", "Koto",
    "Kalimba", "Bag Pipe", "Fiddle", "Shanai", "Tinkle Bell", "Agogo",
    "Steel Drums", "Woodblock", "Taiko Drum", "Melodic Tom", "Synth Drum",
    "Reverse Cymbal", "Guitar Fret Noise", "Breath Noise", "Seashore",
    "Bird Tweet", "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]

GM_DRUM_NAMES: Dict[int, str] = {
    35: "Acoustic Bass Drum", 36: "Bass Drum 1", 37: "Side Stick",
    38: "Acoustic Snare", 39: "Hand Clap", 40: "Electric Snare",
    41: "Low Floor Tom", 42: "Closed Hi-Hat", 43: "High Floor Tom",
    44: "Pedal Hi-Hat", 45: "Low Tom", 46: "Open Hi-Hat",
    47: "Low-Mid Tom", 48: "Hi-Mid Tom", 49: "Crash Cymbal 1",
    50: "High Tom", 51: "Ride Cymbal 1", 52: "Chinese Cymbal",
    53: "Ride Bell", 54: "Tambourine", 55: "Splash Cymbal",
    56: "Cowbell", 57: "Crash Cymbal 2", 58: "Vibraslap",
    59: "Ride Cymbal 2", 60: "Hi Bongo", 61: "Low Bongo",
    62: "Mute Hi Conga", 63: "Open Hi Conga", 64: "Low Conga",
    65: "High Timbale", 66: "Low Timbale", 67: "High Agogo",
    68: "Low Agogo", 69: "Cabasa", 70: "Maracas", 71: "Short Whistle",
    72: "Long Whistle", 73: "Short Guiro", 74: "Long Guiro",
    75: "Claves", 76: "Hi Wood Block", 77: "Low Wood Block",
    78: "Mute Cuica", 79: "Open Cuica", 80: "Mute Triangle",
    81: "Open Triangle",
}



@dataclass(frozen=True)
class SlotDefinition:
    slot: int
    abbrev: str
    representative_midi: int
    midi_input_allowed: Tuple[int, ...]


@dataclass(frozen=True)
class SlotMapDefinition:
    slot_map_id: int
    name: str
    slots: Tuple[SlotDefinition, ...]

    @property
    def accepted_notes(self) -> set[int]:
        result: set[int] = set()
        for slot in self.slots:
            result.update(slot.midi_input_allowed)
        return result


def load_slot_maps(path: Path) -> Tuple[SlotMapDefinition, ...]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"slot-map definition not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load slot-map definition {path}: {exc}") from exc

    if not isinstance(data, list) or not data:
        raise ValueError("slot-map JSON root must be a non-empty array")

    maps: List[SlotMapDefinition] = []
    for row in data:
        if not isinstance(row, dict):
            raise ValueError("each slot map must be an object")
        map_id = row.get("slot_map_id")
        name = row.get("name")
        slots_data = row.get("slots")
        if not isinstance(map_id, int) or not isinstance(name, str):
            raise ValueError("invalid slot-map id or name")
        if not isinstance(slots_data, list) or not slots_data:
            raise ValueError(f"{name}: slots must be a non-empty array")

        slots: List[SlotDefinition] = []
        for item in slots_data:
            slot_no = item.get("slot")
            abbrev = item.get("abbrev")
            representative = item.get("representative_midi")
            allowed = item.get("midi_input_allowed")
            if (
                not isinstance(slot_no, int)
                or not isinstance(abbrev, str)
                or not isinstance(representative, int)
                or not isinstance(allowed, list)
                or not allowed
                or any(not isinstance(n, int) for n in allowed)
            ):
                raise ValueError(f"{name}: invalid slot definition")
            slots.append(
                SlotDefinition(
                    slot=slot_no,
                    abbrev=abbrev,
                    representative_midi=representative,
                    midi_input_allowed=tuple(allowed),
                )
            )
        slots.sort(key=lambda slot: slot.slot)
        maps.append(SlotMapDefinition(map_id, name, tuple(slots)))

    maps.sort(key=lambda item: item.slot_map_id)
    return tuple(maps)


def choose_slot_map(
    maps: Sequence[SlotMapDefinition],
    used_notes: set[int],
) -> SlotMapDefinition:
    """Choose the most appropriate slot map for the notes actually used.

    Policy:
    1. Prefer LEGACY whenever it can represent every used note.
    2. Otherwise, among maps that fully cover the notes, choose the map with
       the fewest unused accepted notes, then the lowest slot-map ID.
    3. If no map fully covers the notes, minimize uncovered notes first.
    """
    legacy = next((item for item in maps if item.name.upper() == "LEGACY"), None)
    if legacy is not None and used_notes <= legacy.accepted_notes:
        return legacy

    compatible = [item for item in maps if used_notes <= item.accepted_notes]
    if compatible:
        return min(
            compatible,
            key=lambda item: (
                len(item.accepted_notes - used_notes),
                item.slot_map_id,
            ),
        )

    return min(
        maps,
        key=lambda item: (
            len(used_notes - item.accepted_notes),
            len(item.accepted_notes - used_notes),
            item.slot_map_id,
        ),
    )


@dataclass(frozen=True)
class TimedMessage:
    tick: int
    track: int
    order: int
    message: Message | MetaMessage


@dataclass(frozen=True)
class TimeSignaturePoint:
    tick: int
    numerator: int
    denominator: int
    measure: int


@dataclass(frozen=True)
class PairedNote:
    start_tick: int
    end_tick: int
    duration: int
    track: int
    channel: int
    note: int
    velocity: int


def collect_timed_messages(mid: MidiFile) -> List[TimedMessage]:
    rows: List[TimedMessage] = []
    order = 0
    for track_index, track in enumerate(mid.tracks):
        tick = 0
        for msg in track:
            tick += int(msg.time)
            rows.append(TimedMessage(tick, track_index, order, msg))
            order += 1
    rows.sort(key=lambda row: (row.tick, row.track, row.order))
    return rows


def build_time_signature_map(
    timed: Sequence[TimedMessage],
    tpq: int,
) -> List[TimeSignaturePoint]:
    changes = [(0, 4, 4)]
    for row in timed:
        msg = row.message
        if isinstance(msg, MetaMessage) and msg.type == "time_signature":
            changes.append((row.tick, int(msg.numerator), int(msg.denominator)))

    merged: Dict[int, Tuple[int, int]] = {}
    for tick, num, den in changes:
        merged[tick] = (num, den)

    ordered = sorted((tick, num, den) for tick, (num, den) in merged.items())
    points: List[TimeSignaturePoint] = []
    measure = 1
    previous_tick = ordered[0][0]
    previous_num = ordered[0][1]
    previous_den = ordered[0][2]
    points.append(TimeSignaturePoint(previous_tick, previous_num, previous_den, measure))

    for tick, num, den in ordered[1:]:
        bar_ticks = max(1, round(tpq * previous_num * 4 / previous_den))
        elapsed = max(0, tick - previous_tick)
        completed = elapsed // bar_ticks
        if elapsed % bar_ticks:
            completed += 1
        measure += completed
        points.append(TimeSignaturePoint(tick, num, den, measure))
        previous_tick, previous_num, previous_den = tick, num, den

    return points


def musical_position(
    tick: int,
    tpq: int,
    time_signatures: Sequence[TimeSignaturePoint],
) -> str:
    point = time_signatures[0]
    for candidate in time_signatures:
        if candidate.tick <= tick:
            point = candidate
        else:
            break

    beat_ticks = tpq * 4 / point.denominator
    bar_ticks = beat_ticks * point.numerator
    relative = max(0, tick - point.tick)
    bar_offset = int(relative // bar_ticks)
    within_bar = relative - bar_offset * bar_ticks
    beat_index = int(within_bar // beat_ticks)
    within_beat = within_bar - beat_index * beat_ticks
    tick_in_beat = int(round(within_beat))
    return f"{point.measure + bar_offset}:{beat_index + 1}:{tick_in_beat:03d}"


def pair_notes(timed: Sequence[TimedMessage]) -> List[PairedNote]:
    active: Dict[Tuple[int, int, int], List[Tuple[int, int]]] = defaultdict(list)
    notes: List[PairedNote] = []

    for row in timed:
        msg = row.message
        if not isinstance(msg, Message):
            continue
        if msg.type == "note_on" and msg.velocity > 0:
            active[(row.track, msg.channel, msg.note)].append((row.tick, msg.velocity))
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            key = (row.track, msg.channel, msg.note)
            if active[key]:
                start, velocity = active[key].pop(0)
                notes.append(
                    PairedNote(
                        start_tick=start,
                        end_tick=row.tick,
                        duration=max(0, row.tick - start),
                        track=row.track,
                        channel=msg.channel,
                        note=msg.note,
                        velocity=velocity,
                    )
                )

    for (track, channel, note), starts in active.items():
        for start, velocity in starts:
            notes.append(
                PairedNote(start, start, 0, track, channel, note, velocity)
            )

    notes.sort(key=lambda note: (note.start_tick, note.track, note.channel, note.note))
    return notes


def format_division(mid: MidiFile) -> str:
    tpq = int(mid.ticks_per_beat)
    if tpq > 0:
        return f"PPQN {tpq}"
    return f"SMPTE/raw division {tpq} (unsupported by ADX rhythm tools)"


def program_name(program: int) -> str:
    if 0 <= program < len(GM_PROGRAM_NAMES):
        return GM_PROGRAM_NAMES[program]
    return "Unknown GM program"


def unique_tempos(timed: Sequence[TimedMessage]) -> List[Tuple[int, float]]:
    result = []
    for row in timed:
        msg = row.message
        if isinstance(msg, MetaMessage) and msg.type == "set_tempo":
            result.append((row.tick, tempo2bpm(msg.tempo)))
    return result


def unique_time_signatures(timed: Sequence[TimedMessage]) -> List[Tuple[int, int, int]]:
    result = []
    for row in timed:
        msg = row.message
        if isinstance(msg, MetaMessage) and msg.type == "time_signature":
            result.append((row.tick, int(msg.numerator), int(msg.denominator)))
    return result


def unique_key_signatures(timed: Sequence[TimedMessage]) -> List[Tuple[int, str]]:
    result: List[Tuple[int, str]] = []
    for row in timed:
        msg = row.message
        if isinstance(msg, MetaMessage) and msg.type == "key_signature":
            result.append((row.tick, str(msg.key)))
    return result


def marker_events(timed: Sequence[TimedMessage]) -> List[Tuple[int, str, str]]:
    result: List[Tuple[int, str, str]] = []
    for row in timed:
        msg = row.message
        if not isinstance(msg, MetaMessage):
            continue
        if msg.type in {"marker", "cue_marker"}:
            result.append((row.tick, msg.type, str(msg.text)))
    return result


def print_meta_map(
    title: str,
    rows: Sequence[Tuple[str, str]],
    empty_text: str,
) -> None:
    print(f"\n{title}")
    print("-" * 72)
    if not rows:
        print(empty_text)
        return
    for position, value in rows:
        print(f"{position:<14} {value}")


def print_smf_summary(
    path: Path,
    mid: MidiFile,
    timed: Sequence[TimedMessage],
    ts_map: Sequence[TimeSignaturePoint],
) -> None:
    tempos = unique_tempos(timed)
    time_sigs = unique_time_signatures(timed)
    key_sigs = unique_key_signatures(timed)
    markers = marker_events(timed)

    print("=" * 72)
    print(f"File          : {path.name}")
    print(f"Format        : {mid.type}")
    print(f"Tracks        : {len(mid.tracks)}")
    print(f"Division      : {format_division(mid)}")
    try:
        print(f"Duration      : {mid.length:.3f} sec")
    except Exception:
        print("Duration      : unavailable")

    tempo_rows = [
        (
            musical_position(tick, mid.ticks_per_beat, ts_map),
            f"{bpm:.3f} BPM",
        )
        for tick, bpm in tempos
    ]
    print_meta_map(
        "Tempo Map",
        tempo_rows,
        "not specified (120 BPM is the Standard MIDI default)",
    )

    time_signature_rows = [
        (
            musical_position(tick, mid.ticks_per_beat, ts_map),
            f"{numerator}/{denominator}",
        )
        for tick, numerator, denominator in time_sigs
    ]
    print_meta_map(
        "Time Signature Map",
        time_signature_rows,
        "not specified (4/4 fallback is used for musical positions)",
    )

    key_signature_rows = [
        (
            musical_position(tick, mid.ticks_per_beat, ts_map),
            key,
        )
        for tick, key in key_sigs
    ]
    print_meta_map(
        "Key Signature Map",
        key_signature_rows,
        "not specified",
    )

    marker_rows = [
        (
            musical_position(tick, mid.ticks_per_beat, ts_map),
            f"{text_value}" if kind == "marker" else f"[cue] {text_value}",
        )
        for tick, kind, text_value in markers
    ]
    print_meta_map(
        "Markers",
        marker_rows,
        "none",
    )

def print_channel_summary(
    timed: Sequence[TimedMessage],
    tpq: int,
    ts_map: Sequence[TimeSignaturePoint],
) -> None:
    used_channels = set()
    event_counts: Counter[int] = Counter()
    program_changes: Dict[int, List[Tuple[int, int]]] = defaultdict(list)

    for row in timed:
        msg = row.message
        if not isinstance(msg, Message):
            continue
        if hasattr(msg, "channel"):
            used_channels.add(msg.channel)
            event_counts[msg.channel] += 1
        if msg.type == "program_change":
            program_changes[msg.channel].append((row.tick, msg.program))

    print("\nChannel Summary")
    print("-" * 72)
    if not used_channels:
        print("(no channel messages)")
        return

    for channel in sorted(used_channels):
        label = f"CH{channel + 1:02d}"
        print(f"{label}  events={event_counts[channel]}")
        if channel == DRUM_CHANNEL:
            print("      General MIDI percussion channel")
        changes = program_changes.get(channel, [])
        if not changes:
            if channel != DRUM_CHANNEL:
                print("      Program: not specified")
            continue
        for tick, program in changes:
            pos = musical_position(tick, tpq, ts_map)
            suffix = " (percussion channel)" if channel == DRUM_CHANNEL else ""
            print(
                f"      {pos}  Program {program + 1:03d}  "
                f"{program_name(program)}{suffix}"
            )


def print_drum_summary(notes: Sequence[PairedNote]) -> None:
    drum_notes = [note for note in notes if note.channel == DRUM_CHANNEL]
    print("\nDrum Summary")
    print("-" * 72)
    if not drum_notes:
        print("CH10 drum events: none")
        return

    counts = Counter(note.note for note in drum_notes)
    print(f"CH10 drum events: {len(drum_notes)} note-on events")
    for note_number in sorted(counts):
        name = GM_DRUM_NAMES.get(note_number, "Unknown / non-GM note")
        print(f"{note_number:3d}  {name:<24} count={counts[note_number]}")
    unknown = sorted(note for note in counts if note not in GM_DRUM_NAMES)
    print("Unknown notes :", ", ".join(map(str, unknown)) if unknown else "none")


def dump_paired_notes(
    notes: Sequence[PairedNote],
    tpq: int,
    ts_map: Sequence[TimeSignaturePoint],
    drums_only: bool,
) -> None:
    selected = [
        note for note in notes
        if not drums_only or note.channel == DRUM_CHANNEL
    ]

    title = "Drum Event Dump" if drums_only else "Note Event Dump"
    print(f"\n{title}")
    print("-" * 96)
    print(
        f"{'POSITION':>13} {'DUR':>6} {'TRK':>4} {'CH':>4} "
        f"{'NOTE':>5} {'VELOCITY':>8}  NAME"
    )
    print("-" * 96)

    for note in selected:
        position = musical_position(note.start_tick, tpq, ts_map)
        if note.channel == DRUM_CHANNEL:
            name = GM_DRUM_NAMES.get(note.note, "Unknown / non-GM drum note")
        else:
            name = ""
        print(
            f"{position:>13} {note.duration:6d} {note.track + 1:4d} "
            f"{note.channel + 1:4d} {note.note:5d} {note.velocity:8d}  {name}"
        )



def midi_title(path: Path, timed: Sequence[TimedMessage]) -> str:
    for row in timed:
        msg = row.message
        if isinstance(msg, MetaMessage) and msg.type == "track_name":
            title = str(msg.name).strip()
            if title:
                return title
    return path.stem


def map_note_order(
    used_notes: set[int],
    slot_map: SlotMapDefinition,
) -> List[int]:
    ordered: List[int] = []
    for slot in reversed(slot_map.slots):
        for note in sorted(
            (n for n in slot.midi_input_allowed if n in used_notes),
            reverse=True,
        ):
            if note not in ordered:
                ordered.append(note)
    for note in sorted(used_notes - set(ordered), reverse=True):
        ordered.append(note)
    return ordered


def bar_spans(
    max_tick: int,
    tpq: int,
    ts_map: Sequence[TimeSignaturePoint],
) -> List[Tuple[int, int, int, int, int]]:
    """Return (measure, start, end, numerator, denominator)."""
    spans: List[Tuple[int, int, int, int, int]] = []
    measure = 1
    tick = 0
    sig_index = 0
    current = ts_map[0]

    while tick <= max_tick:
        while (
            sig_index + 1 < len(ts_map)
            and ts_map[sig_index + 1].tick <= tick
        ):
            sig_index += 1
            current = ts_map[sig_index]
            measure = current.measure

        bar_ticks = max(
            1,
            round(tpq * current.numerator * 4 / current.denominator),
        )
        next_change = (
            ts_map[sig_index + 1].tick
            if sig_index + 1 < len(ts_map)
            else None
        )
        end = tick + bar_ticks
        if next_change is not None and tick < next_change < end:
            end = next_change

        spans.append(
            (measure, tick, end, current.numerator, current.denominator)
        )
        tick = end
        measure += 1

    return spans


def write_drum_roll_html(
    source: Path,
    mid: MidiFile,
    timed: Sequence[TimedMessage],
    notes: Sequence[PairedNote],
    ts_map: Sequence[TimeSignaturePoint],
    slot_maps: Sequence[SlotMapDefinition],
    bars_per_row: int,
    show_all_notes: bool,
    output_dir: Optional[Path],
) -> Path:
    drum_notes = [note for note in notes if note.channel == DRUM_CHANNEL]
    if not drum_notes:
        raise ValueError("cannot create drum roll: no CH10 notes")

    used_notes = {note.note for note in drum_notes}
    slot_map = choose_slot_map(slot_maps, used_notes)
    global_note_order = map_note_order(used_notes, slot_map)
    max_tick = max(note.end_tick for note in drum_notes)
    spans = bar_spans(max_tick, mid.ticks_per_beat, ts_map)
    rows = [
        spans[index:index + bars_per_row]
        for index in range(0, len(spans), bars_per_row)
    ]

    label_width = 160
    bar_width = 112
    row_height = 19
    header_height = 54
    footer_height = 24
    system_gap = 26

    # Compact mode is the default: each system displays only instruments that
    # actually occur inside that system. --show-all-notes restores a stable
    # file-wide row set for every system.
    system_layouts = []
    next_origin_y = 76
    for system_bars in rows:
        system_start = system_bars[0][1]
        system_end = system_bars[-1][2]
        if show_all_notes:
            system_note_order = list(global_note_order)
        else:
            system_used_notes = {
                note.note
                for note in drum_notes
                if system_start <= note.start_tick < system_end
            }
            system_note_order = map_note_order(system_used_notes, slot_map)
        if not system_note_order:
            system_note_order = list(global_note_order[:1])

        plot_height = max(1, len(system_note_order)) * row_height
        system_height = header_height + plot_height + footer_height
        system_layouts.append(
            {
                "bars": system_bars,
                "notes": system_note_order,
                "origin_y": next_origin_y,
                "plot_height": plot_height,
                "system_height": system_height,
            }
        )
        next_origin_y += system_height + system_gap

    svg_width = label_width + bars_per_row * bar_width + 34
    svg_height = next_origin_y

    title = midi_title(source, timed)
    subtitle = (
        f"CH10 Drum Piano Roll · {slot_map.name} "
        f"(SLOT_MAP_ID={slot_map.slot_map_id}) · PPQN {mid.ticks_per_beat} · "
        f"{VERSION_TEXT}"
        + (" · fixed rows" if show_all_notes else " · compact rows")
    )

    svg: List[str] = []
    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {svg_width} {svg_height}" '
        f'width="{svg_width}" height="{svg_height}" role="img">'
    )
    svg.append(
        f'<text x="{svg_width / 2:.1f}" y="30" class="score-title" '
        f'text-anchor="middle">{html.escape(title)}</text>'
    )
    svg.append(
        f'<text x="{svg_width / 2:.1f}" y="51" class="score-subtitle" '
        f'text-anchor="middle">{html.escape(subtitle)}</text>'
    )

    drum_by_start: Dict[int, List[PairedNote]] = defaultdict(list)
    for note in drum_notes:
        drum_by_start[note.start_tick].append(note)

    for system_index, layout in enumerate(system_layouts):
        system_bars = layout["bars"]
        note_order = layout["notes"]
        origin_y = layout["origin_y"]
        plot_height = layout["plot_height"]
        plot_top = origin_y + header_height
        plot_left = label_width
        actual_bars = len(system_bars)
        plot_width = actual_bars * bar_width
        note_index = {note: index for index, note in enumerate(note_order)}

        first_measure = system_bars[0][0]
        last_measure = system_bars[-1][0]
        svg.append(
            f'<text x="18" y="{origin_y + 20}" class="system-label">'
            f'Bars {first_measure}–{last_measure}</text>'
        )

        # Instrument names, ordered through the selected slot-map but shown as
        # actual GM instrument names rather than slot categories.
        for index, note_number in enumerate(note_order):
            y = plot_top + index * row_height + row_height / 2
            name = GM_DRUM_NAMES.get(
                note_number,
                f"Unknown drum note {note_number}",
            )
            svg.append(
                f'<text x="{label_width - 10}" y="{y + 4:.1f}" '
                f'class="instrument-label" text-anchor="end">'
                f'{html.escape(name)} · GM {note_number}</text>'
            )
            svg.append(
                f'<line x1="{plot_left}" y1="{y + row_height / 2:.1f}" '
                f'x2="{plot_left + plot_width}" '
                f'y2="{y + row_height / 2:.1f}" class="staff-line"/>'
            )

        for bar_index, (measure, start, end, numerator, denominator) in enumerate(system_bars):
            x0 = plot_left + bar_index * bar_width
            duration = max(1, end - start)
            svg.append(
                f'<line x1="{x0}" y1="{plot_top - 8}" x2="{x0}" '
                f'y2="{plot_top + plot_height}" class="bar-line"/>'
            )
            svg.append(
                f'<text x="{x0 + 5}" y="{plot_top - 15}" class="bar-number">'
                f'{measure}</text>'
            )
            svg.append(
                f'<text x="{x0 + bar_width - 5}" y="{plot_top - 15}" '
                f'class="meter-label" text-anchor="end">'
                f'{numerator}/{denominator}</text>'
            )

            beat_ticks = mid.ticks_per_beat * 4 / denominator
            for beat in range(1, numerator):
                bx = x0 + beat * beat_ticks / duration * bar_width
                svg.append(
                    f'<line x1="{bx:.2f}" y1="{plot_top}" x2="{bx:.2f}" '
                    f'y2="{plot_top + plot_height}" class="beat-line"/>'
                )

            for note in drum_notes:
                if not (start <= note.start_tick < end):
                    continue
                relative = (note.start_tick - start) / duration
                x = x0 + relative * bar_width
                actual_width = note.duration / duration * bar_width
                x2 = min(x0 + bar_width, max(x + 1.5, x + actual_width))
                if note.note not in note_index:
                    continue
                y = plot_top + note_index[note.note] * row_height + row_height / 2
                radius = 2.0 + 2.0 * note.velocity / 127
                instrument = GM_DRUM_NAMES.get(
                    note.note,
                    "Unknown / non-GM drum note",
                )
                position = musical_position(
                    note.start_tick,
                    mid.ticks_per_beat,
                    ts_map,
                )
                tooltip = (
                    f"{position} · {instrument} · note {note.note} · "
                    f"velocity {note.velocity} · duration {note.duration}"
                )
                svg.append(
                    f'<line x1="{x:.2f}" y1="{y:.2f}" x2="{x2:.2f}" '
                    f'y2="{y:.2f}" class="note-duration">'
                    f'<title>{html.escape(tooltip)}</title></line>'
                )
                svg.append(
                    f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" '
                    f'class="note-head"><title>{html.escape(tooltip)}</title>'
                    f'</circle>'
                )

        right_x = plot_left + actual_bars * bar_width
        svg.append(
            f'<line x1="{right_x}" y1="{plot_top - 8}" x2="{right_x}" '
            f'y2="{plot_top + plot_height}" class="bar-line end-line"/>'
        )

    svg.append("</svg>")

    target_dir = output_dir if output_dir is not None else source.parent
    target = target_dir / f"{source.stem}_DRUM_ROLL.html"
    # Reports are derived artifacts. Regeneration should refresh an existing
    # report rather than leave a stale file from an older script version.
    target.parent.mkdir(parents=True, exist_ok=True)

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — Drum Roll</title>
<style>
@page {{
  size: A4 portrait;
  margin: 12mm 12mm 14mm 12mm;
}}
:root {{
  color-scheme: light;
  --ink: #171717;
  --muted: #666;
  --paper: #fffef9;
  --rule: #d8d5cc;
  --beat: #dedede;
}}
html, body {{
  margin: 0;
  min-height: 100%;
}}
body {{
  padding: 18px;
  background: #ecebe7;
  color: var(--ink);
  font-family: Georgia, "Times New Roman", serif;
}}
.sheet {{
  width: 186mm;
  margin: 0 auto;
  padding: 10mm 8mm 12mm 8mm;
  box-sizing: border-box;
  background: var(--paper);
  box-shadow: 0 3px 18px rgba(0,0,0,.14);
}}
svg {{
  display: block;
  width: 100%;
  height: auto;
}}
.score-title {{ font-size: 24px; font-weight: 700; }}
.score-subtitle {{ font-size: 12px; fill: var(--muted); }}
.system-label {{ font-size: 13px; font-weight: 700; }}
.instrument-label {{ font-size: 10.5px; fill: #222; }}
.bar-number {{ font-size: 12px; font-weight: 700; }}
.meter-label {{ font-size: 10px; fill: var(--muted); }}
.staff-line {{ stroke: var(--rule); stroke-width: .7; }}
.bar-line {{ stroke: #222; stroke-width: 1.1; }}
.end-line {{ stroke-width: 2; }}
.beat-line {{ stroke: var(--beat); stroke-width: .7; stroke-dasharray: 2 3; }}
.note-duration {{
  stroke: #222;
  stroke-width: 1.35;
  stroke-linecap: round;
  opacity: .7;
}}
.note-head {{
  fill: #111;
  stroke: var(--paper);
  stroke-width: .75;
}}
@media print {{
  html, body {{
    background: white;
  }}
  body {{
    padding: 0;
  }}
  .sheet {{
    width: 100%;
    margin: 0;
    padding: 0;
    box-shadow: none;
    background: white;
  }}
  svg {{
    width: 100%;
    max-width: 186mm;
    margin: 0 auto;
  }}
}}
</style>
</head>
<body>
<div class="sheet">
{''.join(svg)}
</div>
</body>
</html>
"""
    target.write_text(document, encoding="utf-8")
    return target


def output_path_for(source: Path, suffix: str, output_dir: Optional[Path]) -> Path:
    directory = output_dir if output_dir is not None else source.parent
    return directory / f"{source.stem}{suffix}.MID"


def save_type0(source: Path, mid: MidiFile, output_dir: Optional[Path]) -> Path:
    target = output_path_for(source, "_T0", output_dir)
    if target.exists():
        raise FileExistsError(f"output exists: {target}")

    if mid.type == 0:
        # Still write a separate, normalized copy as explicitly requested.
        merged = MidiTrack()
        merged.extend(
            msg.copy()
            for msg in mid.tracks[0]
            if not (isinstance(msg, MetaMessage) and msg.type == "end_of_track")
        )
    else:
        merged = MidiTrack()
        merged.extend(
            msg.copy()
            for msg in merge_tracks(mid.tracks)
            if not (isinstance(msg, MetaMessage) and msg.type == "end_of_track")
        )

    merged.append(MetaMessage("end_of_track", time=0))
    result = MidiFile(type=0, ticks_per_beat=mid.ticks_per_beat)
    result.tracks.append(merged)
    target.parent.mkdir(parents=True, exist_ok=True)
    result.save(target)
    return target


def extract_drums(source: Path, mid: MidiFile, output_dir: Optional[Path]) -> Path:
    target = output_path_for(source, "_DRUMS", output_dir)
    if target.exists():
        raise FileExistsError(f"output exists: {target}")

    merged = merge_tracks(mid.tracks)
    absolute: List[Tuple[int, int, Message | MetaMessage]] = []
    tick = 0
    order = 0

    keep_meta = {
        "set_tempo",
        "time_signature",
        "key_signature",
        "track_name",
        "marker",
        "cue_marker",
        "text",
        "copyright",
        "smpte_offset",
        "sequencer_specific",
    }

    for msg in merged:
        tick += int(msg.time)
        keep = False
        if isinstance(msg, MetaMessage):
            keep = msg.type in keep_meta
        elif isinstance(msg, Message):
            keep = getattr(msg, "channel", None) == DRUM_CHANNEL

        if keep:
            absolute.append((tick, order, msg.copy(time=0)))
            order += 1

    absolute.sort(key=lambda item: (item[0], item[1]))
    track = MidiTrack()
    previous_tick = 0
    for event_tick, _order, msg in absolute:
        delta = max(0, event_tick - previous_tick)
        previous_tick = event_tick
        track.append(msg.copy(time=delta))
    track.append(MetaMessage("end_of_track", time=0))

    result = MidiFile(type=0, ticks_per_beat=mid.ticks_per_beat)
    result.tracks.append(track)
    target.parent.mkdir(parents=True, exist_ok=True)
    result.save(target)
    return target


def collect_input_files(path: Path, recursive: bool) -> List[Path]:
    if path.is_file():
        if path.suffix.lower() not in {".mid", ".midi"}:
            raise ValueError(f"not a MIDI file: {path}")
        return [path]

    if not path.is_dir():
        raise ValueError(f"not found: {path}")

    iterator: Iterable[Path]
    iterator = path.rglob("*") if recursive else path.iterdir()
    files = sorted(
        (
            item for item in iterator
            if item.is_file() and item.suffix.lower() in {".mid", ".midi"}
        ),
        key=lambda item: str(item).lower(),
    )
    if not files:
        raise ValueError(f"no MIDI files found: {path}")
    return files


def inspect_file(path: Path, args: argparse.Namespace) -> int:
    try:
        mid = MidiFile(path)
    except Exception as exc:
        print(f"[ERROR] {path}: {exc}", file=sys.stderr)
        return 1

    if mid.ticks_per_beat <= 0:
        print(f"[ERROR] {path}: SMPTE timing is not supported", file=sys.stderr)
        return 1

    timed = collect_timed_messages(mid)
    ts_map = build_time_signature_map(timed, mid.ticks_per_beat)
    notes = pair_notes(timed)

    print_smf_summary(path, mid, timed, ts_map)
    print_channel_summary(timed, mid.ticks_per_beat, ts_map)
    print_drum_summary(notes)

    if args.dump:
        dump_paired_notes(notes, mid.ticks_per_beat, ts_map, drums_only=False)
    if args.dump_drums:
        dump_paired_notes(notes, mid.ticks_per_beat, ts_map, drums_only=True)

    try:
        if args.write_type0:
            target = save_type0(path, mid, args.output_dir)
            print(f"\n[WROTE] {target}")
        if args.extract_drums:
            target = extract_drums(path, mid, args.output_dir)
            print(f"\n[WROTE] {target}")
        if args.write_drum_roll:
            slot_map_path = (
                args.slot_maps
                if args.slot_maps is not None
                else Path(__file__).with_name("slot_map_definitions.json")
            )
            slot_maps = load_slot_maps(slot_map_path)
            target = write_drum_roll_html(
                path,
                mid,
                timed,
                notes,
                ts_map,
                slot_maps,
                args.bars_per_row,
                args.show_all_notes,
                args.output_dir,
            )
            print(f"\n[WROTE] {target}")
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {path}: {exc}", file=sys.stderr)
        return 1

    print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description=(
            "Inspect Standard MIDI Files and optionally write Type 0 or "
            "drum-only copies for the ADX Toolkit."
        ),
    )
    parser.add_argument("input", type=Path, help="MIDI file or directory")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="scan directories recursively",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="dump paired note events using measure:beat:tick positions",
    )
    parser.add_argument(
        "--dump-drums",
        action="store_true",
        help="dump only CH10 paired note events",
    )
    parser.add_argument(
        "--write-type0",
        action="store_true",
        help="write a non-destructive *_T0.MID copy",
    )
    parser.add_argument(
        "--extract-drums",
        action="store_true",
        help="write a CH10-only *_DRUMS.MID copy with relevant meta events",
    )
    parser.add_argument(
        "--write-drum-roll",
        action="store_true",
        help=(
            "write a printable A4-portrait CH10 drum sheet HTML/SVG using "
            "the canonical slot-map order, actual GM instrument names, "
            "and GM note numbers"
        ),
    )
    parser.add_argument(
        "--bars-per-row",
        type=int,
        default=4,
        help="number of measures per drum-roll system (default: 4; A4 portrait)",
    )
    parser.add_argument(
        "--show-all-notes",
        action="store_true",
        help=(
            "use the same file-wide instrument rows in every drum-roll system; "
            "default compact mode hides rows without events in each system"
        ),
    )
    parser.add_argument(
        "--slot-maps",
        type=Path,
        help=(
            "slot_map_definitions.json "
            "(default: beside this script; required by --write-drum-roll)"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="directory for generated MIDI/HTML files",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=VERSION_TEXT,
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.bars_per_row < 1:
        print("[ERROR] --bars-per-row must be at least 1", file=sys.stderr)
        return 2

    try:
        files = collect_input_files(args.input, args.recursive)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    failures = 0
    for path in files:
        failures += inspect_file(path, args)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())