# ADC Toolkit

ADC Toolkit은 **ADX(Advanced Drum eXchange)** 생태계의 핵심 구성 요소입니다.

Standard MIDI File(SMF)의 드럼 연주를 분석하여 재사용 가능한 드럼 패턴(ADX = ADT/ADX Pattern)으로 변환하기 위한 도구 모음입니다.

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

# Quick Start

## 실행 환경 및 의존성

ADC Toolkit은 **Python 3.10 이상**을 기준으로 개발되었습니다.

### 필수 환경

- Python 3.10 이상
- Windows 10/11 (권장)
- 표준 MIDI File(SMF)

### 주요 Python 패키지

ADC Toolkit은 다음과 같은 Python 패키지를 사용합니다.

- mido
- pretty_midi
- pandas
- numpy
- matplotlib
- svgwrite

필요한 패키지는 다음과 같이 설치할 수 있습니다.

```powershell
pip install -r requirements.txt
```

> [!TIP]
> `adc-midi-split.py`의 PDF 생성 기능(`--pdf`)을 사용하려면 **Ghostscript**가 설치되어 있어야 합니다.
>
> Ghostscript는 **PDF 생성에만 필요**하며, 일반적인 MIDI 분석 및 패턴 분할 기능에는 필요하지 않습니다.

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

생성된 HTML 리포트를 Pattern 및 MIDI 재생 기능과 함께 열려면 다음과 같이 실행합니다.

```powershell
python .\play_server.py --report .\song_PatternLab.html
```

웹 브라우저가 자동으로 열리며 다음 기능을 사용할 수 있습니다.

- Pattern 재생
- MIDI 파일 재생
- Pattern 비교 청취
- FluidSynth 기반 SoundFont 재생

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
- [`specs/ADT_v2.3.md`](../specs/ADT_v2.3.md)
- [`specs/ADP_v2.3.md`](../specs/ADP_v2.3.md)
- [`specs/ORN_v1.0.md`](../specs/ORN_v1.0.md)

---

# 드럼 패턴 출처

ADC Toolkit 개발 과정에서 사용된 드럼 패턴은 다음과 같은 공개 자료를 참고하였습니다.

## Cakewalk Forum

https://discuss.cakewalk.com/topic/648-460-free-gm-midi-drum-patterns/

- 200 Instant Drum Patterns
- 260 Instant Drum Patterns
- 27 Instant Rap Patterns

이 자료는 General MIDI(GM) 형식의 드럼 패턴 예제로, ADC Toolkit의 분석 및 검증에 활용되었습니다.

---

## MIDIDrumFiles

https://mididrumfiles.com/

---

## Rene-Pierre Bardet

Cakewalk Forum에 공개된 **200 Instant Drum Patterns** 및 **260 Instant Drum Patterns**는 Rene-Pierre Bardet의 드럼 패턴 교재에 수록된 리듬을 MIDI 형식으로 변환한 자료로 알려져 있습니다.

원저작물은 드럼 교육용 자료이며, 검색을 통해 관련 PDF와 디지털화된 자료를 확인할 수 있습니다. ADC Toolkit은 이러한 자료를 **드럼 패턴 분석 및 연구**의 입력 데이터로 활용합니다. 특히 패턴의 장르 추정에 원저작물 PDF가 많은 도움이 되었습니다.

---

> [!NOTE]
> ADC Toolkit은 MIDI 파일 자체를 배포하는 프로젝트가 아닙니다.
>
> 본 프로젝트는 사용자가 적법하게 확보한 Standard MIDI File을 분석하여 재사용 가능한 드럼 패턴(ADT/ADP/ORN)으로 추상화하는 도구를 제공합니다.

---

# 라이선스

본 프로젝트의 라이선스는 추후 공개 예정입니다.

---

**ADC Toolkit은 Standard MIDI Drum Performance를 분석하여 재사용 가능한 드럼 패턴으로 추상화하는 오픈소스 프로젝트입니다.**
