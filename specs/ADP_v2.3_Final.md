# ADP Specification v2.3 Final

**The ADX Platform for Drum Patternology**

Version: **2.3 Final**  
Status: **Final Public Specification**  
Created: **2026-08-01**  
Last Updated: **2026-08-06**

---

## 1. Overview

ADP (*Ardule Drum Pattern*) is the compact binary cache format of the ADX Platform.

An ADP file is generated from an ADT v2.3 Final file. It preserves the regular step grid, slot indices, and five playable accent levels in a representation intended for efficient storage and playback.

ADP is a cache, not the canonical editable source. ADT remains the human-readable source format. Ornament events that cannot be represented by the selected regular grid are stored separately in a same-basename ORN sidecar.

Because ADP is derived data, files generated from earlier ADP v2.3 drafts shall be regenerated from their authoritative ADT sources.

---

## 2. Byte Order and Limits

All multi-byte integer fields use **little-endian** byte order.

An ADP v2.3 Final file consists of:

```text
12-byte header
variable-length payload
```

Format limits:

- `LENGTH`: 1–255 steps
- slot index: 0–15
- registered slot-map ID: 0–254
- inline slot-map ID: 255
- payload size: 0–65535 bytes
- per-step hit count: 0–255
- stored accent value: 1–5

The packed-hit representation supports at most 16 slots.

---

## 3. Header

The ADP v2.3 Final header is exactly 12 bytes.

The magic value **`ADP3`** identifies the **third-generation ADP binary file format**. It identifies the binary container family rather than the specification revision itself.

The actual specification revision is carried independently in the **Version** field. This separation allows future revisions of the ADP3 binary format while retaining the same file-family identifier.


| Offset | Size | Field | Description |
|---:|---:|---|---|
| `0x00` | 4 | Magic | ASCII `ADP3` |
| `0x04` | 1 | Version | Decimal `23` |
| `0x05` | 1 | SUBDIV code | `0` = `16`, `1` = `32`, `2` = `8T`, `3` = `16T` |
| `0x06` | 1 | LENGTH | Number of steps |
| `0x07` | 1 | SLOT_MAP_ID | Registered numeric ID, or `255` for `INLINE` |
| `0x08` | 2 | Payload Bytes | Payload length in bytes |
| `0x0A` | 2 | Payload CRC16 | CRC16-CCITT of the payload only |

The header corresponds to the following little-endian structure:

```text
<4sBBBBHH
```

A reader shall reject a file when:

- the magic is not `ADP3`;
- the version is not `23`;
- the subdivision code is unknown;
- `LENGTH` is zero;
- the actual payload length differs from `Payload Bytes`; or
- the calculated payload CRC differs from `Payload CRC16`.

### 3.1 SUBDIV code table

| Code | ADT `SUBDIV` value | Grid meaning | Steps per quarter note |
|---:|---|---|---:|
| `0` | `16` | Straight sixteenth-note grid | 4 |
| `1` | `32` | Straight thirty-second-note grid | 8 |
| `2` | `8T` | Eighth-note triplet grid | 3 |
| `3` | `16T` | Sixteenth-note triplet grid | 6 |

The converter shall preserve the `SUBDIV` value recorded in the source ADT file. It shall not reclassify the pattern resolution.

---

## 4. Payload

The payload contains exactly `LENGTH` step records in chronological order.

Each step is encoded as:

```text
u8 hit_count
hit_count × u8 packed_hit
```

A silent step therefore occupies one byte:

```text
00
```

Hits within a step are written in ascending slot-index order by the reference writer.

### 4.1 Packed hit

Each hit occupies one byte:

```text
packed_hit = (slot_index << 3) | accent
```

Bit layout:

```text
bit 7      reserved; shall be 0
bits 6–3   slot index (0–15)
bits 2–0   accent level (1–5)
```

The fields are decoded as:

```text
slot_index = packed_hit >> 3
accent     = packed_hit & 0x07
```

A conforming writer shall never encode accent value `0` as a packed hit. Rest is represented by the absence of a hit for that slot in that step.

A conforming reader shall reject:

- packed hits with bit 7 set;
- slot indices outside the selected slot map;
- accent value `0`;
- accent values `6` or `7`;
- duplicate slot indices within one step record.

### 4.2 Accent levels

ADP preserves the five playable ADT v2.3 Final accent levels without collapsing them.

| Value | ADT symbol | Meaning | Stored in payload |
|---:|---|---|---|
| `0` | `.` | Rest | No |
| `1` | `-` | Very Weak / Ghost | Yes |
| `2` | `x` | Weak | Yes |
| `3` | `o` | Medium | Yes |
| `4` | `^` | Strong | Yes |
| `5` | `@` | Accent | Yes |

The authoritative velocity ranges and representative MIDI velocities are defined outside ADP in `accent_levels.json`.

ADP stores only the discrete accent value. It does not store the original MIDI velocity, velocity range, representative velocity, label, or textual symbol.

The reference ADT-to-ADP converter shall obtain symbol-to-level mappings from the `6-accent` scheme in `accent_levels.json`. It shall not maintain a separate hard-coded accent conversion table.

---

## 5. Slot Maps

`SLOT_MAP_ID` identifies how packed slot indices are interpreted.

### 5.1 Registered maps

Values 0–254 identify registered maps in `slot_map_definitions.json`.

The current default map is:

```text
0 = LEGACY
```

An ADP reader shall use the registered map corresponding to the numeric ID. ADP does not embed registered slot definitions.

### 5.2 Inline maps

Value `255` means:

```text
SLOT_MAP_ID = INLINE
```

An INLINE ADP shall be accompanied by a same-basename ADT file in the same directory:

```text
ABC_0001.ADP
ABC_0001.ADT
```

The companion ADT supplies the `SLOT0...SLOTn` definitions required to interpret slot indices. The reference converter copies the source ADT beside the generated ADP only for INLINE slot maps.

---

## 6. CRC16-CCITT

`Payload CRC16` is calculated over the payload bytes only.

Parameters:

```text
Polynomial: 0x1021
Initial value: 0xFFFF
Input reflection: none
Output reflection: none
Final XOR: none
```

Reference pseudocode:

```text
crc = 0xFFFF

for each byte:
    crc = crc XOR (byte << 8)

    repeat 8 times:
        if crc bit 15 is set:
            crc = ((crc << 1) XOR 0x1021) AND 0xFFFF
        else:
            crc = (crc << 1) AND 0xFFFF
```

The 16-bit result is stored little-endian in the header.

---

## 7. File Association

The pattern identifier is carried by the filename rather than by the ADP header.

Example:

```text
WLZ_0005.ADP
```

Related files use the same basename:

```text
WLZ_0005.ADT
WLZ_0005.ADP
WLZ_0005.ORN
```

A same-basename ORN file is optional. It adds only ornament events that remain outside the selected ADT/ADP grid.

Grid-representable notes shall already be present in ADT and ADP and shall not be duplicated in ORN.

ADP v2.3 Final does not store fields such as `NAME`, `SOURCE`, `TIME_SIG`, `PPQN`, `KIT`, or textual slot definitions in its 12-byte header.

---

## 8. Example: WLZ_0005.ADP

The following example represents the same 24-step, 12-slot `LEGACY` pattern used by the ADT reference example. Every non-rest hit has accent value `3` (`o`, Medium).

The file is 50 bytes long:

```text
Header  : 12 bytes
Payload : 38 bytes
Total   : 50 bytes
```

Decoded header:

| Field | Value |
|---|---|
| Magic | `ADP3` |
| Version | `23` |
| SUBDIV code | `0` (`16`) |
| LENGTH | `24` |
| SLOT_MAP_ID | `0` (`LEGACY`) |
| Payload Bytes | `38` |
| Payload CRC16 | `0x9F48` |

Complete hexadecimal representation:

```text
41 44 50 33 17 00 18 00 26 00 48 9F 01 0B 00 00
00 02 13 2B 00 01 2B 00 02 13 23 00 01 23 00 01
0B 00 00 00 02 13 2B 00 01 2B 00 02 13 23 00 01
23 00
```

Non-empty steps decoded from the payload:

- Step 0: slot 1, accent 3 (`0x0B`)
- Step 4: slot 2, accent 3 (`0x13`), slot 5, accent 3 (`0x2B`)
- Step 6: slot 5, accent 3 (`0x2B`)
- Step 8: slot 2, accent 3 (`0x13`), slot 4, accent 3 (`0x23`)
- Step 10: slot 4, accent 3 (`0x23`)
- Step 12: slot 1, accent 3 (`0x0B`)
- Step 16: slot 2, accent 3 (`0x13`), slot 5, accent 3 (`0x2B`)
- Step 18: slot 5, accent 3 (`0x2B`)
- Step 20: slot 2, accent 3 (`0x13`), slot 4, accent 3 (`0x23`)
- Step 22: slot 4, accent 3 (`0x23`)

All other steps have `hit_count = 0`.

For example:

```text
0x0B = (1 << 3) | 3
```

Therefore `0x0B` means slot 1 with accent level 3.

The calculated CRC16-CCITT of the 38-byte payload is:

```text
0x9F48
```

which matches the header bytes:

```text
48 9F
```

---

## 9. Conformance Requirements

A conforming ADP v2.3 Final writer shall:

- emit the exact 12-byte header described above;
- encode SUBDIV codes as `0=16`, `1=32`, `2=8T`, `3=16T`;
- encode one step record for every step;
- omit rests from hit lists;
- restrict slot indices to 0–15;
- restrict stored accents to 1–5;
- encode each hit as `(slot_index << 3) | accent`;
- keep bit 7 of every packed hit clear;
- write hits in ascending slot-index order;
- avoid duplicate slot indices in a step;
- store the correct payload byte count;
- calculate CRC16-CCITT over the payload only; and
- use slot-map ID 255 only for INLINE maps.

A conforming reader shall:

- validate the header, payload size, and CRC;
- recognize all four SUBDIV codes;
- decode exactly `LENGTH` step records;
- reject truncated or trailing payload data;
- reject packed hits with reserved bit 7 set;
- reject packed hits with accent 0, 6, or 7;
- reject duplicate slot indices within a step;
- resolve registered slot maps by numeric ID; and
- require a same-basename companion ADT when `SLOT_MAP_ID=255`.

---

## 10. Compatibility and Migration

ADP v2.3 Final changes the packed-hit layout used by earlier development drafts:

```text
Draft layout: packed_hit = (slot_index << 2) | accent
Final layout: packed_hit = (slot_index << 3) | accent
```

It also changes the SUBDIV code assignments by inserting Straight-32:

```text
Final:
0 = 16
1 = 32
2 = 8T
3 = 16T
```

Consequently, ADP files generated from pre-Final v2.3 drafts are not binary-compatible with ADP v2.3 Final, even though they may contain the same `ADP3` magic and version byte `23`.

Because ADP is a derived binary cache, the required migration procedure is:

1. retain the authoritative ADT file;
2. delete or archive the old draft ADP file;
3. regenerate ADP using the Final reference converter;
4. regenerate any test vectors and CRC values;
5. use a Final-compatible player or reader.

No automatic in-place conversion of draft ADP payloads is required by this specification.

---

## 11. Reference Implementation

The Final reference encoder is:

```text
adc-adt2adp.py
```

Its default workflow is:

```text
./ADT/*.ADT -> ./ADP/*.ADP
```

The encoder shall:

- read `SUBDIV` directly from ADT;
- support `16`, `32`, `8T`, and `16T`;
- read the `6-accent` symbol mapping from `accent_levels.json`;
- encode accents 1–5 without collapsing levels;
- resolve registered maps through `slot_map_definitions.json`;
- copy the companion ADT only when `SLOT_MAP_ID=INLINE`;
- calculate and write the payload CRC16; and
- reject obsolete or unsupported ADT symbols rather than silently remapping them.

---

## 12. Relationship to Other ADX Formats

- **ADT v2.3 Final** is the canonical human-readable grid representation.
- **ADP v2.3 Final** is the compact binary cache generated from ADT.
- **ORN v1.0** is an optional same-basename sidecar containing ornament and microtiming events outside the selected grid.

ADT remains authoritative. ADP accelerates storage and playback. ORN supplements the regular grid but does not replace or duplicate it.
