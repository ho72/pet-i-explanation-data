# 파이프라인 테스트

[실행 안내](SETUP.md) · [입출력 형식](DATA_FORMAT.md) · [GitHub Actions](https://github.com/ho72/pet-i-explanation-data/actions/workflows/checks.yml)

## 실행

Python 3.12 환경에서 다음 검사를 실행합니다.

```bash
python -m pip install -r requirements-dev.txt
python -m pip check
ruff check .
ruff format --check .
python -m pytest -q
```

GitHub Actions에서도 push와 pull request마다 같은 검사를 실행합니다.

## 테스트 대상

[회귀 테스트 31개](../tests/test_pipeline.py)는 다음 동작을 확인합니다.

| 영역 | 확인하는 동작 |
| --- | --- |
| 입력 | CSV·JSON·BOM·필수 열·잘못된 형식·기존 진단명 정규화 |
| 출력 | 성공은 service·sft, 실패는 error, dry-run은 preview로 분리 |
| 컨텍스트 | 문서 부족 시 입력·검색 근거 보존, 7개 분류 매핑 |
| 경로 | corpus 지정 경로, 원본 파일명 보존, debug 경로 정규화 |
| 설정 | 모델 우선순위, 근거 사용 옵션, CLI 제한·종료 코드 |
| SDK 응답 | 중단·거절·빈 내용·호출 실패를 오류로 처리 |

## 외부 서비스와 품질 평가

테스트는 모의 Teacher 클라이언트와 설치한 OpenAI SDK의 응답 스키마를 사용합니다. 예상하지 않은 검색 호출은 테스트 실패로 처리하므로 API 키나 유료 호출 없이 실행할 수 있습니다.

이 테스트의 대상은 파이프라인 동작입니다. 실제 검색 품질·모델 생성 품질·수의학적 사실성·인용 타당성은 별도 평가가 필요합니다. VLM 학습·분류 평가는 [대표 레포](https://github.com/ho72/pet-i-vlm-diagnosis)의 범위입니다.
