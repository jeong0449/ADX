# ADP Specification v2.3

**The ADX Platform for Drum Patternology**

Version: **2.3**  
Status: **Current**  
Created: **2026-08-01**  
Last Updated: **2026-08-01**

---

## 1. Overview

ADP (*Ardule Drum Pattern*) is the compact binary cache format of the ADX Platform.

An ADP file is generated from an ADT v2.3 file. It preserves the regular step grid, slot indices, and three stored accent levels in a representation intended for efficient storage and playback.

ADP is a cache, not the canonical editable source. ADT remains the human-readable source format. Ornament events that cannot be represented by the regular grid are stored separately in a same-basename ORN sidecar.

---

## 2. Byte Order and Limits

All multi-byte integer fields use **little-endian** byte order.

An ADP v2.3 file consists of:

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

The packed-hit representation supports at most 16 slots.

---

## 3. Header

The ADP v2.3 header is exactly 12 bytes.

| Offset | Size | Field | Description |
|---:|---:|---|---|
| `0x00` | 4 | Magic | ASCII `ADP3` |
| `0x04` | 1 | Version | Decimal `23` |
| `0x05` | 1 | SUBDIV code | `0` = `16`, `1` = `8T`, `2` = `16T` |
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
packed_hit = (slot_index << 2) | accent
```

Bit layout:

```text
bits 7–6  reserved by the 0–15 slot-index limit
bits 5–2  slot index (0–15)
bits 1–0  accent level
```

The fields are decoded as:

```text
slot_index = packed_hit >> 2
accent     = packed_hit & 0x03
```

### 4.2 Accent levels

| Value | Meaning | ADT symbols accepted by the reference writer |
|---:|---|---|
| `0` | Rest | `.`; omitted from ADP payload |
| `1` | Weak | `-` |
| `2` | Medium | `x`, `X` |
| `3` | Strong | `o`, `O`, `^` |

Only accent values 1–3 are stored as hits. Accent value 0 represents no hit and shall not appear as a packed hit.

ADT may use multiple textual symbols that collapse to the same ADP accent value. ADP therefore preserves the playable accent level, not the exact original ADT character.

---

## 5. Slot Maps

`SLOT_MAP_ID` identifies how packed slot indices are interpreted.

### 5.1 Registered maps

Values 0–254 identify registered maps in `slot_map_definitions.json`.

The current default map is:

```text
0 = LEGACY
```

An ADP reader shall use the registered map corresponding to the numeric ID. ADP does not embed the registered slot definitions.

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

A same-basename ORN file is optional. It adds ornament events such as flam grace notes without altering the ADP regular grid.

ADP v2.3 does not store fields such as `NAME`, `SOURCE`, `TIME_SIG`, `PPQN`, `KIT`, or textual slot definitions in its 12-byte header.

---

## 8. Example: WLZ_0005.ADP

The supplied reference example is 50 bytes long:

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
| Payload CRC16 | `0x4BFA` |

Complete hexadecimal representation:

```text
41 44 50 33 17 00 18 00 26 00 FA 4B 01 07 00 00
00 02 0B 17 00 01 17 00 02 0B 13 00 01 13 00 01
07 00 00 00 02 0B 17 00 01 17 00 02 0B 13 00 01
13 00
```

Non-empty steps decoded from the payload:

- Step 0: slot 1, accent 3 (`0x07`)
- Step 4: slot 2, accent 3 (`0x0B`), slot 5, accent 3 (`0x17`)
- Step 6: slot 5, accent 3 (`0x17`)
- Step 8: slot 2, accent 3 (`0x0B`), slot 4, accent 3 (`0x13`)
- Step 10: slot 4, accent 3 (`0x13`)
- Step 12: slot 1, accent 3 (`0x07`)
- Step 16: slot 2, accent 3 (`0x0B`), slot 5, accent 3 (`0x17`)
- Step 18: slot 5, accent 3 (`0x17`)
- Step 20: slot 2, accent 3 (`0x0B`), slot 4, accent 3 (`0x13`)
- Step 22: slot 4, accent 3 (`0x13`)

All other steps have `hit_count = 0`.

For example:

```text
0x07 = (1 << 2) | 3
```

Therefore `0x07` means slot 1 with accent level 3.

The calculated CRC16-CCITT of the 38-byte payload is:

```text
0x4BFA
```

which matches the header.

---

## 9. Conformance Requirements

A conforming ADP v2.3 writer shall:

- emit the exact 12-byte header described above;
- encode one step record for every step;
- omit rests from hit lists;
- restrict slot indices to 0–15;
- restrict stored accents to 1–3;
- store the correct payload byte count;
- calculate CRC16-CCITT over the payload only; and
- use slot-map ID 255 only for INLINE maps.

A conforming reader shall:

- validate the header, payload size, and CRC;
- decode exactly `LENGTH` step records;
- reject truncated or trailing payload data;
- reject packed hits with accent 0;
- resolve registered slot maps by numeric ID; and
- require a same-basename companion ADT when `SLOT_MAP_ID=255`.

---

## 10. Reference Implementation

The reference encoder is:

```text
adc-adt2adp.py 260801c
```

Its default workflow is:

```text
./ADT/*.ADT -> ./ADP/*.ADP
```

The ADP v2.3 payload encoding is intentionally compatible with the step payload used by ADP v2.2, while the v2.3 header provides explicit versioning, subdivision, length, slot-map identification, payload size, and payload integrity checking.
