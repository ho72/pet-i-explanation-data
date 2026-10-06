# 설치와 실행

[프로젝트 소개](../README.md) · [입출력 형식](DATA_FORMAT.md) · [구현 한계](PIPELINE.md)

## 환경 준비

저장소 루트에서 실행하세요. 현재 코드는 로컬 문서를 `./corpus`에서 찾고 검색 스냅샷을 `./debug`에 저장합니다.

```bash
git clone https://github.com/ho72/PET-I-Explanation-Data.git
cd PET-I-Explanation-Data
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python auto_ctx.py --help
```

`ddgs`는 DuckDuckGo 검색, `wikipedia`는 Wikipedia 검색, `openai`는 Teacher LLM 호출에 사용합니다. 패키지 버전은 고정하지 않았으며 과거 생성 환경을 그대로 재현하는 lock 파일은 없습니다. Python 3.14에서 이번 CLI와 외부 호출 없는 샘플 처리를 확인했습니다. 외부 패키지를 설치해 검색·LLM 호출을 검증한 결과는 아닙니다.

## API 키 설정

코드는 셸 환경변수 `OPENAI_API_KEY`를 읽습니다. 발급받은 키를 로컬 셸에 설정한 후 생성 명령을 실행하세요. `.env` 파일을 자동으로 읽는 기능은 없습니다. 소스 수정이나 키 파일의 Git 업로드는 필요하지 않습니다.

현재 CLI 파이프라인의 Teacher 모델은 `gpt-4o`로 지정되어 있습니다. `OPENAI_MODEL`만 바꿔서는 CLI에서 사용하는 모델이 바뀌지 않습니다. 실제 생성 실행에는 OpenAI API 비용이 발생하며, 설정한 계정에서 모델 접근이 가능해야 합니다.

## CSV 샘플로 설명 생성

```bash
python auto_ctx.py \
  --csv data/sample_input.csv \
  --out outputs/explanations.jsonl \
  --k 20 \
  --use_ddg --use_wiki
```

기존 샘플에는 결막염·유루증·안검염 입력 세 건이 있습니다. 성공적으로 처리된 입력 하나당 `service`와 `sft` 레코드를 저장하므로 세 건 처리 시 출력은 여섯 줄입니다. 처리 예외가 발생한 입력은 `error` 레코드 한 줄로 저장됩니다. `--out` 파일은 매 실행에서 덮어씁니다.

## 로컬 문서만 사용

```bash
python auto_ctx.py \
  --csv data/sample_input.csv \
  --out outputs/local_explanations.jsonl \
  --k 4
```

`--use_ddg`, `--use_wiki`를 생략하면 외부 검색을 호출하지 않습니다. **API 키가 설정되어 있으면 이 명령도 Teacher LLM을 호출합니다.** 저장된 시드 문서가 컨텍스트에 추가될 수 있으나, 시드 URL을 실제로 열어 최신 내용을 가져오지는 않습니다.

## API 키 없이 출력 구조 확인

LLM 호출 없이 입출력 경로만 확인하려면 위의 로컬 문서 명령을 `OPENAI_API_KEY`가 없는 환경에서 실행합니다. macOS/Linux 셸에서는 다음과 같이 해당 프로세스에만 키를 전달하지 않을 수 있습니다.

```bash
env -u OPENAI_API_KEY python auto_ctx.py \
  --csv data/sample_input.csv \
  --out outputs/structure_check.jsonl \
  --k 4
```

현재 폴백 구현에 따라 출력 텍스트는 `오류 - 풀백`입니다. 이 방법은 JSONL 구조를 확인하기 위한 실행이며, 설명문 생성 성공이나 사용 가능한 학습 데이터 생성 결과가 아닙니다. 출력의 `length_ok`가 `false`인 것도 함께 확인하세요.

## JSON 입력

```bash
python auto_ctx.py \
  --json_dir data \
  --out outputs/json_explanations.jsonl \
  --k 4
```

`data/example_input.json`은 CSV 샘플 첫 행의 진단명·증상을 사용해 구성한 입력 형식 예시입니다. 이미지 파일은 읽지 않습니다. `--json_dir`는 해당 디렉터리의 `*.json`만 읽고 하위 디렉터리는 재귀 탐색하지 않습니다. JSON 구문이 잘못되었거나 최상위 값이 객체가 아닌 파일은 건너뜁니다.

## CLI 옵션

| 옵션 | 의미 | 기본값 |
| --- | --- | --- |
| `--csv` / `--json_dir` | 두 입력 방식 중 하나를 필수로 선택 | 없음 |
| `--out` | 출력 JSONL 파일 경로, 필수 | 없음 |
| `--k` | 최종 컨텍스트 문서 수의 상한 | `20` |
| `--use_ddg` | DuckDuckGo 검색 활성화 | 비활성 |
| `--use_wiki` | Wikipedia 검색 활성화 | 비활성 |
| `--wiki_pages` | Wikipedia 쿼리별 페이지 수 | `2` |
| `--ctx_sections` | 예약된 힌트 옵션, 현재 처리 결과에 반영되지 않음 | `3` |

## 실행이 기대와 다를 때

- **컨텍스트가 적음:** 레포 루트에서 실행했는지, 진단명이 코드의 매핑과 맞는지 확인합니다. `debug/*.json`에서 수집·필터·중복 제거·완화 단계별 결과를 볼 수 있습니다.
- **외부 검색 결과가 없음:** 패키지 import 실패나 검색 오류 시 빈 결과가 반환될 수 있습니다. 로컬 문서·시드만으로 실행이 계속될 수 있으므로 검색 스냅샷을 확인합니다.
- **`오류 - 풀백` 출력:** API 키 미설정, SDK 미설치, 모델 호출 실패 등을 확인합니다. `[OK]` 로그만으로 생성 성공을 판단하지 마세요.
- **JSONL을 바로 학습에 넣을 수 없음:** 먼저 `record_type`을 분리하고, 폴백·오류 레코드를 제외한 뒤 사용하는 학습기의 대화·도구 호출 스키마로 변환해야 합니다.
- **입력 레코드가 없음:** 빈 CSV나 유효한 JSON 파일이 없는 경우 종료 코드 `2`로 끝납니다.

## 이번 확인 범위

2026.10.06에 Python 구문, `--help`, 키를 전달하지 않는 CSV 3건·JSON 1건의 처리와 출력 스키마를 확인했습니다. 외부 검색·Teacher LLM·VLM 학습은 실행하지 않았습니다. 이 확인은 외부 API 연결이나 설명문의 품질 검증을 보장하지 않습니다.
