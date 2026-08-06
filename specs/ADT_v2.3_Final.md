# ADT Specification v2.3 Final

**The ADX Platform for Drum Patternology**\
**Format name:** Ardule Drum Text (ADT)\
**Version:** 2.3 Final\
**Status:** Final Public Specification

> This document supersedes the draft ADT v2.3 specification and reflects
> the finalized design adopted after large-scale PatternLab analysis.

# Revision Summary

The following normative changes are introduced:

-   Straight-32 grid officially added.
-   Supported resolutions: **16, 32, 8T, 16T**
-   Six-level accent model:
    -   `.` Rest
    -   `-` Very Weak / Ghost
    -   `x` Weak
    -   `o` Medium
    -   `^` Strong
    -   `@` Accent
-   Accent symbols shall be obtained from `accent_levels.json`.
-   Resolution is determined by PatternLab.
-   The writer shall not recompute resolution.
-   Only ornament events outside the selected grid shall be stored in
    ORN.

------------------------------------------------------------------------

## 4.5 SUBDIV (Grid Resolution)

`SUBDIV` specifies the grid resolution.

Supported values:

  Value   Meaning
  ------- ------------------------
  16      Straight sixteenth
  32      Straight thirty-second
  8T      Eighth-note triplet
  16T     Sixteenth-note triplet

### Resolution Selection Rule

The writer shall use the coarsest resolution capable of representing all
required grid events.

Preference:

-   16 over 32 whenever possible.
-   8T over 16T whenever possible.

The writer records the resolution determined by PatternLab.

------------------------------------------------------------------------

## 5.1 Cell Symbols

ADT v2.3 Final defines six accent levels.

  Symbol     Level Meaning
  -------- ------- -------------------
  `.`            0 Rest
  `-`            1 Very Weak / Ghost
  `x`            2 Weak
  `o`            3 Medium
  `^`            4 Strong
  `@`            5 Accent

Velocity thresholds are not defined by this specification.

The mapping is provided exclusively by `accent_levels.json`.

Reference writers shall obtain the output symbol directly from the JSON
`symbol` field.

------------------------------------------------------------------------

## 6. Grid and ORN Separation

PatternLab determines the grid resolution before ornament analysis.

After the resolution has been selected:

-   Grid-aligned notes shall be written into ADT.
-   Only ornament events outside the selected grid shall be written into
    ORN.

A flam on a 16-step grid may therefore become a normal grid note on a
32-step grid.

ORN preserves only timing information that remains outside the chosen
grid.

------------------------------------------------------------------------

## 7. Validation

A conforming ADT v2.3 Final file shall satisfy:

-   SUBDIV is one of `16`, `32`, `8T`, `16T`.
-   Writers emit only:
    -   `.`
    -   `-`
    -   `x`
    -   `o`
    -   `^`
    -   `@`
-   Grid-representable notes shall not appear in ORN.
-   ORN contains only events outside the selected grid.

------------------------------------------------------------------------

## Appendix A --- Resolution Policy

The ADX Platform adopts the **Coarsest Valid Grid** principle.

Patterns shall be represented using the simplest grid that preserves all
musically significant events.

This improves readability, interoperability, and minimizes unnecessary
ORN data.
