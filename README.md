# The ADX Platform for Drum Patternology

**Analyze • Abstract • Exchange • Play**

The ADX Platform is an open software ecosystem for transforming Standard MIDI drum performances into reusable, exchangeable, and playable drum patterns.

## Project Reorganization (v2.3)

With the introduction of the ADT/ADP v2.3 specifications, the project has been reorganized into two independent repositories.

- [Nano Ardule](https://github.com/jeong0449/NanoArdule) remains focused on the hardware and firmware implementation for Arduino Nano– and Raspberry Pi–based embedded MIDI playback.

- **ADX** is a new standalone software project that focuses on the analysis and preprocessing of Standard MIDI drum files, drum pattern abstraction, ADT/ADP/ORN generation, pattern exchange, lightweight playback tools, and the drum pattern library.

This separation clarifies the distinction between the embedded playback platform (**Nano Ardule**) and the software ecosystem (**The ADX Platform for Drum Patternology**), while allowing both projects to evolve independently.
