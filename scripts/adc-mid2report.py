# adc-mid2report.py 260801b (fully integrated shared rhythm analysis)
# Integrated MIDI report + whole-song and bar-level triplet tendency, flam/ghost detection, and overlap analysis.
#
# Report includes:
# - Basic file info: type, ticks_per_beat (TPQ), total length (seconds / ticks)
# - Channel usage: used channels, last effective Program/Bank, drum-channel flag
# - SysEx summary
# - Tempo / Time Signature sections (seconds-based); at the same tick, "last event wins"
# - ADT conversion hints: time signature / tempo / recommended steps_per_bar & tick_per_step
# - Subdivision analysis: straight offbeats vs triplet offbeats, excluding shared beat anchors
# - Advanced rhythm analysis: triplet candidate bars, flam/ghost bars, and their overlaps
#
# Usage:
#   python adc-mid2report.py INPUT.mid
#   python adc-mid2report.py MIDI_DIRECTORY
#   python adc-mid2report.py -h
#
# Requirements:
#   pip install mido

import sys
import argparse
from pathlib import Path
from collections import defaultdict, Counter
from statistics import median
from mido import MidiFile, MetaMessage, Message

from adc_rhythm_analysis import (
    analyze_midi_rhythm,
    recommended_steps_per_bar,
)

SCRIPT_NAME = "adc-mid2report.py"
VERSION = "260801b"
VERSION_TEXT = f"{SCRIPT_NAME} {VERSION}"

# ---- 간단 GM Program Names (0~127) ----
GM_NAMES = [
    "Acoustic Grand", "Bright Acoustic", "Electric Grand", "Honky-tonk",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet",
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)",
    "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar",
    "Guitar harmonics",
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)",
    "Fretless Bass", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1",
    "Synth Bass 2",
    "Violin", "Viola", "Cello", "Contrabass",
    "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "SynthStrings 1",
    "SynthStrings 2", "Choir Aahs", "Voice Oohs", "Synth Voice",
    "Orchestra Hit",
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "SynthBrass 1", "SynthBrass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet",
    "Piccolo", "Flute", "Recorder", "Pan Flute",
    "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)",
    "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)",
    "Lead 7 (fifths)", "Lead 8 (bass+lead)",
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)",
    "Pad 4 (choir)", "Pad 5 (bowed)", "Pad 6 (metallic)",
    "Pad 7 (halo)", "Pad 8 (sweep)",
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)",
    "FX 4 (atmosphere)", "FX 5 (brightness)", "FX 6 (goblins)",
    "FX 7 (echoes)", "FX 8 (sci-fi)",
    "Sitar", "Banjo", "Shamisen", "Koto",
    "Kalimba", "Bagpipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot"
]



# ---- GM Standard Drum Kit note names (35~81) ----
GM_DRUM_NAMES = {
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
    68: "Low Agogo", 69: "Cabasa", 70: "Maracas",
    71: "Short Whistle", 72: "Long Whistle", 73: "Short Guiro",
    74: "Long Guiro", 75: "Claves", 76: "Hi Wood Block",
    77: "Low Wood Block", 78: "Mute Cuica", 79: "Open Cuica",
    80: "Mute Triangle", 81: "Open Triangle",
}


def print_drum_articulation_report(analysis):
    flams = analysis['flams']
    ghosts = analysis['ghosts']
    settings = analysis['settings']

    print("Drum Articulation Candidates:")
    print(f"  flam gap window: 1..{settings.get('flam_max_gap_ticks', 0)} ticks")
    if flams:
        print("  Flam candidates:")
        print("    bar beat  fam  grace->main(note/vel)  gap  confidence  note")
        for f in flams:
            suffix = "roll/drag-like cluster" if f['cluster_like'] else ""
            print(f"    {f['bar']:4d} {f['beat']:4.2f}  {f['family']:<3}  "
                  f"{f['grace_note']:3d}/{f['grace_velocity']:3d} -> "
                  f"{f['main_note']:3d}/{f['main_velocity']:3d}  "
                  f"{f['gap_ticks']:3d}  {f['confidence']:<10} {suffix}")
    else:
        print("  Flam candidates: (none)")

    ordinary_ghosts = [g for g in ghosts if not g['flam_grace']]
    flam_ghosts = [g for g in ghosts if g['flam_grace']]
    if ordinary_ghosts:
        print("  Ghost-like hits:")
        print("    bar beat  fam  note vel  family_median threshold")
        for g in ordinary_ghosts:
            print(f"    {g['bar']:4d} {g['beat']:4.2f}  {g['family']:<3}  "
                  f"{g['note']:4d} {g['velocity']:3d}  "
                  f"{g['median_velocity']:13.1f} {g['threshold']:9d}")
    else:
        print("  Ghost-like hits: (none)")
    if flam_ghosts:
        print(f"  note: {len(flam_ghosts)} low-velocity hit(s) were already identified as flam grace notes.")

    flam_bars = sorted({f['bar'] for f in flams})
    ghost_bars = sorted({g['bar'] for g in ordinary_ghosts})
    print(f"  bars with flam candidates : {flam_bars if flam_bars else '(none)'}")
    print(f"  bars with ghost-like hits : {ghost_bars if ghost_bars else '(none)'}")
    print("  caution: these are heuristic candidates; audition or piano-roll inspection is recommended.")
    print()


def collect_note_frequencies(mid: MidiFile):
    """Count every note_on event (velocity > 0), preserving channel and note number."""
    by_channel_note = Counter()
    drum_notes = Counter()
    for track in mid.tracks:
        for msg in track:
            if (isinstance(msg, Message) and msg.type == 'note_on'
                    and msg.velocity > 0):
                ch = getattr(msg, 'channel', None)
                if ch is None:
                    continue
                by_channel_note[(ch, msg.note)] += 1
                if ch == 9:
                    drum_notes[msg.note] += 1
    return by_channel_note, drum_notes


def print_note_frequency_report(by_channel_note: Counter, drum_notes: Counter):
    total = sum(by_channel_note.values())
    print("Note-On Frequencies (all channels, no grouping):")
    print(f"  total note_on events: {total}")
    print("  ch  note  count")
    for (ch, note), count in sorted(by_channel_note.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        print(f"  {ch + 1:2d}  {note:4d}  {count:8d}")
    if not by_channel_note:
        print("  (none)")
    print()

    print("GM Drum Note Frequencies (channel 10, no grouping):")
    print("  note  GM instrument                 count     percent")
    drum_total = sum(drum_notes.values())
    for note, count in sorted(drum_notes.items()):
        name = GM_DRUM_NAMES.get(note, "Unknown / non-GM note")
        pct = (count / drum_total * 100.0) if drum_total else 0.0
        print(f"  {note:4d}  {name:<28}  {count:8d}  {pct:8.3f}%")
    if not drum_notes:
        print("  (none)")
    print(f"  total: {drum_total}")
    print()


def find_midi_files(directory: Path):
    """Return MIDI files below directory, recursively, in deterministic order."""
    return sorted(
        p for p in directory.rglob('*')
        if p.is_file() and p.suffix.lower() in ('.mid', '.midi')
    )


def report_directory(path: str):
    root = Path(path)
    files = find_midi_files(root)
    if not files:
        raise FileNotFoundError(f"no .mid/.midi files found in: {root}")

    aggregate = Counter()
    aggregate_drum = Counter()
    processed = 0
    errors = []
    rhythm_candidates = []

    for midi_path in files:
        try:
            mid = MidiFile(str(midi_path))
            by_channel_note, drum_notes = collect_note_frequencies(mid)
            aggregate.update(by_channel_note)
            aggregate_drum.update(drum_notes)

            events = build_absolute_events(mid)
            end_tick = song_end_tick(events)
            tpq = mid.ticks_per_beat
            timesigs = last_wins_map(events, 'timesig')
            ts_segs = build_timesig_segments(timesigs, end_tick)

            rhythm = analyze_midi_rhythm(mid, ts_segs, midi_path.name)
            bar_subdivisions = rhythm['bars']
            triplet_bars = sorted(
                row['bar'] for row in bar_subdivisions
                if row['triplet_candidate']
            )

            articulations = rhythm['articulations']
            flam_bars = sorted({item['bar'] for item in articulations['flams']})
            ghost_bars = sorted({
                item['bar'] for item in articulations['ghosts']
                if not item['flam_grace']
            })

            if triplet_bars or flam_bars or ghost_bars:
                rhythm_candidates.append({
                    'path': midi_path.relative_to(root),
                    'triplet_bars': triplet_bars,
                    'flam_bars': flam_bars,
                    'ghost_bars': ghost_bars,
                })

            processed += 1
        except Exception as exc:
            errors.append((midi_path, str(exc)))

    print("============================================================")
    print(f"Directory: {root}")
    print(f"MIDI files found: {len(files)}   processed: {processed}   errors: {len(errors)}")
    print("============================================================\n")
    print_note_frequency_report(aggregate, aggregate_drum)

    print("Rhythm Candidate Files:")
    if rhythm_candidates:
        for item in rhythm_candidates:
            print(f"  {item['path']}")
            print(f"    triplet bars : {item['triplet_bars'] if item['triplet_bars'] else '(none)'}")
            print(f"    flam bars    : {item['flam_bars'] if item['flam_bars'] else '(none)'}")
            print(f"    ghost bars   : {item['ghost_bars'] if item['ghost_bars'] else '(none)'}")
    else:
        print("  (none)")
    print()

    print("Rhythm Candidate Summary:")
    print(f"  files with any candidate : {len(rhythm_candidates)}")
    print(f"  triplet candidate files  : {sum(bool(x['triplet_bars']) for x in rhythm_candidates)}")
    print(f"  flam candidate files     : {sum(bool(x['flam_bars']) for x in rhythm_candidates)}")
    print(f"  ghost candidate files    : {sum(bool(x['ghost_bars']) for x in rhythm_candidates)}")
    print()

    if errors:
        print("Files skipped because of errors:")
        for midi_path, message in errors:
            print(f"  {midi_path}: {message}")

def pp_time(sec: float) -> str:
    return f"{sec:8.3f}s"

def micros_per_qn_to_bpm(us_per_qn: int) -> float:
    return 60_000_000.0 / us_per_qn if us_per_qn else 0.0

def build_absolute_events(mid: MidiFile):
    events = []
    for ti, track in enumerate(mid.tracks):
        abs_t = 0
        for msg in track:
            abs_t += msg.time
            events.append((abs_t, msg))
    events.sort(key=lambda x: x[0])  # stable sort
    return events

def song_end_tick(events) -> int:
    return events[-1][0] if events else 0

def last_wins_map(events, kind: str):
    d = {}
    if kind == 'tempo':
        for t, m in events:
            if isinstance(m, MetaMessage) and m.type == 'set_tempo':
                d[t] = m.tempo
    elif kind == 'timesig':
        for t, m in events:
            if isinstance(m, MetaMessage) and m.type == 'time_signature':
                d[t] = (m.numerator, m.denominator)
    else:
        raise ValueError("unknown kind")
    return sorted(d.items(), key=lambda kv: kv[0])

def build_tempo_segments(tpq: int, tempos: list, end_tick: int):
    if not tempos or tempos[0][0] != 0:
        tempos = [(0, 500000)] + tempos  # default 120 BPM
    segs = []
    cur_sec = 0.0
    for i, (t0, us0) in enumerate(tempos):
        t1 = tempos[i+1][0] if i+1 < len(tempos) else end_tick
        bpm = micros_per_qn_to_bpm(us0)
        ticks_len = max(0, t1 - t0)
        dur_sec = (us0 / 1_000_000.0) * (ticks_len / tpq)
        segs.append((t0, cur_sec, t1, cur_sec + dur_sec, us0, bpm))
        cur_sec += dur_sec
    return segs

def build_timesig_segments(timesigs: list, end_tick: int):
    if not timesigs or timesigs[0][0] != 0:
        timesigs = [(0, (4, 4))] + timesigs
    segs = []
    for i, (t0, ts) in enumerate(timesigs):
        t1 = timesigs[i+1][0] if i+1 < len(timesigs) else end_tick
        segs.append((t0, t1, ts))
    return segs

def collect_channels_and_programs(mid: MidiFile):
    ch_used = set()
    ch_prog = {ch: {'bank_msb': None, 'bank_lsb': None, 'program': None} for ch in range(16)}
    ch_drum = {ch: (ch == 9) for ch in range(16)}  # GM convention: channel 10 (index 9) is drums
    ch_note_count = defaultdict(int)

    for track in mid.tracks:
        abs_t = 0
        for msg in track:
            abs_t += msg.time
            if isinstance(msg, Message) and not msg.is_meta:
                if hasattr(msg, 'channel'):
                    ch = msg.channel
                    ch_used.add(ch)
                    if msg.type == 'control_change':
                        if msg.control == 0:   # Bank MSB
                            ch_prog[ch]['bank_msb'] = msg.value
                        elif msg.control == 32: # Bank LSB
                            ch_prog[ch]['bank_lsb'] = msg.value
                    elif msg.type == 'program_change':
                        ch_prog[ch]['program'] = msg.program
                    elif msg.type == 'note_on' and msg.velocity > 0:
                        ch_note_count[ch] += 1
    return ch_used, ch_prog, ch_drum, ch_note_count

def collect_sysex(events):
    syx = []
    for t, m in events:
        if isinstance(m, Message) and m.type == 'sysex':
            data = m.data or bytes()
            mfr = f"{data[0]:02X}" if len(data) > 0 else "--"
            syx.append((t, len(data), mfr))
    return syx

def estimate_length_seconds(tempo_segs):
    return tempo_segs[-1][3] if tempo_segs else 0.0

# ---------- Shared rhythm detection is provided by adc_rhythm_analysis.py ----------

def print_advanced_rhythm_report(bar_subdivisions, articulations):
    """Print bar-level triplet, articulation, and overlap results as the final report."""
    flams = articulations['flams']
    ghosts = articulations['ghosts']
    settings = articulations['settings']
    ordinary_ghosts = [g for g in ghosts if not g['flam_grace']]
    flam_ghosts = [g for g in ghosts if g['flam_grace']]

    triplet_rows = [r for r in bar_subdivisions if r['triplet_candidate']]
    triplet_bars = sorted({r['bar'] for r in triplet_rows})
    flam_bars = sorted({f['bar'] for f in flams})
    ghost_bars = sorted({g['bar'] for g in ordinary_ghosts})

    triplet_flam = sorted(set(triplet_bars) & set(flam_bars))
    triplet_ghost = sorted(set(triplet_bars) & set(ghost_bars))
    flam_ghost = sorted(set(flam_bars) & set(ghost_bars))
    all_three = sorted(set(triplet_bars) & set(flam_bars) & set(ghost_bars))

    print("============================================================")
    print("Advanced Rhythm Analysis (bar-level heuristic report)")
    print("============================================================")

    print("Triplet Candidate Bars:")
    if triplet_rows:
        print("  bar meter positions evidence trip_hits straight_hits triplet_ratio")
        for row in triplet_rows:
            n, d = row['meter']
            print(f"  {row['bar']:4d} {n}/{d:<3} {row['note_positions']:9d} "
                  f"{row['samples']:8d} {row['triplet_hits']:9d} "
                  f"{row['straight_hits']:13d} {row['triplet_hit_ratio']:13.3f}")
    else:
        print("  (none)")
    print(f"  bars: {triplet_bars if triplet_bars else '(none)'}")
    print("  rule: exclude beat anchors; at least 2 triplet offbeats and triplet ratio >= 0.60")
    print()

    print("Flam Candidates:")
    print(f"  flam gap window: 1..{settings.get('flam_max_gap_ticks', 0)} ticks")
    if flams:
        print("  bar beat  fam  grace->main(note/vel)  gap  confidence  note")
        for f in flams:
            suffix = "roll/drag-like cluster" if f['cluster_like'] else ""
            print(f"  {f['bar']:4d} {f['beat']:4.2f}  {f['family']:<3}  "
                  f"{f['grace_note']:3d}/{f['grace_velocity']:3d} -> "
                  f"{f['main_note']:3d}/{f['main_velocity']:3d}  "
                  f"{f['gap_ticks']:3d}  {f['confidence']:<10} {suffix}")
    else:
        print("  (none)")
    print(f"  bars: {flam_bars if flam_bars else '(none)'}")
    print()

    print("Ghost-like Hits:")
    if ordinary_ghosts:
        print("  bar beat  fam  note vel  family_median threshold")
        for g in ordinary_ghosts:
            print(f"  {g['bar']:4d} {g['beat']:4.2f}  {g['family']:<3}  "
                  f"{g['note']:4d} {g['velocity']:3d}  "
                  f"{g['median_velocity']:13.1f} {g['threshold']:9d}")
    else:
        print("  (none)")
    if flam_ghosts:
        print(f"  note: {len(flam_ghosts)} low-velocity hit(s) were excluded because they are flam grace notes.")
    print(f"  bars: {ghost_bars if ghost_bars else '(none)'}")
    print()

    print("Overlap Analysis:")
    print(f"  Triplet ∩ Flam        : {triplet_flam if triplet_flam else '(none)'}")
    print(f"  Triplet ∩ Ghost       : {triplet_ghost if triplet_ghost else '(none)'}")
    print(f"  Flam ∩ Ghost          : {flam_ghost if flam_ghost else '(none)'}")
    print(f"  Triplet ∩ Flam ∩ Ghost: {all_three if all_three else '(none)'}")
    print()

    overlap_bars = sorted(set(triplet_flam) | set(triplet_ghost) | set(flam_ghost))
    print("Summary:")
    print(f"  triplet candidate bars : {len(triplet_bars):4d}")
    print(f"  flam candidate bars    : {len(flam_bars):4d}")
    print(f"  ghost-like bars        : {len(ghost_bars):4d}")
    print(f"  any-overlap bars       : {len(overlap_bars):4d}  {overlap_bars if overlap_bars else '(none)'}")
    print("  caution: all advanced rhythm findings are heuristic candidates; audition or piano-roll inspection is recommended.")
    print()

def adt_hints(tpq, ts_segs, tempo_segs, triplet_decision=None):
    """
    ADT conversion hints:
    - Representative time signature / tempo
    - Recommended steps_per_bar:
        * 4/4 -> 16 (default)
        * 3/4 -> 12
        * 6/8 -> 12
      In 4/4, detected 8T recommends 12 steps/bar and 16T recommends 24 steps/bar.
    """
    ts = ts_segs[0][2] if ts_segs else (4, 4)
    num, den = ts
    bpm = tempo_segs[0][5] if tempo_segs else 120.0

    if den == 4:
        beats_per_bar = num
    elif den == 8:
        beats_per_bar = num / 2.0
    else:
        beats_per_bar = num * (4.0 / den)

    steps_per_bar = recommended_steps_per_bar(num, den, triplet_decision)
    tick_per_step = (tpq * beats_per_bar) / steps_per_bar
    return {
        'time_signature': f"{num}/{den}",
        'bpm': round(bpm, 3),
        'steps_per_bar': int(steps_per_bar),
        'tick_per_step': tick_per_step
    }

# ---------- Main report ----------

def main(path: str):
    mid = MidiFile(path)
    events = build_absolute_events(mid)
    end_t = song_end_tick(events)
    tpq = mid.ticks_per_beat
    typ = mid.type

    # Meta: tempo / time signature (at the same tick, last event wins)
    tempos  = last_wins_map(events, 'tempo')
    timesig = last_wins_map(events, 'timesig')
    tempo_segs = build_tempo_segments(tpq, tempos, end_t)
    ts_segs    = build_timesig_segments(timesig, end_t)

    eff_us0 = tempo_segs[0][4] if tempo_segs else 500000
    eff_bpm = micros_per_qn_to_bpm(eff_us0)
    eff_ts  = ts_segs[0][2] if ts_segs else (4, 4)

    # Channels / programs
    ch_used, ch_prog, ch_drum, ch_note_count = collect_channels_and_programs(mid)

    # SysEx
    sysex_list = collect_sysex(events)

    # Total length (seconds)
    total_sec = estimate_length_seconds(tempo_segs)

    # Shared grace/flam/ghost and straight/8T/16T analysis.
    # Conservative flam grace onsets are excluded before subdivision scoring.
    rhythm = analyze_midi_rhythm(mid, ts_segs, Path(path).name)
    subdiv = rhythm['subdivision']
    bar_subdivisions = rhythm['bars']
    articulations = rhythm['articulations']

    # ADT hints (triplet-aware)
    hints = adt_hints(tpq, ts_segs, tempo_segs, subdiv)

    # --------- Output ---------
    print("============================================================")
    print(f"File: {path}")
    print(f"Type: {typ}   TPQ: {tpq}   EndTick: {end_t}   Length: {total_sec:.3f}s")
    print(f"Effective @0  Tempo: {eff_bpm:.3f} BPM   TimeSig: {eff_ts[0]}/{eff_ts[1]}")
    print("============================================================\n")

    by_channel_note, drum_notes = collect_note_frequencies(mid)
    print_note_frequency_report(by_channel_note, drum_notes)

    print(f"Channels Used: {len(ch_used)}  -> {sorted(ch_used)}")
    active_note_ch = sorted([ch for ch, n in ch_note_count.items() if n > 0])
    setup_only_ch  = sorted([ch for ch in ch_used if ch_note_count.get(ch,0) == 0])
    print(f"  Active note channels: {active_note_ch}  (played notes)")
    print(f"  Setup-only channels : {setup_only_ch}   (CC/PC etc., no notes)")
    print()
    print("Per-Channel Program/Bank (last effective):")
    print("  ch  drum  bank(msb:lsb)  program  name                          notes")
    for ch in range(16):
        if ch in ch_used or ch == 9:
            msb = ch_prog[ch]['bank_msb']
            lsb = ch_prog[ch]['bank_lsb']
            pgm = ch_prog[ch]['program']
            name = GM_NAMES[pgm] if (pgm is not None and 0 <= pgm <= 127) else "-"
            notes = ch_note_count.get(ch, 0)
            print(f"  {ch:2d}  {str(ch_drum[ch]):<5}  "
                  f"{'-' if msb is None else msb:>3}:{'-' if lsb is None else lsb:<3}   "
                  f"{'-' if pgm is None else pgm:>3}     {name:<28}  {notes:6d}")
    print()

    if sysex_list:
        print("SysEx Messages:")
        print("  tick       length  mfr_id(hex)")
        for t, ln, mfr in sysex_list:
            print(f"  {t:10d}   {ln:6d}   {mfr}")
    else:
        print("SysEx Messages: (none)")
    print()

    print("Tempo Map (sections):")
    print("  start_tick @ start_sec  ->  BPM   |  end_tick @ end_sec   (dur)")
    for (t0, s0, t1, s1, us, bpm) in tempo_segs:
        print(f"{t0:12d} @ {pp_time(s0)} -> {bpm:7.3f} | "
              f"{t1:12d} @ {pp_time(s1)}  (Δ {pp_time(s1 - s0)})")
    print()

    print("Time Signatures (sections):")
    print("  start_tick -> end_tick : numer/denom")
    for (t0, t1, (n, d)) in ts_segs:
        print(f"{t0:10d} -> {t1:10d} : {n}/{d}")
    print()

    print("ADT Conversion Hints:")
    print(f"  time_signature : {hints['time_signature']}")
    print(f"  bpm            : {hints['bpm']}")
    print(f"  steps_per_bar  : {hints['steps_per_bar']}")
    print(f"  tick_per_step  : {hints['tick_per_step']:.3f}")
    print()

    print("Subdivision Analysis:")
    print(f"  grid                  : {subdiv['grid']}")
    print(f"  resolution            : {subdiv.get('resolution', 'unknown')}")
    print(f"  subdivision           : {subdiv.get('subdivision', subdiv['grid'])}")
    print(f"  rhythmic_feel         : {subdiv.get('rhythmic_feel', subdiv['grid'])}")
    print(f"  confidence            : {subdiv.get('confidence', 0.0)}")
    print(f"  observed_resolution   : {subdiv.get('observed_resolution', '-')}")
    print(f"  phase_subdivision     : {subdiv.get('phase_subdivision', '-')}")
    print(f"  duration_samples      : {subdiv.get('duration_samples', 0)}")
    print(f"  excluded_flam_grace   : {subdiv.get('excluded_flam_grace_count', 0)}")
    print(f"  combined_scores       : {subdiv.get('combined_scores', {})}")
    print(f"  filename_hints        : {subdiv.get('filename_hints', []) or '(none)'}")
    print(f"  triplet_offbeat_ratio : {subdiv['triplet_hit_ratio']}")
    print(f"  straight_offbeat_ratio: {subdiv['straight_hit_ratio']}")
    det = subdiv['details']
    print(f"  evidence={det.get('samples',0)}, anchors_excluded={det.get('anchor_hits',0)}, "
          f"shared_half_excluded={det.get('shared_half_hits',0)}, straight_hits={det.get('straight_hits',0)}, "
          f"8T_hits={det.get('triplet_8t_hits',0)}, 16T_only_hits={det.get('triplet_16t_only_hits',0)}, "
          f"unclassified={det.get('unclassified_hits',0)}, tol_ticks={det.get('tol_ticks','-')}")
    if subdiv['grid'] == 'triplet':
        print(f"  note: {subdiv.get('subdivision')} detected → {hints['steps_per_bar']} steps/bar recommended.")
    elif subdiv['grid'] in ('mixed', 'unknown'):
        print("  note: No firm subdivision decision → meter-based default recommendation retained.")
    print()

    print_advanced_rhythm_report(bar_subdivisions, articulations)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description=(
            f"{VERSION_TEXT}\n"
            "MIDI inspection tool that prints an integrated report including tempo/time-signature sections, "
            "channel usage, SysEx summary, ADT conversion hints, offbeat-position straight vs 8T/16T subdivision tendency, "
            "and a final bar-level triplet/flam/ghost overlap report."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "input_path",
        nargs="?",
        help="Input MIDI file, or a directory containing .mid/.midi files",
    )
    p.add_argument(
        "--version",
        action="version",
        version=VERSION_TEXT,
        help="Show script version and exit",
    )
    return p


if __name__ == "__main__":
    parser = _build_arg_parser()

    if len(sys.argv) == 1:
        print(VERSION_TEXT)
        print()
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    try:
        input_path = Path(args.input_path)
        if input_path.is_dir():
            report_directory(str(input_path))
        elif input_path.is_file():
            main(str(input_path))
        else:
            raise FileNotFoundError(args.input_path)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
