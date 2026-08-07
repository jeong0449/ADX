# ADX Drum
## An Open Platform for Drum Patternology

**Analyze • Abstract • Exchange • Play**

ADX Drum is an **open cross-platform software ecosystem** for analyzing, abstracting, exchanging, and playing reusable drum patterns derived from Standard MIDI drum performances.

---

## Why ADX?

**ADX Drum** evolved from the **ADT/ADP** family drum pattern formats.

- **ADT** (*Ardule Drum Text*) is the human-readable drum pattern format.
- **ADP** (*Ardule Drum Pattern*) is the compact binary-cache format generated from ADT for efficient storage and playback.

The name **ADX** originally referred to the ADT/ADP format family. As the project evolved beyond file formats into a complete software ecosystem—including MIDI analysis, pattern abstraction, format conversion, playback, and a pattern library—**ADX** naturally became the umbrella name for the platform.

The letter **X** is intentionally open-ended. It evokes ideas such as **eXchange**, **eXtensibility**, **Cross-platform compatibility**, and future **eXpansion**, reflecting the evolution of ADX from a family of file formats into an open software ecosystem.

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

Additional reference patterns were obtained from the **27 Instant Rap Patterns** collection, whose original compiler or author could not be identified.

The widely circulated GM MIDI transcriptions of these collections have served as an invaluable reference dataset throughout the development of ADX Drum. They made it possible to study, analyze, validate, and refine the pattern abstraction methods implemented in this project.

The original books remain valuable references and can still be found through various online archival resources. The GM MIDI transcriptions used during the development of ADX Drum were obtained from community resources, including:

- https://discuss.cakewalk.com/topic/648-460-free-gm-midi-drum-patterns/

ADX Drum builds upon this legacy by transforming Standard MIDI drum performances into reusable, exchangeable, and playable drum patterns for modern software and embedded systems.

Today, ADX Drum enables anyone to create, analyze, exchange, and share drum patterns beyond the original reference collections, whether authored from scratch or derived from Standard MIDI drum performances.

---

## Source Notes

During the development of ADX Drum, several observations were made regarding the historical reference materials that are widely circulated on the Internet.

A PDF commonly distributed under the title **200 Drum Machine Patterns** was found to contain only the cover page and table of contents from René-Pierre Bardet's book. The remainder of the document consists of **Ray F. Badness's _Drum Programming: A Complete Guide to Program and Think Like a Drummer_**. Whether this resulted from an editorial mistake or from the way the PDF was originally assembled could not be determined. Regardless of its origin, the same mixed document appears to have been widely redistributed over the years.

Despite this apparent confusion, Badness's book is itself an excellent reference. Although written primarily for drum programming rather than traditional drumming, it provides many practical insights into constructing convincing drum machine patterns and remains valuable reading for musicians and programmers alike.

Another interesting observation concerns the GM MIDI files distributed through the Cakewalk forum. Files whose names begin with **6** correspond closely to the patterns published in Bardet's **260 Drum Machine Patterns**. In contrast, the original printed source corresponding to the files whose names begin with **2** could not be identified during the development of ADX Drum. They do not correspond directly to the patterns presented in Badness's book either.

For this reason, ADX Drum treats these MIDI files as **historical reference material** rather than assuming that their numbering faithfully reproduces any particular printed edition. Pattern identifiers within the ADX library are therefore assigned independently, while preserving available source information as metadata whenever possible.
