# ADT Specification v2.3

**The ADX Platform for Drum Patternology**  
**Format name:** Ardule Drum Text (ADT)  
**Version:** 2.3  
**Status:** Current  
**Created:** 2026-08-02

---

## 1. Overview

ADT is the canonical, human-readable drum-pattern format of the ADX Platform. It represents a pattern as a regular step grid with one character per instrument slot.

ADT stores the abstracted grid pattern. Timing details and ornament events that cannot be represented faithfully on that grid, such as flam grace notes, belong in a same-basename ORN sidecar rather than in the ADT data grid.

An ADT file normally uses the `.ADT` extension.

---

## 2. Text encoding and general syntax

- An ADT file is UTF-8 text.
- The first line shall declare the format version exactly as:

  ```text
  ; ADT v2.3
  ```

- A line beginning with `;` is a comment.
- Blank lines shall be ignored.
- Header fields use `KEY=VALUE` syntax.
- Field names are uppercase in the reference writer.
- The `[DATA]` marker ends the header and begins the pattern grid.
- The reference writer terminates the file with a final newline.

The standard comment preamble emitted by the reference writer is:

```text
; ADT v2.3
; Drum Pattern Exchange Format
; Lines beginning with ';' are comments.
; Blank lines shall be ignored.
; The first line shall declare the ADT version.
```

---

## 3. File structure

An ADT v2.3 file has the following logical structure:

```text
; ADT v2.3
; optional comments

NAME=...
SOURCE=...
PPQN=...

TIME_SIG=...
SUBDIV=...
LENGTH=...
KIT=...

SLOT_MAP_ID=...
ORIENTATION=...
SLOT0=...
SLOT1=...
...

[DATA]
...
```

Only fields required by the selected options need to be written. Default-valued fields are normally omitted.

---

## 4. Header fields

### 4.1 `NAME`

```text
NAME=WLZ_0005
```

Required. The reference implementation uses the following form:

```text
ABC_0001
```

The accepted pattern is:

```text
^[A-Z0-9]{3}_[0-9]{4}$
```

The ADT filename should use the same basename, for example `WLZ_0005.ADT`.

### 4.2 `SOURCE`

```text
SOURCE=6WALTZ.MID:9-10
```

Optional. Records the source MIDI file and source range used to derive the pattern. ADT does not assign a deeper machine-readable structure to this value; it is provenance text preserved by the toolchain.

### 4.3 `PPQN`

```text
PPQN=240
```

Optional when the value is `240`, which is the ADT v2.3 default used by the reference writer. It shall be written when the source pattern uses another ticks-per-quarter-note value or when explicit output is requested.

`PPQN` provides the timing reference used when ADT is combined with tick-based companion data such as ORN.

### 4.4 `TIME_SIG`

```text
TIME_SIG=3/4
```

Required. Specifies the musical time signature as `numerator/denominator`.

Examples:

```text
TIME_SIG=4/4
TIME_SIG=3/4
TIME_SIG=6/8
```

### 4.5 `SUBDIV`

```text
SUBDIV=16
```

Required. Defines the number and type of grid divisions per quarter note.

| Value | Meaning | Steps per quarter note | Step length at PPQN 240 |
|---|---|---:|---:|
| `16` | straight sixteenth-note grid | 4 | 60 ticks |
| `8T` | eighth-note triplet grid | 3 | 80 ticks |
| `16T` | sixteenth-note triplet grid | 6 | 40 ticks |

The step duration is:

```text
step_ticks = PPQN / steps_per_quarter
```

### 4.6 `LENGTH`

```text
LENGTH=24
```

Required. Specifies the total number of grid steps in the pattern.

For a complete pattern, `LENGTH`, `TIME_SIG`, `SUBDIV`, and the number of measures shall describe the same duration. For example, two measures of `3/4` at `SUBDIV=16` contain:

```text
2 measures × 3 quarter notes × 4 steps = 24 steps
```

### 4.7 `KIT`

```text
KIT=GM_STD
```

Optional. Identifies the intended drum kit. The default is:

```text
GM_STD
```

The reference writer omits `KIT` when its value is `GM_STD`.

### 4.8 `SLOT_MAP_ID`

```text
SLOT_MAP_ID=LEGACY
```

Optional when the value is `LEGACY`, which is the default.

The value identifies the slot map that determines:

- the number of slots;
- the order of characters in each step row;
- the mapping from MIDI drum notes to ADT slots;
- the slot abbreviations used by companion formats.

Registered slot maps are defined outside the ADT file, normally in `slot_map_definitions.json`.

When the slot map is `INLINE`, the file shall also contain consecutive `SLOT0` through `SLOTn` definitions.

### 4.9 `ORIENTATION`

```text
ORIENTATION=STEP
```

Optional when the value is `STEP`, which is the default.

Permitted values are:

| Value | Data layout |
|---|---|
| `STEP` | one row per step; one character per slot |
| `SLOT` | one row per slot; one character per step |

### 4.10 Inline slot definitions

Inline definitions are required only when:

```text
SLOT_MAP_ID=INLINE
```

Syntax:

```text
SLOTn=ABBREV@MIDI_NOTE,EXTENDED_NAME
```

Example:

```text
SLOT_MAP_ID=INLINE
SLOT0=KK@36,KICK
SLOT1=SN@38,SNARE
```

Requirements:

- `n` starts at `0`;
- slot indices are contiguous;
- `ABBREV` is the compact slot identifier;
- `MIDI_NOTE` is the representative MIDI note;
- `EXTENDED_NAME` is the extended slot name;
- the order of inline definitions determines the data-column order.

---

## 5. Pattern data

The pattern begins after:

```text
[DATA]
```

Each data character represents the state of one slot at one grid step.

### 5.1 Cell symbols

The reference writer uses the following symbols:

| Symbol | Meaning | Default MIDI-velocity range |
|---|---|---:|
| `.` | no hit | — |
| `-` | weak hit | 1–63 |
| `x` / `X` | medium hit | 64–95 |
| `o` / `O` / `^` | strong hit | 96–127 |

The default velocity thresholds are `64,96`.

ADT defines four discrete accent values:

| Accent value | Meaning |
|---:|---|
| 0 | Rest |
| 1 | Weak |
| 2 | Medium |
| 3 | Strong |

Only the three non-zero values represent playable hit levels.

The reference writer emits the canonical symbols:

- `.`
- `-`
- `x`
- `o`

Readers should additionally accept the following compatibility symbols:

- `X` → Medium
- `O` → Strong
- `^` → Strong

### 5.2 `STEP` orientation

With `ORIENTATION=STEP`, each row represents one step and each character represents one slot.

Requirements:

- the number of data rows shall equal `LENGTH`;
- every row shall contain exactly the number of characters defined by the selected slot map;
- character position `0` represents slot `0`, position `1` represents slot `1`, and so on.

Example with a 12-slot map:

```text
.o..........
............
```

The first row contains a normal hit (`o`) in slot 1. The second row contains no hits.

### 5.3 `SLOT` orientation

With `ORIENTATION=SLOT`, each row represents one slot and each character represents one step.

Requirements:

- the number of rows shall equal the number of slots;
- every row shall contain exactly `LENGTH` characters;
- row `0` represents slot `0`, row `1` represents slot `1`, and so on.

`STEP` and `SLOT` orientations are transposed representations of the same logical grid.

---

## 6. Grid and ornament separation

ADT represents only the regular quantized grid.

A conservative flam grace note shall not be written as an independent ADT grid hit when it decorates a main hit. This rule also applies at the loop boundary: a grace note near the end of the pattern may decorate the first main hit of the next repetition.

Such timing information belongs in a same-basename ORN file, for example:

```text
WLZ_0005.ADT
WLZ_0005.ORN
```

This separation prevents ornament notes from:

- being mistaken for ordinary grid hits;
- contaminating subdivision classification;
- being duplicated during ADT/ORN playback;
- being clamped incorrectly into the final ADT step.

---

## 7. Validation requirements

A conforming ADT v2.3 file shall satisfy the following conditions:

1. The first line is `; ADT v2.3`.
2. `NAME`, `TIME_SIG`, `SUBDIV`, and `LENGTH` are present.
3. `NAME` follows the supported pattern naming convention.
4. `SUBDIV` is one of `16`, `8T`, or `16T`.
5. `LENGTH` is a positive integer.
6. `ORIENTATION`, when present, is `STEP` or `SLOT`.
7. `SLOT_MAP_ID`, when omitted, resolves to `LEGACY`.
8. `SLOT_MAP_ID=INLINE` is accompanied by valid contiguous inline slot definitions.
9. The dimensions of `[DATA]` match `LENGTH`, orientation, and slot count.
10. Reference writers shall emit only `.`, `-`, `x`, and `o`.

    Readers shall additionally accept `X`, `O`, and `^` for compatibility.
11. Ornament-only grace events are not duplicated in the regular grid.

Readers should ignore blank lines and comment lines. Unknown header fields should not silently change the interpretation of defined v2.3 fields.

---

## 8. Complete example: `WLZ_0005.ADT`

The following is the reference example supplied with ADT v2.3. It represents a two-measure `3/4` pattern on a straight sixteenth-note grid using the default PPQN, kit, slot map, and orientation. The omitted defaults are therefore `PPQN=240`, `KIT=GM_STD`, `SLOT_MAP_ID=LEGACY`, and `ORIENTATION=STEP`.

```text
; ADT v2.3
; Drum Pattern Exchange Format
; Lines beginning with ';' are comments.
; Blank lines shall be ignored.
; The first line shall declare the ADT version.

NAME=WLZ_0005
SOURCE=6WALTZ.MID:9-10

TIME_SIG=3/4
SUBDIV=16
LENGTH=24

[DATA]
.o..........
............
............
............
..o..o......
............
.....o......
............
..o.o.......
............
....o.......
............
.o..........
............
............
............
..o..o......
............
.....o......
............
..o.o.......
............
....o.......
............
```

The file contains exactly 24 step rows. Each row contains 12 characters because the example uses the 12-slot `LEGACY` slot map.

The original performance also contains flam grace notes. They are intentionally absent from this ADT grid and are preserved separately in `WLZ_0005.ORN`.

---

## 9. Reference implementation

The initial ADT v2.3 reference writer is:

```text
adc-mid2adt.py 260801g
```

Its relevant behavior includes:

- default `PPQN=240`;
- valid subdivisions `16`, `8T`, and `16T`;
- default `KIT=GM_STD`;
- default `SLOT_MAP_ID=LEGACY`;
- default `ORIENTATION=STEP`;
- canonical symbols `.`, `-`, `x`, and `o`;
- compatibility support for `X`, `O`, and `^`;
- exclusion of conservative flam grace notes, including loop-boundary grace notes, from the ADT grid.

---

## 10. Relationship to other ADX formats

- **ADT v2.3** is the canonical human-readable grid representation.
- **ADP v2.3** is the compact binary cache generated from ADT for efficient storage and playback.
- **ORN v1.0** is an optional same-basename sidecar that preserves ornament and microtiming events not represented in the ADT grid.

ADT remains authoritative for the pattern structure and slot interpretation. ADP accelerates playback; ORN supplements, but does not replace, the grid.
