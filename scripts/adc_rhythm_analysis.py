#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adc_rhythm_analysis.py 260804c

Shared grace/flam/ghost and straight/8T/16T subdivision analysis for ADC Toolkit.
Used by adc-patternlab.py and adc-mid2report.py.

The module analyzes MIDI data only; it does not render output or modify MIDI files.
Legacy adc_flam.py and adc_subdivision.py remain unchanged during migration stage 1.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
from statistics import median
from typing import Any, Iterable

from mido import Message, MidiFile

SCRIPT_NAME = "adc_rhythm_analysis.py"
VERSION = "260804c"
VERSION_TEXT = f"{SCRIPT_NAME} {VERSION}"

ADT_DRUM_FAMILIES = {
    35: "KK", 36: "KK", 37: "SS", 38: "SN", 40: "SN", 39: "CL",
    41: "LT", 43: "LT", 45: "MT", 47: "MT", 48: "HT", 50: "HT",
    42: "CH", 44: "PH", 46: "OH", 49: "CR", 52: "CR", 55: "CR", 57: "CR",
    51: "RD", 53: "RD", 59: "RD",
}
GHOST_FAMILIES = {"SN", "SS", "LT", "MT", "HT", "CL"}


def _get(event: Any, name: str, default=None):
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def gather_note_on_ticks(mid: MidiFile, excluded_ticks: set[int] | None = None) -> list[int]:
    """Collect absolute note-on ticks, preferring channel 10 when present."""
    excluded_ticks = excluded_ticks or set()
    all_ticks: list[int] = []
    drum_ticks: list[int] = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if isinstance(msg, Message) and msg.type == "note_on" and msg.velocity > 0:
                if tick in excluded_ticks:
                    continue
                all_ticks.append(tick)
                if getattr(msg, "channel", -1) == 9:
                    drum_ticks.append(tick)
    return sorted(drum_ticks if drum_ticks else all_ticks)


def classify_subdivision(tpq: int, note_ticks: Iterable[int]) -> dict:
    """Classify straight, 8T, or 16T evidence without overcalling 16T.

    Beat anchors and the shared half-beat are excluded. A 16T result requires
    dominant evidence at both exclusive 1/6 and 5/6 phases.
    """
    if tpq <= 0:
        tpq = 1
    tol = max(1, tpq // 24)
    anchor = shared_half = straight = t8 = t16 = unclassified = 0
    t8_phase = [0, 0]
    t16_phase = [0, 0]

    for tick in sorted(set(int(t) for t in note_ticks)):
        phase = tick % tpq
        d_anchor = min(abs(phase), abs(tpq - phase))
        d_half = abs(phase - tpq / 2)
        d_straight = min(abs(phase - tpq / 4), abs(phase - 3 * tpq / 4))
        d8a, d8b = abs(phase - tpq / 3), abs(phase - 2 * tpq / 3)
        d16a, d16b = abs(phase - tpq / 6), abs(phase - 5 * tpq / 6)
        if d_anchor <= tol:
            anchor += 1
        elif d_half <= tol:
            shared_half += 1
        else:
            distance, kind, phase_index = min(
                [(d_straight, "straight", -1), (d8a, "8T", 0), (d8b, "8T", 1),
                 (d16a, "16T", 0), (d16b, "16T", 1)],
                key=lambda item: item[0],
            )
            if distance > tol:
                unclassified += 1
            elif kind == "straight":
                straight += 1
            elif kind == "8T":
                t8 += 1
                t8_phase[phase_index] += 1
            else:
                t16 += 1
                t16_phase[phase_index] += 1

    triplet = t8 + t16
    evidence = straight + triplet
    straight_ratio = straight / evidence if evidence else 0.0
    triplet_ratio = triplet / evidence if evidence else 0.0
    grid = resolution = subdivision = rhythmic_feel = "unknown"

    if evidence:
        if straight >= 2 and straight_ratio >= 0.60:
            grid, resolution, subdivision, rhythmic_feel = "straight", "16", "straight-16", "straight"
        elif triplet >= 2 and triplet_ratio >= 0.60:
            strong_16 = (
                t16 >= 4 and t16 / evidence >= 0.60 and
                t16 / max(1, triplet) >= 0.67 and min(t16_phase) >= 1
            )
            strong_8 = t8 >= 2 and t8 / max(1, triplet) >= 0.60
            grid, rhythmic_feel = "triplet", "shuffle/swing"
            if strong_16:
                resolution, subdivision = "16T", "triplet-16T"
            elif strong_8:
                resolution, subdivision = "8T", "triplet-8T"
            else:
                resolution, subdivision = "ambiguous", "triplet-ambiguous"
        else:
            grid = resolution = subdivision = "mixed"
            rhythmic_feel = "mixed/ambiguous"

    details = {
        "samples": evidence,
        "anchor": anchor,
        "anchor_hits": anchor,
        "shared_half": shared_half,
        "shared_half_hits": shared_half,
        "straight": straight,
        "straight_hits": straight,
        "8T": t8,
        "8T_phase": t8_phase,
        "triplet_8t_hits": t8,
        "16T": t16,
        "16T_phase": t16_phase,
        "triplet_16t_only_hits": t16,
        "triplet_hits": triplet,
        "unclassified": unclassified,
        "unclassified_hits": unclassified,
        "tol": tol,
        "tol_ticks": tol,
    }
    return {
        "grid": grid,
        "resolution": resolution,
        "subdivision": subdivision,
        "rhythmic_feel": rhythmic_feel,
        "confidence": round(max(straight_ratio, triplet_ratio) if evidence else 0.0, 3),
        "straight": round(straight_ratio, 3),
        "triplet": round(triplet_ratio, 3),
        "straight_hit_ratio": round(straight_ratio, 3),
        "triplet_hit_ratio": round(triplet_ratio, 3),
        "details": details,
    }


def infer_subdivision_hint(filename: str) -> dict:
    """Return conservative filename evidence for straight/triplet resolution."""
    stem = Path(filename).stem.upper()
    compact = re.sub(r"[^A-Z0-9]+", "", stem)
    scores = {"straight-16": 0.0, "triplet-8T": 0.0, "triplet-16T": 0.0}
    reasons = []

    def add(kind: str, weight: float, label: str) -> None:
        scores[kind] += weight
        reasons.append(label)

    if any(x in compact for x in ("16TRIPLET", "TRIPLET16", "16T")):
        add("triplet-16T", 0.34, "filename:triplet-16")
    if any(x in compact for x in ("8TRIPLET", "TRIPLET8", "8T")):
        add("triplet-8T", 0.34, "filename:triplet-8")
    if any(x in compact for x in ("SHUFFLE", "SWING", "TRIPLET")):
        add("triplet-8T", 0.18, "filename:shuffle/swing/triplet")
    if any(x in compact for x in ("STRAIGHT16", "16TH", "16BEAT", "STRAIGHT")):
        add("straight-16", 0.30, "filename:straight")
    return {"scores": scores, "reasons": reasons}


def duration_subdivision_evidence(events: Iterable[Any], tpq: int) -> dict:
    """Return weak duration evidence for already-filtered rhythmic events."""
    scores = {"straight-16": 0.0, "triplet-8T": 0.0, "triplet-16T": 0.0}
    usable = [int(_get(e, "dur", _get(e, "duration", 0))) for e in events]
    usable = [duration for duration in usable if duration > 0]
    if not usable or tpq <= 0:
        return {"scores": scores, "samples": 0}
    targets = {
        "straight-16": (tpq / 4, tpq / 2, tpq),
        "triplet-8T": (tpq / 3, 2 * tpq / 3),
        "triplet-16T": (tpq / 6, tpq / 3),
    }
    tol = max(2, tpq / 20)
    for duration in usable:
        for kind, values in targets.items():
            distance = min(abs(duration - value) for value in values)
            if distance <= tol:
                scores[kind] += 1.0 - distance / tol
    total = max(1, len(usable))
    for kind in scores:
        scores[kind] = min(0.22, 0.22 * scores[kind] / total)
    return {"scores": scores, "samples": len(usable)}



def onset_grid_fit(note_ticks: Iterable[int], tpq: int) -> dict:
    """Measure onset fit for straight-16, 8T, and 16T candidate grids.

    The calculation uses unique note-on positions so simultaneous drum hits do
    not overweight one phase.  A position is considered aligned when it lies
    within 5% of one candidate grid step from the nearest grid line.
    """
    if tpq <= 0:
        tpq = 1
    ticks = sorted(set(int(tick) for tick in note_ticks))
    candidates = {
        "straight-16": 4,
        "triplet-8T": 3,
        "triplet-16T": 6,
    }
    stats = {}
    for kind, cells_per_beat in candidates.items():
        step = tpq / cells_per_beat
        tolerance = max(1.0, step * 0.05)
        errors = []
        normalized = []
        aligned = 0
        for tick in ticks:
            phase = tick % tpq
            nearest = round(phase / step) * step
            error = min(abs(phase - nearest), abs(tpq - abs(phase - nearest)))
            errors.append(error)
            normalized.append(error / step)
            if error <= tolerance:
                aligned += 1
        count = len(ticks)
        stats[kind] = {
            "count": count,
            "aligned": aligned,
            "aligned_ratio": aligned / count if count else 0.0,
            "mean_error_ticks": sum(errors) / count if count else 0.0,
            "mean_error_ratio": sum(normalized) / count if count else 0.0,
            "step_ticks": step,
            "tolerance_ticks": tolerance,
        }
    return stats


def _grid_fit_score(stat: dict) -> float:
    """Convert grid-fit statistics to bounded positive evidence."""
    aligned = float(stat.get("aligned_ratio", 0.0))
    mean_error = float(stat.get("mean_error_ratio", 1.0))
    closeness = max(0.0, 1.0 - min(1.0, mean_error / 0.25))
    return 0.34 * aligned + 0.10 * closeness


def combine_subdivision_evidence(base: dict, events: Iterable[Any], tpq: int,
                                 filename: str = "") -> dict:
    """Combine onset phase, duration, and filename evidence in one shared engine."""
    events = list(events)
    scores = {"straight-16": 0.0, "triplet-8T": 0.0, "triplet-16T": 0.0}
    details = base.get("details", {})
    evidence = max(1, details.get("samples", 0))
    scores["straight-16"] += 0.56 * details.get("straight_hits", 0) / evidence
    scores["triplet-8T"] += 0.56 * details.get("triplet_8t_hits", 0) / evidence
    scores["triplet-16T"] += 0.56 * details.get("triplet_16t_only_hits", 0) / evidence

    note_ticks = [int(_get(event, "tick", 0)) for event in events]
    grid_fit = onset_grid_fit(note_ticks, tpq)
    for kind in scores:
        scores[kind] += _grid_fit_score(grid_fit[kind])

    # A straight-8 pattern may contain only beat anchors and half-beats, leaving
    # no exclusive 1/4 or 3/4 phase evidence.  Half-beat repetition is still
    # positive straight-grid evidence and must not become unknown or 16T.
    if details.get("samples", 0) == 0 and details.get("shared_half_hits", 0) >= 2:
        scores["straight-16"] += 0.50
        base = dict(base)
        base["observed_resolution"] = "8"
        base["straight_8_fallback"] = True

    duration = duration_subdivision_evidence(events, tpq)
    hint = infer_subdivision_hint(filename)
    for kind in scores:
        scores[kind] += duration["scores"][kind] + hint["scores"][kind]

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    winner, top = ranked[0]
    runner_kind, runner = ranked[1]

    # 16T is accepted only when both exclusive phases (1/6 and 5/6 beat) are
    # actually represented.  This prevents the finer 16T grid from winning
    # merely because it also contains every 8T grid line.
    t16_phase = details.get("16T_phase", [0, 0])
    strong_16t_identity = (
        details.get("triplet_16t_only_hits", 0) >= 4
        and len(t16_phase) >= 2
        and max(t16_phase) >= 3
    )
    if winner == "triplet-16T" and not strong_16t_identity:
        eligible = [(kind, value) for kind, value in ranked if kind != "triplet-16T"]
        winner, top = eligible[0]
        runner = eligible[1][1]

    if top < 0.28:
        final = "unknown"
    elif top - runner < 0.07:
        final = "mixed"
    else:
        final = winner

    out = dict(base)
    out["subdivision"] = final
    out["grid"] = "straight" if final == "straight-16" else "triplet" if final.startswith("triplet-") else final
    out["resolution"] = "16" if final == "straight-16" else "8T" if final == "triplet-8T" else "16T" if final == "triplet-16T" else final
    out["rhythmic_feel"] = "straight" if final == "straight-16" else "shuffle/swing" if final.startswith("triplet-") else final
    out["confidence"] = round((top - runner) / max(0.001, top + runner), 3)
    out["combined_scores"] = {kind: round(value, 3) for kind, value in scores.items()}
    out["grid_fit"] = {
        kind: {
            "aligned_ratio": round(stat["aligned_ratio"], 3),
            "aligned_percent": round(100.0 * stat["aligned_ratio"], 1),
            "mean_error_ticks": round(stat["mean_error_ticks"], 3),
            "mean_error_ratio": round(stat["mean_error_ratio"], 4),
        }
        for kind, stat in grid_fit.items()
    }
    out["strong_16t_identity"] = strong_16t_identity
    out["duration_samples"] = duration["samples"]
    out["filename_hints"] = hint["reasons"]
    out["phase_subdivision"] = base.get("subdivision", "unknown")
    return out


def analyze_event_rhythm(events: Iterable[Any], tpq: int, filename: str = "",
                         loop_ticks: int | None = None, loop_start: int | None = None) -> dict:
    """Analyze articulations and subdivision while excluding removable flam grace notes.

    The same filtered event set is used for both onset-phase and note-duration
    evidence, preventing a flam grace note from creating a false 8T/16T result.
    """
    events = list(events)
    flam_analysis = detect_flams(events, tpq, loop_ticks=loop_ticks, loop_start=loop_start)
    grace_indices = {
        item["grace_index"] for item in flam_analysis["flams"]
        if item.get("remove_from_subdivision")
    }
    rhythmic_events = [event for index, event in enumerate(events) if index not in grace_indices]
    note_ticks = [int(_get(event, "tick", 0)) for event in rhythmic_events]
    base = classify_subdivision(tpq, note_ticks)
    subdivision = combine_subdivision_evidence(base, rhythmic_events, tpq, filename)
    subdivision["excluded_flam_grace_count"] = len(grace_indices)
    return {
        "subdivision": subdivision,
        "flams": flam_analysis,
        "rhythmic_events": rhythmic_events,
        "excluded_indices": grace_indices,
    }


def triplet_vs_straight_score(tpq: int, note_ticks: list[int]) -> dict:
    """Backward-compatible public name for the shared classifier."""
    return classify_subdivision(tpq, note_ticks)


def tick_to_bar_position(tick: int, tpq: int, ts_segs: list):
    """Map an absolute tick to a 1-based bar, beat, and meter."""
    bars_before = 0
    for t0, t1, (num, den) in ts_segs:
        bar_ticks = tpq * 4.0 * num / den
        if bar_ticks <= 0:
            continue
        if tick >= t1:
            bars_before += int((t1 - t0) // bar_ticks)
            continue
        if tick >= t0:
            rel = tick - t0
            bar_in_seg = int(rel // bar_ticks)
            tick_in_bar = rel - bar_in_seg * bar_ticks
            beat_ticks = tpq * 4.0 / den
            beat = tick_in_bar / beat_ticks + 1.0
            return bars_before + bar_in_seg + 1, beat, (num, den)
    return bars_before + 1, 1.0, ts_segs[-1][2] if ts_segs else (4, 4)


def analyze_triplet_by_bar(note_ticks: list[int], tpq: int, ts_segs: list) -> list[dict]:
    ticks_by_bar: dict[int, list[int]] = defaultdict(list)
    bar_meter = {}
    for tick in note_ticks:
        bar, _beat, meter = tick_to_bar_position(tick, tpq, ts_segs)
        ticks_by_bar[bar].append(tick)
        bar_meter[bar] = meter
    results = []
    for bar in sorted(ticks_by_bar):
        ticks = sorted(set(ticks_by_bar[bar]))
        score = classify_subdivision(tpq, ticks)
        det = score["details"]
        results.append({
            "bar": bar,
            "meter": bar_meter.get(bar, (4, 4)),
            "note_positions": len(ticks),
            "samples": det["samples"],
            "anchor_hits": det["anchor_hits"],
            "shared_half_hits": det["shared_half_hits"],
            "straight_hits": det["straight_hits"],
            "triplet_hits": det["triplet_hits"],
            "triplet_8t_hits": det["triplet_8t_hits"],
            "triplet_16t_only_hits": det["triplet_16t_only_hits"],
            "triplet_hit_ratio": score["triplet_hit_ratio"],
            "straight_hit_ratio": score["straight_hit_ratio"],
            "grid": score["grid"],
            "resolution": score["resolution"],
            "subdivision": score["subdivision"],
            "triplet_candidate": score["grid"] == "triplet",
            "tol_ticks": det["tol_ticks"],
        })
    return results


def analyze_event_rhythm_by_bar(events: Iterable[Any], tpq: int, ts_segs: list, filename: str = "") -> list[dict]:
    """Apply the unified phase/duration/filename analysis independently to each bar."""
    events_by_bar: dict[int, list[Any]] = defaultdict(list)
    bar_meter = {}
    for event in events:
        tick = int(_get(event, "tick", 0))
        bar, _beat, meter = tick_to_bar_position(tick, tpq, ts_segs)
        events_by_bar[bar].append(event)
        bar_meter[bar] = meter

    results = []
    for bar in sorted(events_by_bar):
        group = events_by_bar[bar]
        analysis = analyze_event_rhythm(group, tpq, filename)
        score = analysis["subdivision"]
        details = score.get("details", {})
        results.append({
            "bar": bar,
            "meter": bar_meter.get(bar, (4, 4)),
            "note_positions": len({int(_get(event, "tick", 0)) for event in group}),
            "samples": details.get("samples", 0),
            "anchor_hits": details.get("anchor_hits", 0),
            "shared_half_hits": details.get("shared_half_hits", 0),
            "straight_hits": details.get("straight_hits", 0),
            "triplet_hits": details.get("triplet_hits", 0),
            "triplet_8t_hits": details.get("triplet_8t_hits", 0),
            "triplet_16t_only_hits": details.get("triplet_16t_only_hits", 0),
            "triplet_hit_ratio": score.get("triplet_hit_ratio", 0.0),
            "straight_hit_ratio": score.get("straight_hit_ratio", 0.0),
            "grid": score.get("grid", "unknown"),
            "resolution": score.get("resolution", "unknown"),
            "subdivision": score.get("subdivision", "unknown"),
            "observed_resolution": score.get("observed_resolution"),
            "confidence": score.get("confidence", 0.0),
            "duration_samples": score.get("duration_samples", 0),
            "triplet_candidate": score.get("grid") == "triplet",
            "tol_ticks": details.get("tol_ticks", 0),
        })
    return results


def recommended_steps_per_bar(numerator: int, denominator: int, decision=None) -> int:
    if (numerator, denominator) == (4, 4):
        steps = 16
    elif (numerator, denominator) in ((3, 4), (6, 8)):
        steps = 12
    else:
        steps = max(8, 4 * numerator)
    if decision and decision.get("grid") == "triplet" and (numerator, denominator) == (4, 4):
        steps = 24
    return int(steps)


def collect_drum_note_events(mid: MidiFile) -> list[dict]:
    """Return channel-10 note events with absolute tick and measured duration."""
    out = []
    for track_index, track in enumerate(mid.tracks):
        tick = 0
        active: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for msg in track:
            tick += msg.time
            if not isinstance(msg, Message) or getattr(msg, "channel", -1) != 9:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                active[int(msg.note)].append((tick, int(msg.velocity)))
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                note = int(msg.note)
                if active.get(note):
                    start_tick, velocity = active[note].pop(0)
                    out.append({
                        "tick": start_tick,
                        "note": note,
                        "velocity": velocity,
                        "duration": max(0, tick - start_tick),
                        "dur": max(0, tick - start_tick),
                        "family": ADT_DRUM_FAMILIES.get(note, f"N{note}"),
                        "track": track_index,
                    })
        for note, items in active.items():
            for start_tick, velocity in items:
                out.append({
                    "tick": start_tick,
                    "note": note,
                    "velocity": velocity,
                    "duration": 0,
                    "dur": 0,
                    "family": ADT_DRUM_FAMILIES.get(note, f"N{note}"),
                    "track": track_index,
                })
    out.sort(key=lambda e: (e["tick"], e["track"], e["note"]))
    return out


def detect_flams(events: Iterable[Any], tpq: int, loop_ticks: int | None = None,
                 loop_start: int | None = None) -> dict:
    """Detect conservative grace/main flam candidates by ADT drum family.

    When loop_ticks is supplied, the final event may be paired with the first
    event of the same family across the loop boundary.
    """
    normalized = []
    for index, event in enumerate(events):
        note = int(_get(event, "note", -1))
        normalized.append({
            "tick": int(_get(event, "tick", 0)),
            "note": note,
            "velocity": int(_get(event, "velocity", _get(event, "vel", 0))),
            "family": _get(event, "family", ADT_DRUM_FAMILIES.get(note, f"N{note}")),
            "track": int(_get(event, "track", 0)),
            "source_index": index,
        })
    max_gap = max(2, int(round(tpq / 8)))
    high_gap = max(2, int(round(tpq / 12)))
    by_family: dict[str, list[dict]] = defaultdict(list)
    for event in normalized:
        by_family[event["family"]].append(event)

    flams = []
    grace_keys = set()
    used_indices = set()
    for family, group in by_family.items():
        if family.startswith("N"):
            continue
        seq = sorted(group, key=lambda e: (e["tick"], e["source_index"]))
        i = 0
        while i + 1 < len(seq):
            first, second = seq[i], seq[i + 1]
            gap = second["tick"] - first["tick"]
            if gap <= 0 or gap > max_gap or first["velocity"] >= second["velocity"]:
                i += 1
                continue
            third_close = i + 2 < len(seq) and 0 < seq[i + 2]["tick"] - second["tick"] <= max_gap
            ratio = first["velocity"] / max(1, second["velocity"])
            if gap <= high_gap and ratio <= 0.75 and not third_close:
                confidence = "HIGH"
            elif ratio <= 0.90 and not third_close:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"
            removable = confidence in {"HIGH", "MEDIUM"} and not third_close
            item = {
                "family": family,
                "grace_tick": first["tick"], "main_tick": second["tick"],
                "gap_ticks": gap,
                "grace_note": first["note"], "main_note": second["note"],
                "grace_velocity": first["velocity"], "main_velocity": second["velocity"],
                "grace_index": first["source_index"], "main_index": second["source_index"],
                "confidence": confidence, "cluster_like": third_close,
                "remove_from_subdivision": removable,
                "grace_key": (first["tick"], first["note"], first["track"]),
            }
            flams.append(item)
            if removable:
                grace_keys.add(item["grace_key"])
            used_indices.update((first["source_index"], second["source_index"]))
            i += 2

        if loop_ticks and loop_ticks > 0 and len(seq) >= 2:
            first, last = seq[0], seq[-1]
            start = int(loop_start if loop_start is not None else min(e["tick"] for e in normalized))
            first_wrapped_tick = first["tick"]
            while first_wrapped_tick < start:
                first_wrapped_tick += loop_ticks
            first_wrapped_tick += loop_ticks
            gap = first_wrapped_tick - last["tick"]
            available = last["source_index"] not in used_indices and first["source_index"] not in used_indices
            if available and 0 < gap <= max_gap and last["velocity"] < first["velocity"]:
                ratio = last["velocity"] / max(1, first["velocity"] )
                confidence = "HIGH" if gap <= high_gap and ratio <= 0.75 else "MEDIUM" if ratio <= 0.90 else "LOW"
                removable = confidence in {"HIGH", "MEDIUM"}
                item = {
                    "family": family,
                    "grace_tick": last["tick"], "main_tick": first["tick"],
                    "main_tick_unwrapped": first_wrapped_tick,
                    "gap_ticks": gap,
                    "grace_note": last["note"], "main_note": first["note"],
                    "grace_velocity": last["velocity"], "main_velocity": first["velocity"],
                    "grace_index": last["source_index"], "main_index": first["source_index"],
                    "confidence": confidence, "cluster_like": False,
                    "remove_from_subdivision": removable, "across_loop": True,
                    "grace_key": (last["tick"], last["note"], last["track"]),
                }
                flams.append(item)
                if removable:
                    grace_keys.add(item["grace_key"])

    flams.sort(key=lambda x: (x.get("main_tick_unwrapped", x["main_tick"]), x["family"]))
    return {
        "flams": flams,
        "grace_keys": grace_keys,
        "grace_ticks": {key[0] for key in grace_keys},
        "settings": {"flam_max_gap_ticks": max_gap, "flam_high_gap_ticks": high_gap},
    }


def detect_drum_articulations(drum_events: list[dict], tpq: int, ts_segs: list) -> dict:
    """Detect flam/grace and ghost-like candidates without modifying MIDI data."""
    if not drum_events:
        return {"flams": [], "ghosts": [], "settings": {}}
    flam_analysis = detect_flams(drum_events, tpq)
    flams = []
    for item in flam_analysis["flams"]:
        bar, beat, meter = tick_to_bar_position(item["main_tick"], tpq, ts_segs)
        flams.append({**item, "bar": bar, "beat": beat, "meter": meter})

    by_family: dict[str, list[dict]] = defaultdict(list)
    for event in drum_events:
        by_family[event["family"]].append(event)
    ghosts = []
    family_stats = {}
    for family, group in by_family.items():
        if family not in GHOST_FAMILIES or len(group) < 3:
            continue
        med = float(median([e["velocity"] for e in group]))
        threshold = min(50, int(round(med * 0.60)))
        family_stats[family] = {"median_velocity": med, "threshold": threshold}
        for event in group:
            if event["velocity"] > threshold:
                continue
            key = (event["tick"], event["note"], event["track"])
            bar, beat, meter = tick_to_bar_position(event["tick"], tpq, ts_segs)
            ghosts.append({
                "bar": bar, "beat": beat, "meter": meter, "family": family,
                "tick": event["tick"], "note": event["note"], "velocity": event["velocity"],
                "threshold": threshold, "median_velocity": med,
                "flam_grace": key in flam_analysis["grace_keys"],
            })
    ghosts.sort(key=lambda x: (x["tick"], x["family"]))
    settings = dict(flam_analysis["settings"])
    settings["ghost_family_stats"] = family_stats
    return {"flams": flams, "ghosts": ghosts, "settings": settings}


def analyze_midi_rhythm(mid: MidiFile, ts_segs: list, filename: str = "") -> dict:
    """Analyze one MIDI file through the same unified event-rhythm engine."""
    drum_events = collect_drum_note_events(mid)
    if drum_events:
        loop_start = min(event["tick"] for event in drum_events)
        loop_end = max(event["tick"] + max(1, int(event.get("duration", 0))) for event in drum_events)
        if ts_segs:
            loop_start = min(loop_start, int(ts_segs[0][0]))
            loop_end = max(loop_end, int(ts_segs[-1][1]))
        loop_ticks = max(1, loop_end - loop_start)
    else:
        loop_start = 0
        loop_ticks = max(1, int(ts_segs[-1][1])) if ts_segs else 1

    event_analysis = analyze_event_rhythm(
        drum_events,
        mid.ticks_per_beat,
        filename,
        loop_ticks=loop_ticks,
        loop_start=loop_start,
    )
    articulations = detect_drum_articulations(drum_events, mid.ticks_per_beat, ts_segs)
    # Preserve loop-boundary flam candidates from the unified event analysis.
    loop_flams = event_analysis["flams"]["flams"]
    if loop_flams:
        enriched = []
        for item in loop_flams:
            main_tick = item.get("main_tick", 0)
            bar, beat, meter = tick_to_bar_position(main_tick, mid.ticks_per_beat, ts_segs)
            enriched.append({**item, "bar": bar, "beat": beat, "meter": meter})
        articulations = dict(articulations)
        articulations["flams"] = enriched
        settings = dict(articulations.get("settings", {}))
        settings.update(event_analysis["flams"].get("settings", {}))
        articulations["settings"] = settings

    rhythmic_events = event_analysis["rhythmic_events"]
    bars = analyze_event_rhythm_by_bar(rhythmic_events, mid.ticks_per_beat, ts_segs, filename)
    return {
        "ticks": [int(event["tick"]) for event in rhythmic_events],
        "events": drum_events,
        "subdivision": event_analysis["subdivision"],
        "bars": bars,
        "articulations": articulations,
    }