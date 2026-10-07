# 입력과 출력 데이터 형식

[프로젝트 소개](../README.md) · [데이터 출처와 구성](DATA_SOURCES.md) · [실행 안내](SETUP.md) · [파이프라인](PIPELINE.md)

이 문서는 설명 생성 모듈의 CSV·JSON 입력과 JSONL 출력을 설명합니다. AI Hub 원본은 JPG 이미지와 라벨 JSON이고, 이 모듈은 앞선 전처리에서 준비한 진단명·증상을 입력받습니다. 원본 데이터와 공개 예시의 관계는 [데이터 출처](DATA_SOURCES.md)를 참고하세요.

## CSV 입력

[data/sample_input.csv](../data/sample_input.csv)의 세 열을 사용합니다.

| 열 | 처리 방식 |
| --- | --- |
| `image_path` | 파일명 stem을 `text_name`으로 사용. 이미지 파일을 열지 않음 |
| `diagnosis` | 설명 대상 진단명. 이 스크립트가 진단을 추론하지 않음 |
| `symptoms` | 쉼표 또는 세미콜론으로 구분된 증상 문자열을 목록으로 변환 |

```csv
image_path,diagnosis,symptoms
./data/img/001.jpg,결막염,결막 충혈;눈곱 증가
```

위 행은 저장소의 기존 샘플에 포함된 입력 예시입니다. 진단명은 서비스나 앞선 전처리에서 준비해 전달하는 값입니다.

## JSON 입력

[data/example_input.json](../data/example_input.json)처럼 최상위 `input` 객체를 쓰거나, 그 안의 객체를 최상위에 바로 둘 수 있습니다.

```json
{
  "input": {
    "text_name": "example_conjunctivitis",
    "normalized": {
      "diagnosis": "결막염",
      "symptoms": ["결막 충혈", "눈곱 증가"]
    },
    "ctx": [],
    "instructions": {
      "freeze_report": true,
      "use_ctx_only": true,
      "sections": ["overview", "reasoning", "causes", "care"]
    }
  }
}
```

- `report_json`을 직접 제공하면 `normalized`보다 우선합니다. 이때 `diagnosis`, `symptoms`를 그 안에 넣습니다.
- 입력 `ctx`에는 `idx`, `title`, `excerpt`, `url`을 가진 기존 컨텍스트를 전달할 수 있습니다.
- 기본 네 섹션은 질병 설명·진단 근거·주요 발생 원인·관리 방법으로 프롬프트에서 매핑됩니다.
- `use_ctx_only`는 true/false에 따라 근거 한정 또는 배경 지식 보충 지시를 만듭니다. `freeze_report`는 기존 메타데이터이며 자동 사실 검증 스위치가 아닙니다. [구현 범위](PIPELINE.md)를 참고하세요.

## 같은 JSONL에 저장되는 레코드

성공적으로 처리한 입력 하나마다 두 줄을 저장합니다. 아래는 필드 구조를 설명하는 예시이며 실제 LLM 출력이 아닙니다.

### `service`

```json
{
  "record_type": "service",
  "input": {
    "text_name": "example_conjunctivitis",
    "report_json": {
      "diagnosis": "결막염",
      "symptoms": ["결막 충혈", "눈곱 증가"]
    },
    "ctx": [],
    "instructions": {}
  },
  "output": {"text": "<생성된 설명문>"},
  "meta": {
    "quality": {
      "sections": true,
      "citations": false,
      "symptom_cov": true,
      "length_ok": false,
      "distinct_refs>=2": false
    },
    "text_name": "example_conjunctivitis",
    "used_queries": []
  }
}
```

`meta.quality`의 의미는 다음과 같습니다.

| 필드 | 실제 확인 대상 |
| --- | --- |
| `sections` | 섹션 설정 목록이 비어 있지 않은지 |
| `citations` | 컨텍스트에 URL이 하나라도 있는지 |
| `symptom_cov` | 입력 증상 목록이 비어 있지 않은지 |
| `length_ok` | 출력 텍스트가 80자 이상인지 |
| `distinct_refs>=2` | 컨텍스트의 서로 다른 URL이 두 개 이상인지 |

이 값들은 생성문이 요구 섹션을 모두 작성했는지, 증상을 정확히 설명했는지, 인용이 사실을 뒷받침하는지를 판정하지 않습니다. `false`인 레코드도 자동 제외하지 않고 저장합니다.

### `sft`

| 순서 | 메시지 역할 | 내용 |
| --- | --- | --- |
| 1 | `user` | 진단명·증상·섹션에 따른 보고서 작성 요구 |
| 2 | `assistant` | 검색 의사 표현과 `search` 호출 형식 |
| 3 | `tool` (`search`) | 수집 문서의 제목·URL·요약 목록 |
| 4 | `assistant` | 선택 컨텍스트의 `fetch` 호출 형식 |
| 5 | `tool` (`fetch`) | 선택 문서의 발췌 목록 |
| 6 | `assistant` | 정상 완료한 Teacher 설명문 |

레코드는 `record_type`, `messages`, `meta`로 구성됩니다. `meta`에는 `diagnosis`, `symptoms`, `ctx_count`, `text_name`, `used_queries`, `has_citations`가 저장됩니다. `has_citations`는 출력에 `[`와 `]`가 포함되는지만 확인합니다.

현재 `tool_calls`는 `name`, `arguments`를 가진 자체 형식이며 `tool` 메시지의 `content`는 목록입니다. API 표준 tool-call 메시지나 특정 학습기의 입력 형식과 동일하다고 가정하지 마세요. 이미지 데이터·이미지 토큰은 `messages`에 포함되지 않습니다.

### `preview`와 `error`

`--dry-run`에서는 입력당 `record_type: "preview"` 한 줄에 `input`, `ctx`, `prompt`, `meta`를 저장합니다. `meta.external_calls`는 false입니다. 생성문과 SFT를 만들지 않으며 학습에 넣는 레코드가 아닙니다.

입력 형식 오류, 키 미설정, SDK/연결 오류, 빈 응답, 거절·중단 출력은 `record_type: "error"`, 원본 `input`, 오류 설명으로 저장합니다. 정상 `service`·`sft`를 생성하지 않고 다른 입력은 계속 처리합니다. 실패가 하나라도 있으면 CLI 종료 코드는 1입니다.

## 검색 디버그 파일

기본적으로 출력 파일 옆 `debug/{정규화된 이름}-{식별 해시}.{stage}.json`에 문서 수와 제목·URL·출처·최대 220자의 본문 미리보기를 저장합니다. 단계 이름은 `collected_raw`, `after_filter1`, `after_dedup`, 필요 시 `after_relax`입니다. 출력 파일과 디버그 자료는 기본적으로 Git 추적에서 제외합니다.
