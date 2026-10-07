import json
import os
import subprocess
import sys
import unicodedata
from types import SimpleNamespace

import pytest

import auto_ctx as app


@pytest.fixture(autouse=True)
def isolated_execution(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setattr(app, "DEBUG_DIR", tmp_path / "debug")
    # Any unexpected provider call fails the test instead of reaching the network.
    monkeypatch.setattr(app, "ddg_search", lambda *a, **k: pytest.fail("Unexpected web call"))
    monkeypatch.setattr(app, "wiki_chunks", lambda *a, **k: pytest.fail("Unexpected wiki call"))


def rows():
    return app.load_rows_from_csv(str(app.ROOT / "data/sample_input.csv"))


def run_rows(tmp_path, input_rows=None, **kwargs):
    out = tmp_path / "result.jsonl"
    counts = app.process_rows(input_rows or rows(), str(out), False, False, 4, 3, **kwargs)
    return counts, [json.loads(x) for x in out.read_text().splitlines()]


def test_dry_run_never_calls_teacher_and_has_only_preview(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "call_teacher_llm", lambda *a, **k: pytest.fail("Unexpected LLM call"))
    counts, records = run_rows(tmp_path, dry_run=True)
    assert counts == {"success": 0, "preview": 3, "error": 0}
    assert [r["record_type"] for r in records] == ["preview"] * 3
    assert all(r["meta"]["external_calls"] is False and r["prompt"] and r["ctx"] for r in records)


def test_missing_key_creates_error_records_not_training_data(tmp_path):
    counts, records = run_rows(tmp_path)
    assert counts["error"] == 3
    assert all(r["record_type"] == "error" for r in records)
    assert all("output" not in r for r in records)


def test_success_preserves_service_and_sft_schema(monkeypatch, tmp_path):
    calls = []

    def teacher(prompt, model=None):
        calls.append(model)
        return "# 설명\n" + "합성 응답 [1]. " * 20

    monkeypatch.setattr(app, "call_teacher_llm", teacher)
    counts, records = run_rows(tmp_path, model="test-model")
    assert counts["success"] == 3
    assert [r["record_type"] for r in records] == ["service", "sft"] * 3
    assert calls == ["test-model"] * 3
    assert records[0]["input"]["report_json"]["diagnosis"] == "결막염"
    assert records[1]["messages"][-1]["content"] == records[0]["output"]["text"]


def test_provider_failure_and_next_row_success_are_distinct(monkeypatch, tmp_path):
    calls = iter([None, "테스트 완료"])

    def teacher(*a, **k):
        answer = next(calls)
        if answer is None:
            raise app.GenerationError("테스트 생성 실패")
        return answer

    monkeypatch.setattr(app, "call_teacher_llm", teacher)
    counts, records = run_rows(tmp_path, input_rows=rows()[:2])
    assert counts == {"success": 1, "preview": 0, "error": 1}
    assert [r["record_type"] for r in records] == ["error", "service", "sft"]


def test_relaxation_preserves_input_and_collected_web_documents(monkeypatch):
    monkeypatch.setattr(
        app,
        "ddg_search",
        lambda *a, **k: (
            [
                {
                    "title": "web dog",
                    "text": "웹 근거 " * 30,
                    "url": "https://example.org/web",
                    "source": "ddg",
                }
            ],
            ["query"],
        ),
    )
    ctx, queries, raw = app.build_ctx_for_row(
        "demo",
        "결막염",
        [
            {
                "idx": 1,
                "title": "input",
                "excerpt": "입력 근거 " * 30,
                "url": "https://example.org/input",
            }
        ],
        True,
        False,
        4,
        2,
        {},
    )
    assert {c["url"] for c in ctx} == {"https://example.org/input", "https://example.org/web"}
    assert queries == ["query"] and len(raw) == 2


def test_corpus_reads_nfd_names_without_renaming_and_honors_directory(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    filename = unicodedata.normalize("NFD", "무증상.txt")
    (corpus / filename).write_text("건강한 눈\n합성 문서 본문")
    mapped = app.load_local_corpus_by_diag(corpus)
    assert mapped["무증상"][0]["text"] == "합성 문서 본문"
    assert [p.name for p in corpus.iterdir()] == [filename]


@pytest.mark.parametrize("use_ctx_only", [True, False])
def test_prompt_grounding_option_is_effective(use_ctx_only):
    prompt = app.compose_teacher_prompt(
        {"diagnosis": "무증상", "symptoms": []}, [], use_ctx_only, ["overview"]
    )
    assert ("[CTX]로 지지되는 내용만" in prompt) == use_ctx_only
    assert ("배경 지식을 보충" in prompt) != use_ctx_only
    assert "새로운 진단을 추론하지" in prompt


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://vcahospitals.com/x", True),
        ("https://www.vcahospitals.com/x", True),
        ("https://fakevcahospitals.com/x", False),
    ],
)
def test_source_domain_score_requires_actual_domain(url, expected):
    assert app.domain_ok(url) is expected


def test_invalid_json_is_not_silently_discarded(tmp_path):
    (tmp_path / "broken.json").write_text("{")
    (tmp_path / "list.json").write_text("[]")
    input_rows = app.load_rows_from_json_dir(str(tmp_path))
    counts, records = run_rows(tmp_path, input_rows=input_rows, dry_run=True)
    assert counts["error"] == 2 and len(records) == 2
    assert all(r["record_type"] == "error" for r in records)


def test_sample_json_input_is_normalized():
    data = json.loads((app.ROOT / "data/example_input.json").read_text())
    row = app._normalize_json_input(data.get("input", data))
    assert row["report_json"]["diagnosis"] == "결막염"
    assert isinstance(row["report_json"]["symptoms"], list)


def test_normalized_diagnosis_preserves_legacy_primary_label():
    row = app._normalize_json_input(
        {"normalized": {"diagnosis": " 결막염/보조 라벨", "symptoms": []}}
    )
    assert row["report_json"]["diagnosis"] == "결막염"


def test_csv_requires_columns_and_accepts_bom(tmp_path):
    p = tmp_path / "input.csv"
    p.write_text("wrong\nvalue")
    with pytest.raises(ValueError, match="열이 필요"):
        app.load_rows_from_csv(str(p))
    p.write_text("image_path,diagnosis,symptoms\na.jpg,무증상,", encoding="utf-8-sig")
    assert app.load_rows_from_csv(str(p))[0]["report_json"]["diagnosis"] == "무증상"


@pytest.mark.parametrize(
    "bad_field,value",
    [
        ("symptoms", "문자열"),
        ("symptoms", [42]),
        ("sections", [42]),
        ("use_ctx_only", "false"),
        ("ctx", ["bad"]),
    ],
)
def test_invalid_row_shape_is_an_error(tmp_path, bad_field, value):
    row = rows()[0]
    if bad_field == "symptoms":
        row["report_json"][bad_field] = value
    elif bad_field == "ctx":
        row[bad_field] = value
    else:
        row["instructions"][bad_field] = value
    counts, records = run_rows(tmp_path, [row], dry_run=True)
    assert counts["error"] == 1 and records[0]["record_type"] == "error"


def test_debug_name_cannot_escape_output_directory(tmp_path):
    app.save_retrieval_debug("../../outside", "collected_raw", [])
    files = list((tmp_path / "debug").glob("*.json"))
    assert len(files) == 1
    assert not (tmp_path.parent / "outside.collected_raw.json").exists()


@pytest.mark.parametrize("argv", [["--k", "0"], ["--wiki_pages", "-1"], ["--dry-run", "--use_ddg"]])
def test_cli_rejects_invalid_limits_and_network_dry_run(argv):
    with pytest.raises(SystemExit) as exc:
        app.parse_args(["--csv", "input.csv", "--out", "out.jsonl", *argv])
    assert exc.value.code == 2


def fake_provider(monkeypatch, result=None, error=None):
    import openai

    sent = {}

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def create(self, **kwargs):
            sent.update(kwargs)
            if error:
                raise error
            return result

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-fake-value")
    return sent


def response(finish="stop", text="합성 설명", refusal=None):
    from openai.types.chat import ChatCompletion

    return ChatCompletion.model_validate(
        {
            "id": "test",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {"role": "assistant", "content": text, "refusal": refusal},
                }
            ],
        }
    )


def test_installed_sdk_response_and_environment_model(monkeypatch):
    sent = fake_provider(monkeypatch, response())
    monkeypatch.setenv("OPENAI_MODEL", "environment-model")
    assert app.call_teacher_llm("입력 프롬프트") == "합성 설명"
    assert sent["model"] == "environment-model"
    assert sent["messages"][0]["content"] == "입력 프롬프트"
    app.call_teacher_llm("입력", model="explicit-model")
    assert sent["model"] == "explicit-model"


@pytest.mark.parametrize(
    "finish,text,refusal",
    [
        ("length", "일부 응답", None),
        ("content_filter", None, None),
        ("stop", "", None),
        ("stop", None, "거절"),
    ],
)
def test_incomplete_empty_and_refused_response_are_errors(monkeypatch, finish, text, refusal):
    fake_provider(monkeypatch, response(finish, text, refusal))
    with pytest.raises(app.GenerationError):
        app.call_teacher_llm("입력")


def test_provider_error_does_not_expose_raw_response(monkeypatch):
    fake_provider(monkeypatch, error=RuntimeError("private provider detail"))
    with pytest.raises(app.GenerationError) as exc:
        app.call_teacher_llm("입력")
    assert "private provider detail" not in str(exc.value)


def test_cli_works_outside_repository_without_key(tmp_path):
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    script = app.ROOT / "auto_ctx.py"
    base = [
        sys.executable,
        str(script),
        "--csv",
        str(app.ROOT / "data/sample_input.csv"),
        "--out",
        str(tmp_path / "out.jsonl"),
    ]
    preview = subprocess.run(
        [*base, "--dry-run"], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert preview.returncode == 0, preview.stderr
    assert len((tmp_path / "out.jsonl").read_text().splitlines()) == 3
    failure = subprocess.run(base, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert failure.returncode == 1
    assert all(
        json.loads(x)["record_type"] == "error"
        for x in (tmp_path / "out.jsonl").read_text().splitlines()
    )
