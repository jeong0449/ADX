# ADX Drum
## An Open Platform for Drum Patternology

**Analyze • Abstract • Exchange • Play**

ADX Drum is an open software ecosystem for analyzing, abstracting, exchanging, and playing reusable drum patterns derived from Standard MIDI drum performances.

---

## Why ADX?

**ADX Drum** evolved from the **ADT/ADP** family drum pattern formats.

- **ADT** (*Ardule Drum Text*) is the human-readable drum pattern format.
- **ADP** (*Ardule Drum Pattern*) is the compact binary-cache format generated from ADT for efficient storage and playback.

The name **ADX** originally referred to the ADT/ADP format family. As the project evolved beyond file formats into a complete software ecosystem—including MIDI analysis, pattern abstraction, format conversion, playback, and a pattern library—**ADX** naturally became the umbrella name for the platform.

The letter **X** is intentionally open-ended. It evokes concepts such as **eXchange**, **eXtended**, and future **expansion**, reflecting the evolution of the platform beyond individual file formats.

Today, **ADX Drum** provides an integrated workflow for **analyzing, abstracting, exchanging, and playing** drum patterns.

---

## Project Reorganization (v2.3)

With the introduction of the ADT/ADP v2.3 specifications, the project has been reorganized into two independent repositories.

- [Nano Ardule](https://github.com/jeong0449/NanoArdule) remains focused on the hardware and firmware implementation for Arduino Nano- and Raspberry Pi-based embedded MIDI playback.

- **ADX Drum** is a new standalone software project dedicated to Standard MIDI drum analysis, drum pattern abstraction, ADT/ADP/ORN generation, pattern exchange, lightweight playback tools, and the drum pattern library.

This separation clearly distinguishes the embedded playback platform from the software ecosystem, allowing both projects to evolve independently while sharing the same design philosophy.

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
          ADX Drum Player • Nano Ardule • Fluid Ardule
```

The ADX Platform consists of four major components:

- [**ADC Toolkit**](./scripts/README_KO.md) – Tools for MIDI analysis, preprocessing, pattern abstraction, and format conversion.
- **ADT / ADP / ORN** – Open drum pattern specifications for human-readable editing, compact binary playback, and ornament representation.
- **ADX Drum Player** – A lightweight player for validating and performing ADT/ADP/ORN patterns.
- **Pattern Library** – A collection of reusable and exchangeable drum patterns derived from Standard MIDI files.

Together, these components provide a complete workflow from **performance MIDI** to **portable drum patterns**.

---

## Acknowledgements

The development of **ADX Drum** owes much to the pioneering work of **René-Pierre Bardet**, whose classic books

- *200 Drum Machine Patterns*
- *260 Drum Machine Patterns*

have inspired generations of drummers, musicians, and MIDI enthusiasts.

Additional reference patterns were obtained from the **27 Instant Rap Patterns** collection (original compiler/author unknown).

The widely circulated GM MIDI transcriptions of these pattern collections have served as an invaluable reference dataset throughout the development of ADX Drum. They made it possible to study, analyze, validate, and refine the pattern abstraction methods implemented in this project.

The original books remain valuable references and can still be found through various online archival resources. The GM MIDI transcriptions used during the development of ADX Drum were obtained from community resources, including:

- https://discuss.cakewalk.com/topic/648-460-free-gm-midi-drum-patterns/

ADX Drum builds upon this legacy by transforming Standard MIDI drum performances into reusable, exchangeable, and playable drum patterns for modern software and embedded systems.
