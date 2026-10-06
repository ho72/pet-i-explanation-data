# PET-I · Explanation Data

**진단명과 증상에 검색 근거를 붙여 반려견 안구 질환 설명문과 SFT 학습 데이터를 만드는 Python 파이프라인입니다.**

PET-I의 VLM 학습에 사용할 설명형 데이터를 준비하기 위해 구현한 모듈입니다. CSV 또는 JSON으로 주어진 진단명·증상을 읽고, 질환별 로컬 문서와 선택적 웹 검색 결과를 모아 Teacher LLM에 전달합니다. 결과는 서비스용 설명문과 검색·열람 대화 형식의 학습 레코드로 저장합니다.

이미지를 직접 분석하거나 질환을 새로 판별하는 기능은 이 저장소에 없습니다. `image_path`는 입력 식별자를 만드는 데 사용합니다. VLM 학습·평가와 전체 서비스 구조는 [PET-I-VLM-Diagnosis](https://github.com/ho72/PET-I-VLM-Diagnosis)를 참고하세요.

[데이터 출처와 구성](docs/DATA_SOURCES.md) · [입출력 형식](docs/DATA_FORMAT.md) · [실행 안내](docs/SETUP.md) · [파이프라인 설계와 한계](docs/PIPELINE.md) · [기존 생성 예시](docs/GENERATED_EXAMPLES.md)

## 프로젝트와 담당 역할

- **전체 프로젝트:** PET-I — VLM 기반 반려견 안구 질환 진단·설명 서비스, 건국대학교 4인 졸업프로젝트(2025.03~2025.12).
- **본인 담당:** 설명형 학습 데이터 생성과 전체 데이터 전처리, RAG/Web Search 파이프라인 구현. 이 저장소는 설명형 데이터 생성 코드를 보여줍니다.
- **팀 결과와의 관계:** 최종 학습 데이터는 진단 JSON·진단 Markdown·설명형·챗봇형으로 구성되었습니다. 증상 추출·챗봇 데이터 생성과 서비스의 최종 통합에는 팀원도 참여했습니다.

전체 모델의 분류 성능을 이 데이터 생성 모듈 단독의 성과로 표현하지 않습니다.

## 데이터 출처와 형식

PET-I 전체 프로젝트의 원본 이미지·라벨 출처는 **AI Hub의 [반려동물 안구 질환 데이터](https://www.aihub.or.kr/aihubdata/data/view.do?currMenu=115&dataSetSn=562&topMenu=100)**입니다. 공식 학습용 데이터는 JPG 이미지와 JSON 라벨로 제공됩니다. 프로젝트에서는 이 중 반려견 데이터의 7개 분류 항목으로 5,600장을 선별·전처리했습니다. 이는 전체 VLM 프로젝트의 이미지 준비 규모이며, 이 레포의 설명문 생성 건수는 아닙니다.

| 구분 | 출처·준비 과정 | 형식과 이 모듈의 사용 방식 |
| --- | --- | --- |
| 원본 이미지·질환 라벨 | AI Hub 반려동물 안구 질환 데이터 | JPG + JSON. 전체 PET-I에서 사용하며 이 저장소에는 원본 이미지·라벨을 포함하지 않음 |
| 설명 생성 입력 | 앞선 데이터 준비 단계에서 정리한 진단명·증상 | CSV(`image_path`, `diagnosis`, `symptoms`) 또는 정규화 JSON. 원본 AI Hub JSON을 그대로 읽는 방식은 아님 |
| 로컬 지식 문서 | 프로젝트에서 준비한 질환별 [corpus](corpus/) 7개 | TXT. 문서별 원문 출처·작성 이력은 현재 파일에 기록되어 있지 않음 |
| 선택적 외부 근거 | DuckDuckGo 검색 결과·영문 Wikipedia | 제목·URL·본문 발췌를 컨텍스트로 구성. 일부 상황에서는 코드에 저장된 시드 문서 사용 |
| 생성 결과 | 진단명·증상·컨텍스트를 전달받은 GPT-4o 출력 | JSONL. 설명문을 담는 `service`와 학습 대화를 담는 `sft` 레코드 |

공개 CSV는 **입력 형식을 보여주는 3건의 예시**이고, JSON 1건은 그 첫 행으로 구성한 형식 예시입니다. CSV 각 행을 원본 AI Hub 이미지·라벨과 대조한 매핑 기록은 없으므로 실제 학습 데이터의 일부라고 단정하지 않습니다. AI Hub 원본에 완성된 설명문이나 이 저장소의 SFT 대화가 포함되어 있다는 의미도 아닙니다.

전체 생성 데이터 건수, 필터 통과 건수, 학습·평가 분할은 이 레포에 기록되어 있지 않습니다. 단계별 출처와 공개 파일 목록은 [데이터 구성 안내](docs/DATA_SOURCES.md), 구체적인 필드 예시는 [입출력 형식](docs/DATA_FORMAT.md)에서 확인할 수 있습니다.

## 데이터 생성 흐름

```mermaid
flowchart TD
    A["CSV / JSON: 진단명·증상"] --> B["질환별 컨텍스트 수집"]
    C["로컬 corpus"] --> B
    D["DuckDuckGo / Wikipedia<br/>선택적 검색"] --> B
    B --> E["길이 필터·출처 점수·중복 제거"]
    E --> F["번호가 있는 CTX와 작성 프롬프트"]
    F --> G["Teacher LLM: GPT-4o"]
    G --> H["JSONL: service / sft"]
    E --> I["검색 단계별 debug 스냅샷"]
```

| 단계 | 구현 내용 |
| --- | --- |
| 입력 정규화 | CSV의 `image_path`, `diagnosis`, `symptoms` 또는 JSON 입력을 공통 구조로 변환 |
| 근거 수집 | 진단명과 연결된 로컬 문서, 선택적으로 DuckDuckGo 검색 요약·영문 Wikipedia 본문 수집 |
| 컨텍스트 구성 | 문서 길이·선호 도메인·중복 URL/제목을 기준으로 선별하고 `[번호]` 부여 |
| 설명 생성 | 질병 설명·진단 근거·주요 발생 원인·관리 방법의 네 섹션을 요청 |
| 데이터 저장 | 처리된 입력 하나당 `service`, `sft` 레코드를 같은 JSONL에 저장 |

`sft`의 `search`·`fetch` 대화는 수집된 문서로 **코드가 구성한 학습용 기록**입니다. Teacher LLM이 직접 도구를 호출한 실행 로그는 아닙니다.

## 저장소 구성

```text
PET-I-Explanation-Data/
├── auto_ctx.py                   # 입력·검색·생성·JSONL 저장
├── requirements.txt             # 외부 검색·Teacher LLM 패키지
├── corpus/                      # 질환별 로컬 텍스트 문서
├── data/
│   ├── sample_input.csv          # 기존 CSV 샘플 3건
│   └── example_input.json        # JSON 입력 형식 예시 1건
└── docs/
    ├── SETUP.md                  # 설치·실행·문제 해결
    ├── DATA_SOURCES.md           # 원본·입력·근거·생성 데이터 출처
    ├── DATA_FORMAT.md            # 입력·출력 스키마
    ├── PIPELINE.md               # 함수별 흐름과 구현 한계
    └── GENERATED_EXAMPLES.md     # 기존 README의 생성 예시
```

## 빠른 시작

```bash
git clone https://github.com/ho72/PET-I-Explanation-Data.git
cd PET-I-Explanation-Data
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python auto_ctx.py --help
```

Teacher LLM 호출에 사용할 키를 셸의 `OPENAI_API_KEY` 환경변수로 설정한 뒤 실행합니다. 실제 키를 소스나 Git에 저장하지 마세요. LLM 실행에는 API 사용량에 따른 비용이 발생합니다.

```bash
python auto_ctx.py \
  --csv data/sample_input.csv \
  --out outputs/explanations.jsonl \
  --k 20 \
  --use_ddg --use_wiki
```

외부 검색 없이 로컬 문서로 실행하거나, 키 없이 출력 구조만 확인하는 방법은 [실행 안내](docs/SETUP.md)에 있습니다.

## 현재 범위와 확인 사항

- `meta.quality`는 섹션 설정·근거 URL·증상 입력·출력 길이 등의 간단한 상태 정보를 저장합니다. 인용 정확성이나 수의학적 타당성을 검증하거나 부적합 데이터를 자동 제외하지는 않습니다.
- LLM 실패 시 현재 폴백은 `오류 - 풀백` 문자열을 반환합니다. 해당 레코드는 정상 학습 데이터로 사용하기 전에 제외해야 합니다.
- 임베딩·벡터 DB·LangChain 실행 코드는 이 파이프라인에 없습니다.
- 무증상 문서는 `corpus`에 있지만, 현재 진단별 로컬 문서 매핑은 여섯 질환명 기준입니다. 입력·코퍼스의 지원 범위는 [상세 안내](docs/PIPELINE.md)를 참고하세요.

2026.10.06 정리에서 API 키 자리표시자로 인한 구문 오류를 환경변수 읽기로 수정하고, CLI·샘플 입출력 구조를 외부 호출 없이 확인했습니다. 웹 검색·GPT-4o 설명 생성·VLM 재학습은 이번에 실행하지 않았습니다.

## PET-I 개인 저장소

| 저장소 | 역할 |
| --- | --- |
| [PET-I-VLM-Diagnosis](https://github.com/ho72/PET-I-VLM-Diagnosis) | 전체 프로젝트 소개와 VLM 학습·평가·서비스 코드 |
| [PET-I-Explanation-Data](https://github.com/ho72/PET-I-Explanation-Data) | 설명형 데이터 생성 파이프라인 — 현재 저장소 |

생성 예시는 모델 출력의 기록이며, 수의학적 정확성이 검증된 진단·치료 안내로 사용하지 않습니다.
