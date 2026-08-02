# The ADX Platform for Drum Patternology

**Analyze • Abstract • Exchange • Play**

The ADX Platform is an open software ecosystem for transforming Standard MIDI drum performances into reusable, shareable, and playable drum patterns.

---

## Why ADX?

The **ADX** project evolved from the **ADT/ADP** drum pattern format family.

- **ADT** (*Ardule Drum Text*) is the human-readable drum pattern format.
- **ADP** (*Ardule Drum Pattern*) is the compact binary cache format generated from ADT for efficient storage and playback.

The name **ADX** originally referred to the ADT/ADP format family. As the project evolved beyond file formats into a complete software ecosystem—including MIDI analysis, pattern abstraction, format conversion, playback, and a pattern library—**ADX** naturally became the umbrella name for the platform.

The letter **X** is intentionally open-ended. It evokes concepts such as **eXchange**, **eXtended**, and future **eXpansion**, reflecting the evolution of the platform beyond individual file formats.

Today, **The ADX Platform for Drum Patternology** has evolved into an integrated software ecosystem for **analyzing, abstracting, exchanging, and playing** drum patterns.

---

## Project Reorganization (v2.3)

With the introduction of the ADT/ADP v2.3 specifications, the project has been reorganized into two independent repositories.

- [Nano Ardule](https://github.com/jeong0449/NanoArdule) remains focused on the hardware and firmware implementation for Arduino Nano– and Raspberry Pi–based embedded MIDI playback.

- **ADX** is a new standalone software project dedicated to the analysis and preprocessing of Standard MIDI drum files, drum pattern abstraction, ADT/ADP/ORN generation, pattern exchange, lightweight playback utilities, and the drum pattern library.

This separation clearly distinguishes the embedded playback platform (**Nano Ardule**) from the software ecosystem (**The ADX Platform for Drum Patternology**), allowing both projects to evolve independently while sharing the same design philosophy.

---

## Platform Overview

```text
                 Standard MIDI Drum Files
                            │
                            ▼
                        Analyze
                            │
          MIDI analysis • PatternLab • Reports
                            │
                            ▼
                        Abstract
                            │
              ADT • ADP • ORN Specifications
                            │
                            ▼
                        Exchange
                            │
            Pattern Library • Portable Formats
                            │
                            ▼
                           Play
                            │
          ADX Player • Nano Ardule • Fluid Ardule
```

The ADX Platform consists of four major components:

- [**ADC Toolkit**](./toolkit/README_KO.md) – Tools for MIDI analysis, preprocessing, pattern abstraction, and format conversion.
- **ADT / ADP / ORN** – Open drum pattern specifications for human-readable editing, compact binary playback, and ornament representation.
- **ADX Player** – A lightweight player for validating and performing ADT/ADP/ORN patterns.
- **Pattern Library** – A collection of reusable and exchangeable drum patterns derived from Standard MIDI files.

Together, these components provide a complete workflow from **performance MIDI** to **portable drum patterns**.
