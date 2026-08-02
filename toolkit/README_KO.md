# ADC Toolkit

**ADC Toolkit**은 Standard MIDI File(SMF)의 드럼 연주를 분석하여 **재사용 가능한 드럼 패턴(ADX Pattern)** 으로 변환하기 위한 도구 모음입니다.

ADC Toolkit은 단순한 MIDI 변환기가 아닙니다.

원본 연주를 검사하고, 분석하고, 사람이 검토한 뒤, 표준화된 드럼 패턴으로 추상화하여 비교·교환·재생 가능한 라이브러리를 구축하는 작업 체계입니다.

---

# 프로젝트 목표

ADC Toolkit은 다음과 같은 작업을 지원합니다.

- Standard MIDI 드럼 연주 분석
- Drum Pattern 시각화
- Pattern 단위 분할
- ADT(텍스트) 생성
- ADP(바이너리) 생성
- ORN(장식음) 생성
- ADX Pattern 검증 및 재생
- 드럼 패턴 라이브러리 구축

---

# ADC Toolkit 구성

| 프로그램 | 역할 |
|-----------|------|
| **adc-midi-inspector.py** | 입력 MIDI 검사 및 Drum Roll 생성 |
| **adc-mid2report.py** | 리듬 구조 및 Pattern 분석 |
| **adc-patternlab.py** | 사람이 Pattern을 검토하고 최종 결정 |
| **adc-midi-split.py** | Pattern 단위 MIDI 분할 및 PDF 생성 |
| **adc-mid2adt.py** | Split MIDI → ADT 변환 |
| **adc-adt2adp.py** | ADT → ADP(Binary Cache) 변환 |
| **adc-orn-writer.py** | Flam/Grace 등을 ORN으로 저장 |
| **adx-viewer.py** | ADT/ADP/ORN 시각화 |
| **adx-player.py** | ADX Pattern 재생 |

---

# 전체 작업 흐름

```text
Standard MIDI
      │
      ▼
adc-midi-inspector
      │
      ▼
adc-mid2report
      │
      ▼
adc-patternlab
      │
      ▼
PatternLab CSV
      │
      ▼
adc-midi-split
      │
      ▼
Split MIDI
      │
      ▼
adc-mid2adt
      │
      ▼
ADT
      │
      ├── adc-adt2adp → ADP
      │
      └── adc-orn-writer → ORN
              │
              ▼
      adx-viewer / adx-player
```

---

# Quick Start

## 1. 입력 MIDI 검사

```powershell
python .\adc-midi-inspector.py .\song.mid
```

---

## 2. 드럼 패턴 분석

```powershell
python .\adc-mid2report.py .\song.mid
```

---

## 3. PatternLab 실행

```powershell
python .\adc-patternlab.py .\song.mid
```

Pattern별로 다음 항목을 검토합니다.

- Export
- Genre
- Pattern 번호
- Subdivision
- SLOT_MAP
- ORN

검토가 끝나면 CSV를 저장합니다.

---

## 4. Pattern 분할

```powershell
python .\adc-midi-split.py `
    .\song.mid `
    .\song_patternlab.csv `
    --split `
    --pdf
```

---

## 5. ADT 생성

```powershell
python .\adc-mid2adt.py `
    .\song_patternlab.csv `
    --input-dir .\split-midi `
    --out-dir .\ADT
```

---

## 6. ADP 생성

```powershell
python .\adc-adt2adp.py .\ADT --out-dir .\ADP
```

---

## 7. ORN 생성

```powershell
python .\adc-orn-writer.py `
    .\song.mid `
    .\song_patternlab.csv
```

---

## 8. Viewer 검증

```powershell
python .\adx-viewer.py .\ADP
```

---

## 9. Player 재생

Linux / Raspberry Pi

```bash
python3 adx-player.py ./ADP/RCK_0001.ADP --loop
```

---

# 핵심 파일 형식

| 형식 | 역할 |
|------|------|
| **MID** | 원본 Standard MIDI |
| **CSV** | PatternLab의 최종 결정 |
| **ADT** | 사람이 읽는 표준 패턴 |
| **ADP** | 빠른 재생을 위한 Binary Cache |
| **ORN** | Flam, Grace 등의 장식음 |
| **PDF** | 검수용 패턴 문서 |

---

# 설계 철학

ADC Toolkit은 다음 원칙을 따릅니다.

- 원본 MIDI는 최대한 보존한다.
- 자동 분석은 후보만 제시한다.
- 최종 결정은 사람이 수행한다.
- Pattern과 Performance를 분리한다.
- 사람이 읽는 형식(ADT)과 기계가 읽는 형식(ADP)을 분리한다.

```text
Original MIDI
      │
      ▼
Inspect
      │
      ▼
Analyze
      │
      ▼
Human Review
      │
      ▼
Canonical Pattern
      │
      ▼
Playback
```

---

# Drum Patternology

ADC Toolkit은 **Drum Patternology(드럼 패턴학)** 라는 개념을 기반으로 개발되고 있습니다.

목표는 개별 MIDI 파일을 저장하는 것이 아니라,

- 드럼 패턴을 수집하고,
- 분석하고,
- 정규화하고,
- 비교하고,
- 재사용 가능한 라이브러리로 구축하는 것입니다.

---

# 문서

상세한 사용 방법은 다음 문서를 참고하십시오.

- `docs/ADC_Toolkit_User_Guide_KO.md`
- [`specs/ADT_v2.3.md`](specs/ADT_v2.3.md)
- [`specs/ADP_v2.3.md`](specs/ADP_v2.3.md)
- [`specs/ORN_v1.0.md`](specs/ORN_v1.0.md)

---

# 라이선스

본 프로젝트의 라이선스는 추후 공개 예정입니다.

---

**ADC Toolkit은 Standard MIDI Drum Performance를 분석하여 재사용 가능한 드럼 패턴으로 추상화하는 오픈소스 프로젝트입니다.**
