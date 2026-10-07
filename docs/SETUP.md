# 설치와 실행

[프로젝트 소개](../README.md) · [입출력 형식](DATA_FORMAT.md) · [파이프라인](PIPELINE.md)

## 먼저 외부 호출 없이 확인

```bash
git clone https://github.com/ho72/pet-i-explanation-data.git
cd pet-i-explanation-data
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python auto_ctx.py --help
python auto_ctx.py --csv data/sample_input.csv --out outputs/preview.jsonl --k 4 --dry-run
```

`--dry-run`은 **API 키 유무와 관계없이 외부 검색·Teacher 호출을 하지 않습니다.** 컨텍스트와 프롬프트를 `record_type: preview`로 저장하며 서비스 설명문·SFT 학습 레코드는 만들지 않습니다. 외부 검색 옵션과 함께 지정하면 종료 코드 2로 거절합니다.

검증 환경은 Python 3.12.14입니다. `requirements.txt`의 직접 의존성과 `requirements.lock.txt`의 하위 의존성 제약은 2026.10.07 설치·테스트 조합입니다. 과거 졸업프로젝트의 정확한 의존성 lock을 복원한 것은 아닙니다.

기본 corpus는 스크립트 옆 `corpus/`를 사용하므로 다른 디렉터리에서 실행해도 찾을 수 있습니다. 문서 파일의 Unicode 이름을 정규화하여 비교하지만 원본 이름을 바꾸지 않습니다.

## 실제 설명 생성

셸 환경변수 `OPENAI_API_KEY`에 자신의 키를 설정한 뒤 `--dry-run` 없이 실행합니다. 실제 키를 소스나 Git에 넣지 않습니다. `.env`를 자동으로 읽는 기능은 없습니다. 아래 명령은 유료 Teacher 호출을 포함합니다.

```bash
# 자신의 셸에 키를 설정한 이후 실행
python auto_ctx.py \
  --csv data/sample_input.csv \
  --out outputs/explanations.jsonl \
  --k 20 --use_ddg --use_wiki
```

모델 우선순위는 **`--model` → `OPENAI_MODEL` → 기존 기본값 `gpt-4o`**입니다. 자신의 계정에서 사용할 수 있는 모델을 선택합니다. 모델마다 파라미터 지원이 다를 수 있으며 이 공개 작업에서 실제 모델 접근·생성을 확인한 것은 아닙니다.

외부 검색을 끄면 로컬 문서·저장된 시드로 컨텍스트를 만들지만, dry-run이 아니면 Teacher 호출은 진행합니다. 시드 URL의 원문을 새로 열람하지는 않습니다.

## 입력·저장과 종료 코드

CSV는 `image_path`, `diagnosis`, `symptoms` 열이 필요하며 UTF-8 BOM을 지원합니다. JSON 디렉터리는 `*.json`을 정렬하여 읽고 하위 디렉터리는 탐색하지 않습니다.

```bash
python auto_ctx.py --json_dir data --out outputs/json-preview.jsonl --dry-run
```

정상 생성 입력당 `service`·`sft` 두 줄, dry-run 입력당 `preview` 한 줄, 실패 입력당 `error` 한 줄을 저장합니다. 기존처럼 `--out` 파일은 실행마다 덮어쓰므로 보존할 결과에는 새 파일명을 사용하세요. 키 미설정·생성 실패·빈 응답·중단 출력은 학습 레코드로 저장하지 않습니다. 실패 행 다음의 다른 입력은 계속 처리합니다.

| 종료 코드 | 의미 |
| --- | --- |
| 0 | 모든 입력 생성 또는 dry-run 확인 완료 |
| 1 | 하나 이상의 입력 처리·생성 실패. 오류 레코드 확인 필요 |
| 2 | 잘못된 옵션·읽을 수 없는 입력·필수 열 누락·빈 입력 |

잘못된 JSON도 조용히 건너뛰지 않고 오류 레코드로 남깁니다. 결과 파일의 존재만으로 생성 성공을 판단하지 않습니다.

## 옵션

| 옵션 | 기본·역할 |
| --- | --- |
| `--csv` / `--json_dir` | 하나 필수. CSV 또는 JSON 디렉터리 |
| `--out` | 출력 JSONL 경로, 필수 |
| `--dry-run` | 외부 호출 없이 preview만 저장 |
| `--model` | Teacher 모델 명시 |
| `--k` | 컨텍스트 상한, 기본 20, 양수 |
| `--use_ddg`, `--use_wiki` | 선택적 외부 검색. 기본 비활성 |
| `--wiki_pages` | 쿼리별 Wikipedia 페이지 상한, 기본 2, 양수 |
| `--corpus-dir` | 스크립트 옆 corpus. 다른 로컬 문서 디렉터리 지정 가능 |
| `--debug-dir` | 기본 출력 파일 옆 debug 디렉터리 |
| `--ctx_sections` | 기존 CLI 호환용 예약 옵션. 비기본값에 경고. 섹션 변경은 JSON `instructions.sections` 사용 |

## 검증

```bash
python -m pip install -r requirements-dev.txt
python -m pip check
ruff check .
ruff format --check .
python -m pytest -q
```

회귀 테스트 30개는 입력·컨텍스트·생성 오류·출력·CLI를 확인합니다. 설치한 OpenAI SDK의 응답 객체와 모의 클라이언트를 사용하며, 검색·유료 Teacher·VLM 학습을 실행하지 않습니다. [검증 기록](VALIDATION.md)을 참고하세요.
