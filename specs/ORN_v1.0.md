# ORN Specification v1.0

**The ADX Platform for Drum Patternology**

Version: **1.0**  
Status: **Current**  
Created: **2026-08-01**  
Last Updated: **2026-08-01**

---

## 1. Purpose

ORN is the human-readable ornament sidecar format of the ADX Platform.

ADT and ADP describe a regular rhythmic grid. That grid is intentionally compact and abstract: each stored hit belongs to a discrete step and slot. Some performance details, however, occur outside the regular grid and should not be forced into a neighboring step.

ORN preserves those details separately.

In ORN v1.0, the defined ornament event is a flam grace note. A detected grace note is excluded from ADT/ADP grid quantization and stored in ORN with its tick offset, velocity, target step, and slot family. During playback, the ADT or ADP regular grid is combined with the optional same-basename ORN sidecar.

This separation has two purposes:

- ornament notes do not distort subdivision analysis or regular-grid quantization;
- the original performance intention can still be reproduced during playback.

A pattern that contains no supported ornament events does not require an ORN file.

---

## 2. Relationship to ADT and ADP

Related pattern files use the same basename:

```text
WLZ_0005.ADT
WLZ_0005.ADP
WLZ_0005.ORN
```

Their responsibilities are distinct:

```text
ADT  canonical human-readable regular grid
ADP  compact binary cache of the regular grid
ORN  optional sidecar for events outside the regular grid
```

ORN does not replace ADT or ADP. It supplements either representation during playback.

ORN does not contain the main flam hit. The main hit remains in the regular grid. ORN stores only the grace event that decorates that target hit.

---

## 3. Encoding and General Syntax

ORN v1.0 is a UTF-8 text format.

The first line shall be exactly:

```text
; ORN v1.0
```

Blank lines may be ignored.

Lines beginning with `;` are comments. A semicolon may also introduce a trailing comment on an event line.

The file contains:

```text
version declaration
optional identifying comments
required metadata fields
[EVENTS] marker
zero or more event records
```

The reference writer emits ORN files only when at least one supported ornament event has been detected.

---

## 4. Metadata Fields

The following fields appear before `[EVENTS]`.

| Field | Required | Meaning |
|---|---|---|
| `UNIT` | Yes | Timing unit. ORN v1.0 uses `TICK`. |
| `SUBDIV` | Yes | Grid subdivision inherited from the matching pattern: `16`, `8T`, or `16T`. |
| `LENGTH` | Yes | Number of regular-grid steps in the matching ADT/ADP pattern. |
| `LOOP_TICKS` | Yes | Total pattern length in canonical ticks. |

The reference writer also emits identifying comments:

```text
; NAME=ABC_0001
; SOURCE=original.mid:bars
```

These comment fields are informative. File association is determined primarily by the same basename.

### 4.1 Canonical tick base

ORN timing uses the ADX canonical coordinate system:

```text
PPQN = 240
UNIT = TICK
```

ORN does not store a separate `PPQN` field. The canonical tick base is inherited by definition.

The number of canonical ticks per step is:

| SUBDIV | Steps per quarter note | Ticks per step |
|---|---:|---:|
| `16` | 4 | 60 |
| `8T` | 3 | 80 |
| `16T` | 6 | 40 |

`LOOP_TICKS` shall equal:

```text
LENGTH × ticks_per_step
```

---

## 5. Event Section

The event section begins with:

```text
[EVENTS]
```

ORN v1.0 defines the `FLAM` record:

```text
FLAM TARGET_STEP=<n> SLOT=<name> OFFSET_TICKS=<signed> VELOCITY=<1..127> [LOOP_WRAP=1]
```

A trailing comment may follow:

```text
; confidence=HIGH
```

### 5.1 `TARGET_STEP`

`TARGET_STEP` identifies the regular-grid step containing the main hit decorated by the grace note.

Valid range:

```text
0 <= TARGET_STEP < LENGTH
```

Steps are zero-based.

### 5.2 `SLOT`

`SLOT` identifies the drum slot or instrument family of the flam.

The value shall correspond to the slot identity used by the matching pattern and player, such as:

```text
SN
```

The current reference writer obtains this value from the shared flam analysis engine's instrument-family result.

### 5.3 `OFFSET_TICKS`

`OFFSET_TICKS` is the signed tick displacement of the grace note relative to the target grid step.

```text
grace_tick = target_step_tick + OFFSET_TICKS
```

For an ordinary flam, the grace note normally precedes the main hit, so the value is negative.

Example:

```text
TARGET_STEP=5 OFFSET_TICKS=-30
```

means that the grace note occurs 30 canonical ticks before step 5.

### 5.4 `VELOCITY`

`VELOCITY` is the original MIDI velocity of the grace note.

Valid range:

```text
1..127
```

Unlike ADT/ADP accent levels, ORN preserves the grace note's continuous MIDI velocity.

### 5.5 `LOOP_WRAP`

`LOOP_WRAP=1` marks an ornament whose grace note lies near the end of the pattern but decorates the target hit at the beginning of the next loop iteration.

For a loop-boundary flam:

```text
TARGET_STEP=0
OFFSET_TICKS=<negative value>
LOOP_WRAP=1
```

The playback time of the grace note is interpreted modulo `LOOP_TICKS`.

Conceptually:

```text
event_tick = (TARGET_STEP × ticks_per_step + OFFSET_TICKS) mod LOOP_TICKS
```

Thus a negative offset from step 0 is placed near the end of the preceding loop while still belonging musically to the next step-0 main hit.

`LOOP_WRAP` is omitted for ordinary within-loop ornaments.

### 5.6 Confidence comment

The reference writer appends the detector confidence as a non-normative trailing comment:

```text
; confidence=HIGH
```

Confidence is diagnostic metadata. A player need not interpret it.

---

## 6. Event Ordering

The reference writer sorts events by:

```text
TARGET_STEP
SLOT
OFFSET_TICKS
VELOCITY
```

A reader should not depend on this ordering and may process valid records in any order.

---

## 7. Example: WLZ_0005.ORN

The supplied reference file is:

```text
; ORN v1.0
; NAME=WLZ_0005
; SOURCE=6WALTZ.MID:9-10
UNIT=TICK
SUBDIV=16
LENGTH=24
LOOP_TICKS=1440

[EVENTS]
FLAM TARGET_STEP=0 SLOT=SN OFFSET_TICKS=-30 VELOCITY=40 LOOP_WRAP=1 ; confidence=MEDIUM
FLAM TARGET_STEP=12 SLOT=SN OFFSET_TICKS=-30 VELOCITY=40 ; confidence=MEDIUM
```

Decoded metadata:

| Field | Value |
|---|---|
| Name comment | `WLZ_0005` |
| Source comment | `6WALTZ.MID:9-10` |
| UNIT | `TICK` |
| SUBDIV | `16` |
| LENGTH | `24` |
| LOOP_TICKS | `1440` |
| Event count | `2` |

Decoded event:

- `FLAM` targeting step `0`, slot `SN`, offset `-30` ticks, velocity `40`, with loop wrapping (`confidence=MEDIUM`)
- `FLAM` targeting step `12`, slot `SN`, offset `-30` ticks, velocity `40` (`confidence=MEDIUM`)

This example contains a loop-boundary flam. The grace note occurs before the first step of the next loop iteration and is therefore stored with `TARGET_STEP=0`, a negative `OFFSET_TICKS`, and `LOOP_WRAP=1`.

The matching ADT/ADP grid shall not contain this grace note as an independent final-step hit.

---

## 8. Generation Policy

The reference ORN writer uses:

```text
reviewed PatternLab CSV
+
original unsplit MIDI file
```

Only CSV rows satisfying both conditions are processed:

```text
EXPORT=YES
ORN=YES
```

The selected `START_BAR..END_BAR` range identifies the pattern in the original MIDI file.

Flam candidates are detected by the shared `adc_rhythm_analysis.py` engine. Only candidates marked for removal from subdivision analysis are emitted as ORN events. This includes supported high- or medium-confidence flam candidates and loop-boundary flam grace notes.

Source MIDI ticks are converted to canonical PPQN 240 coordinates before writing ORN.

---

## 9. Conformance Requirements

A conforming ORN v1.0 writer shall:

- emit `; ORN v1.0` as the exact first line;
- use `UNIT=TICK`;
- write `SUBDIV`, `LENGTH`, and `LOOP_TICKS`;
- use the canonical PPQN 240 coordinate system;
- write `[EVENTS]` before event records;
- keep target steps within `0..LENGTH-1`;
- preserve grace-note velocity in `1..127`;
- use a signed tick offset relative to the target step;
- mark cross-loop events with `LOOP_WRAP=1`; and
- exclude the same grace event from the matching ADT/ADP regular grid.

A conforming reader shall:

- associate ORN with a same-basename ADT or ADP pattern;
- validate `SUBDIV`, `LENGTH`, and `LOOP_TICKS` against the matching pattern;
- interpret `OFFSET_TICKS` in canonical ticks;
- apply loop wrapping when `LOOP_WRAP=1`;
- ignore comments and unknown trailing comment metadata; and
- reject unsupported event types unless an extension policy explicitly permits them.

---

## 10. Scope of ORN v1.0

ORN v1.0 formally defines flam grace events only.

The sidecar design allows future versions to add other performance details, but unregistered event types are outside this specification. Potential future extensions may include additional grace structures, drags, ruffs, or other microtiming annotations.

Such extensions shall not change the central division of responsibility:

```text
ADT/ADP = regular grid
ORN     = performance details outside the regular grid
```

---

## 11. Reference Implementation

The reference writer is:

```text
adc-orn-writer.py 260801a
```

Default workflow:

```text
PatternLab CSV + original MIDI -> ./ADP/NAME.ORN
```

The reference implementation uses `adc_rhythm_analysis.py` for flam detection and writes only patterns explicitly selected by the reviewed CSV.
