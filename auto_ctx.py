#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_ctx.py
-------------------------
JSON(dir) or CSV → (진단별 로컬 KB + DuckDuckGo + Wikipedia) 검색 → CTX 구성 →
Teacher LLM → 상태 기록 → service/SFT JSONL 생성(구성한 학습 대화 포함)

필수:
    pip install ddgs wikipedia openai

예시:
    python auto_ctx.py \
        --json_dir ./data/final_json \
        --out ./data/sft_samples.jsonl \
        --k 20 \
        --use_ddg --use_wiki
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DEBUG_DIR = Path("debug")
DEFAULT_SECTIONS = ["overview", "reasoning", "causes", "care"]


class GenerationError(RuntimeError):
    """A provider failure that must not become a training example."""


# -------------------------
# 0) 선택적 외부 검색기
# -------------------------
try:
    from ddgs import DDGS  # DuckDuckGo (pip install ddgs)
except Exception:
    DDGS = None

try:
    import wikipedia

    wikipedia.set_lang("en")
except Exception:
    wikipedia = None

# -------------------------
# 1) 전역 설정/상수
# -------------------------
PREFERRED_DOMAINS = [
    "merckvetmanual.com",
    "vcahospitals.com",
    "cornell.edu",
    "avma.org",
    "aaha.org",
    "acvo.org",
    "vin.com",
]

# 진단 라벨(국문)과 영어 검색 별칭
DIAGNOSES = ["결막염", "궤양성각막질환", "백내장", "안검염", "안검내반증", "유루증", "무증상"]
ALIASES = {
    "결막염": ["conjunctivitis dog", "canine conjunctivitis", "conjunctiva dog"],
    "궤양성각막질환": ["corneal ulcer dog", "ulcerative keratitis dog", "canine corneal ulcer"],
    "백내장": ["cataract dog", "canine cataract", "lens opacity dog"],
    "안검염": ["blepharitis dog", "canine blepharitis", "eyelid inflammation dog"],
    "안검내반증": ["entropion dog", "eyelid entropion dog"],
    "유루증": ["epiphora dog", "tear staining dog", "excessive tearing dog"],
    "무증상": ["healthy dog eyes", "canine eye health"],
}

# 권위 도메인 비상 시드(진단별 URL 보장)
SEED_KB: Dict[str, List[Dict[str, str]]] = {
    "결막염": [
        {
            "id": "seed_merck_conj",
            "title": "Merck Vet Manual — Conjunctiva (dogs)",
            "text": "반려견 결막염은 감염, 알레르기, 자극 요인 등으로 결막 충혈·분비물 증가가 흔합니다. 임상에서는 충혈, 부종(chemosis), 분비물 성상(수양/점액/고름)을 평가합니다.",
            "source": "seed",
            "url": "https://www.merckvetmanual.com/eye-diseases-and-disorders/ophthalmology/conjunctiva",
        },
        {
            "id": "seed_vca_conj",
            "title": "VCA — Conjunctivitis in Dogs",
            "text": "결막 염증은 통증/깜빡임 증가와 함께 분비물·충혈을 동반할 수 있습니다. 원인 규명과 위생 관리가 중요합니다.",
            "source": "seed",
            "url": "https://vcahospitals.com/know-your-pet/conjunctivitis-in-dogs",
        },
    ],
    "궤양성각막질환": [
        {
            "id": "seed_merck_ulcer",
            "title": "Merck Vet Manual — Corneal Ulcers (dogs)",
            "text": "각막 궤양은 표면 결손과 통증, 눈물 과다, 혼탁이 특징입니다. 지연 시 감염 악화·용해성 궤양·천공 위험이 큽니다.",
            "source": "seed",
            "url": "https://www.merckvetmanual.com/eye-diseases-and-disorders/corneal-disease/corneal-ulcers-in-dogs",
        },
        {
            "id": "seed_vca_ulcer",
            "title": "VCA — Corneal Ulcers in Dogs",
            "text": "범위는 표층부터 심부 궤양까지 다양하며 형광염색 검사가 유용합니다. 신속한 처치가 예후에 중요합니다.",
            "source": "seed",
            "url": "https://vcahospitals.com/know-your-pet/corneal-ulcers-in-dogs",
        },
    ],
    "백내장": [
        {
            "id": "seed_cornell_cat",
            "title": "Cornell — Canine Cataracts",
            "text": "백내장은 수정체 혼탁으로 시력 저하를 유발합니다. 노령·유전·대사성 요인 등 원인이 다양하며 핵경화와 감별이 필요합니다.",
            "source": "seed",
            "url": "https://www.vet.cornell.edu/departments-centers-and-institutes/riney-canine-health-center/canine-health-information/canine-cataracts",
        },
        {
            "id": "seed_vca_cat",
            "title": "VCA — Cataracts in Dogs",
            "text": "동공 뒤 혼탁과 반사 저하가 단서이며, 진행도에 따라 관리·수술 여부를 결정합니다.",
            "source": "seed",
            "url": "https://vcahospitals.com/know-your-pet/cataracts-in-dogs",
        },
    ],
    "안검염": [
        {
            "id": "seed_vca_bleph",
            "title": "VCA — Blepharitis in Dogs",
            "text": "안검 가장자리 염증으로 발적·비후·딱지·가려움이 흔합니다. 원인 다양하며 위생 및 원인 치료가 핵심입니다.",
            "source": "seed",
            "url": "https://vcahospitals.com/know-your-pet/blepharitis-in-dogs",
        }
    ],
    "안검내반증": [
        {
            "id": "seed_vca_entro",
            "title": "VCA — Eyelid Entropion in Dogs",
            "text": "안검 내반은 속눈썹/피부가 각막을 문질러 궤양 위험을 높입니다. 중증은 수술 교정이 권장됩니다.",
            "source": "seed",
            "url": "https://vcahospitals.com/know-your-pet/eyelid-entropion-in-dogs",
        }
    ],
    "유루증": [
        {
            "id": "seed_vca_epi",
            "title": "VCA — Eye Discharge (Epiphora) in Dogs",
            "text": "눈물 배출 장애 또는 과다 분비로 눈가 젖음과 착색이 생깁니다. 원인에 따라 관리와 치료가 달라집니다.",
            "source": "seed",
            "url": "https://vcahospitals.com/know-your-pet/eye-discharge-or-epiphora-in-dogs",
        }
    ],
}


# -------------------------
# 2) 디버그 스냅샷
# -------------------------
def save_retrieval_debug(text_name: str, stage: str, items: List[Dict[str, Any]]) -> None:
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        out = {
            "text_name": text_name,
            "stage": stage,
            "count": len(items),
            "items_preview": [
                {
                    "id": it.get("id"),
                    "title": it.get("title"),
                    "url": it.get("url"),
                    "source": it.get("source"),
                    "text_snippet": (it.get("text") or "")[:220],
                }
                for it in items[:50]
            ],
        }
        safe_name = re.sub(r"[^\w.-]", "_", text_name).strip(".")[:100] or "record"
        suffix = hashlib.sha256(text_name.encode()).hexdigest()[:8]
        with (DEBUG_DIR / f"{safe_name}-{suffix}.{stage}.json").open("w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# -------------------------
# 3) 외부 검색
# -------------------------
def ddg_search(queries: List[str], max_results: int = 8) -> Tuple[List[Dict[str, Any]], List[str]]:
    results = []
    used_queries: List[str] = []
    if DDGS is None:
        return results, used_queries
    try:
        with DDGS() as ddgs:
            for q in queries:
                got_any = False
                for r in ddgs.text(q, max_results=max_results):
                    url = r.get("href") or r.get("url") or ""
                    title = r.get("title") or ""
                    body = r.get("body") or ""
                    if not url or "bing.com/aclick" in url:
                        continue
                    results.append(
                        {
                            "id": f"ddg::{hash((q, url)) & 0xFFFFFFF:x}",
                            "title": title[:200],
                            "text": (body or "")[:2000],
                            "url": url,
                            "source": "ddg",
                            "q": q,
                        }
                    )
                    got_any = True
                if got_any:
                    used_queries.append(q)
    except Exception as e:
        print("[ddg] error:", repr(e))
    return results, used_queries


def wiki_chunks(query: str, max_pages: int = 2, max_chars: int = 3000) -> List[Dict[str, Any]]:
    out = []
    if wikipedia is None:
        return out
    try:
        titles = wikipedia.search(query, results=max_pages)
        for t in titles or []:
            try:
                page = wikipedia.page(title=t, auto_suggest=False, redirect=True)
                text = page.content[:max_chars]
                out.append(
                    {
                        "id": f"wiki::{hash((t, query)) & 0xFFFFFFF:x}",
                        "title": page.title,
                        "text": text,
                        "url": page.url,
                        "source": "wiki",
                        "q": query,
                    }
                )
            except Exception:
                continue
    except Exception as e:
        print("[wiki] error:", repr(e))
    return out


# -------------------------
# 4) 필터/정제/스코어
# -------------------------
def domain_ok(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in PREFERRED_DOMAINS)


def filter_docs(docs: List[Dict[str, Any]], min_len: int = 120) -> List[Dict[str, Any]]:
    out = []
    for d in docs:
        txt = (d.get("text") or "").strip()
        url = (d.get("url") or "").strip()
        if len(txt) < min_len:
            continue
        if url and "aclick" in url:
            continue
        out.append(d)
    return out


def dedup_near_duplicates(docs: List[Dict[str, Any]], by_url: bool = True) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for d in docs:
        key = d.get("url") if (by_url and d.get("url")) else (d.get("title") or "")[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def prefer_authority(docs: List[Dict[str, Any]], top_n: int = 12) -> List[Dict[str, Any]]:
    scored = []
    for d in docs:
        url = d.get("url") or ""
        score = 0
        if domain_ok(url):
            score += 5
        if d.get("source") == "ddg":
            score += 1
        if d.get("source") == "wiki":
            score += 1
        if "dog" in (d.get("title") or "").lower():
            score += 1
        scored.append((score, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in scored[:top_n]]


def inject_seed_if_needed(docs: List[Dict[str, Any]], diagnosis: str) -> List[Dict[str, Any]]:
    if any(d.get("url") for d in docs):
        return docs
    return SEED_KB.get(diagnosis, [])[:1] + docs


# -------------------------
# 5) 로컬 코퍼스(진단별로만 사용)
# -------------------------
def load_local_corpus_by_diag(
    corpus_dir: str | Path = ROOT / "corpus",
) -> Dict[str, List[Dict[str, Any]]]:
    by_diag = {d: [] for d in DIAGNOSES}
    for fp in sorted(Path(corpus_dir).glob("*.txt")):
        name = unicodedata.normalize("NFC", fp.stem)
        target = next((d for d in DIAGNOSES if d in name), None)
        if target is None:
            continue
        text = fp.read_text(encoding="utf-8").strip()
        if not text:
            continue
        lines = text.splitlines()
        by_diag[target].append(
            {
                "id": f"local::{name}",
                "title": lines[0][:200],
                "text": ("\n".join(lines[1:]) if len(lines) > 1 else text)[:6000],
                "url": None,
                "source": "local",
            }
        )
    return by_diag


# -------------------------
# 6) 입력 로딩
# -------------------------
def load_rows_from_csv(csv_path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not {"image_path", "diagnosis", "symptoms"}.issubset(reader.fieldnames or []):
            raise ValueError("CSV에는 image_path, diagnosis, symptoms 열이 필요합니다.")
        for r in reader:
            diag = (r.get("diagnosis") or "").strip()
            sym = (r.get("symptoms") or "").strip()
            symlist = (
                [s.strip() for s in re.split(r"[;,]\s*|\]\s*\[", sym) if s.strip()] if sym else []
            )
            rows.append(
                {
                    "text_name": Path(r.get("image_path", "")).stem or f"row_{len(rows) + 1}",
                    "report_json": {"diagnosis": diag, "symptoms": symlist},
                    "ctx": [],
                    "instructions": {
                        "freeze_report": True,
                        "use_ctx_only": True,
                        "sections": ["overview", "reasoning", "causes", "care"],
                    },
                }
            )
    return rows


def _normalize_json_input(obj: Dict[str, Any]) -> Dict[str, Any]:
    norm = obj.get("normalized") or {}
    report = obj.get("report_json") or {
        "diagnosis": (norm.get("diagnosis") or "").strip().split("/")[0],
        "symptoms": norm.get("symptoms", []),
    }
    if not isinstance(report, dict):
        raise ValueError("report_json은 객체여야 합니다.")
    return {
        "text_name": obj.get("text_name") or "unknown",
        "report_json": report,
        "ctx": obj.get("ctx") or [],
        "instructions": obj.get("instructions")
        or {"freeze_report": True, "use_ctx_only": True, "sections": DEFAULT_SECTIONS},
    }


def load_rows_from_json_dir(json_dir: str) -> List[Dict[str, Any]]:
    rows = []
    for fp in sorted(Path(json_dir).glob("*.json")):
        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
            obj = raw.get("input", raw) if isinstance(raw, dict) else raw
            if not isinstance(obj, dict):
                raise ValueError("JSON 입력은 객체여야 합니다.")
            rows.append(_normalize_json_input(obj))
        except (ValueError, TypeError, AttributeError, OSError) as exc:
            rows.append(
                {
                    "text_name": fp.stem,
                    "_input_error": f"JSON 입력을 읽을 수 없습니다 ({type(exc).__name__}).",
                }
            )
    return rows


# -------------------------
# 7) CTX 생성(진단별 로컬 → 외부 검색 → 필터/디듀프 → 시드 보강)
# -------------------------
def build_ctx_for_row(
    text_name: str,
    diagnosis: str,
    base_ctx: List[Dict[str, Any]],
    use_ddg: bool,
    use_wiki: bool,
    k: int,
    wiki_pages: int,
    local_by_diag: Dict[str, List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[str], List[Dict[str, Any]]]:
    """
    Returns:
        out_ctx: List[dict] with idx/title/excerpt/url
        used_queries: List[str]
        raw_docs: the un-numbered docs used to form out_ctx (for SFT tool-results)
    """
    merged: List[Dict[str, Any]] = []
    used_queries: List[str] = []

    # 1) 입력 base_ctx
    for c in base_ctx or []:
        if c.get("title") or c.get("excerpt"):
            merged.append(
                {
                    "id": f"base::{c.get('idx', 0)}",
                    "title": (c.get("title") or "")[:200],
                    "text": (c.get("excerpt") or "")[:4000],
                    "url": c.get("url"),
                    "source": "base",
                }
            )

    # 2) 로컬(해당 진단)
    local_docs = local_by_diag.get(diagnosis, [])
    merged += local_docs

    # 3) 외부 검색
    queries = ALIASES.get(diagnosis, [diagnosis + " dog"])
    ddg_docs: List[Dict[str, Any]] = []
    wiki_docs: List[Dict[str, Any]] = []

    if use_ddg:
        ddg_docs, used_ddg = ddg_search(queries, max_results=8)
        used_queries += used_ddg
        merged += ddg_docs
    if use_wiki:
        for q in queries[:2]:
            wdocs = wiki_chunks(q, max_pages=wiki_pages)
            if wdocs:
                used_queries.append(q)
            wiki_docs += wdocs
        merged += wiki_docs

    save_retrieval_debug(text_name, "collected_raw", merged)

    # 4) 시드 보강
    merged = inject_seed_if_needed(merged, diagnosis)

    # 5) 필터/스코어/디듀프
    collected = list(merged)
    merged = filter_docs(merged, min_len=120)
    save_retrieval_debug(text_name, "after_filter1", merged)

    merged = prefer_authority(merged, top_n=max(k * 2, 12))
    merged = dedup_near_duplicates(merged, by_url=True)
    save_retrieval_debug(text_name, "after_dedup", merged)

    # 6) 부족 시 완화
    if len(merged) < max(4, k // 2):
        print(f"[{text_name}] 컨텍스트 길이 필터 완화 (수집 근거 유지, 검색 재호출 없음)")
        relaxed = list(collected)
        # (다시 시드/필터/스코어/디듀프)
        relaxed = inject_seed_if_needed(relaxed, diagnosis)
        relaxed = filter_docs(relaxed, min_len=60)
        relaxed = prefer_authority(relaxed, top_n=max(k * 2, 12))
        relaxed = dedup_near_duplicates(relaxed, by_url=True)
        merged = relaxed
        save_retrieval_debug(text_name, "after_relax", merged)

    # 7) 상위 k개 선택 → base_ctx 스타일
    final_ctx = merged[:k]
    out_ctx = []
    for i, d in enumerate(final_ctx, 1):
        out_ctx.append(
            {
                "idx": i,
                "title": d.get("title") or "Untitled",
                "excerpt": (d.get("text") or "")[:2000],
                "url": d.get("url"),
            }
        )
    return out_ctx, used_queries, final_ctx


# -------------------------
# 8) Teacher LLM + 프롬프트
# -------------------------
def compose_teacher_prompt(
    report_json: Dict[str, Any], ctx: List[Dict[str, Any]], use_ctx_only: bool, sections: List[str]
) -> str:
    ctx_lines = []
    for c in ctx[:20]:
        line = (
            f"[{c['idx']}] {c.get('title', 'Untitled')} :: {c.get('url') or 'no-url'}\n"
            f"{(c.get('excerpt') or '')[:1500]}"
        )
        ctx_lines.append(line)
    ctx_block = "\n\n".join(ctx_lines)

    diag = report_json.get("diagnosis") or ""
    syms = report_json.get("symptoms") or []
    # sec_str = ", ".join(sections)
    SECTION_MAP = {
        "overview": "질병에 대한 설명",
        "reasoning": "진단 근거",
        "causes": "주요 발생 원인",
        "care": "관리 방법",
    }
    mapped_sections = [SECTION_MAP.get(s, s) for s in sections]
    sec_str = ", ".join(mapped_sections)
    grounding = (
        "[CTX]로 지지되는 내용만 작성하고 근거 번호 [n]을 표시하세요. 근거가 부족하면 부족하다고 명시하세요."
        if use_ctx_only
        else "[CTX]의 사실은 [n]으로 표시하세요. 일반적인 배경 지식을 보충할 때는 검색 근거와 구분하여 표현하세요."
    )
    prompt = f"""반려견 안구 질환의 설명형 학습 자료를 작성하세요.
아래 [CTX]는 사용자가 검색한 '핵심 근거 자료'입니다.
{grounding}
입력 진단명과 증상을 설명하되 새로운 진단을 추론하지 마세요.

[CTX]
{ctx_block}

[보고서 입력]
- 진단명: {diag}
- 증상: {", ".join(syms)}

[작성 요구사항]
- 섹션 순서와 이름은 반드시 다음을 따르세요: {sec_str}
- 문서 언어: 한국어
- 독자: 초보 보호자(쉬운 표현, 자세하고 친절한 설명)
- **말투: 모든 문장을 '...에요', '...해요' 스타일의 친근하고 부드러운 상호작용형 말투로 작성**
- 각 섹션은 최소 3문장 이상으로 상세히 기술할 것.
- 금지: 구체적 날짜·인명·수치·약품명 언급
- 사용한 검색 근거는 [번호]로 표시하고 존재하지 않는 번호를 만들지 말 것
- 중요 단어나 문장에는 **굵은 글씨** 사용
- 마크다운으로 작성

지금부터 위 요구사항을 엄격히 지켜 보고서를 작성하세요.
"""
    return prompt


def call_teacher_llm(
    prompt: str, model: Optional[str] = None, api_key_env: str = "OPENAI_API_KEY", timeout: int = 60
) -> str:
    api_key = os.getenv(api_key_env, "")
    if not api_key:
        raise GenerationError(
            "Teacher API 키가 설정되지 않았습니다. --dry-run으로 구조만 확인할 수 있습니다."
        )
    try:
        from openai import OpenAI

        with OpenAI(api_key=api_key) as client:
            response = client.chat.completions.create(
                model=model or os.getenv("OPENAI_MODEL", "gpt-4o"),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                timeout=timeout,
            )
        if not response.choices:
            raise GenerationError("Teacher 응답에 결과가 없습니다.")
        choice = response.choices[0]
        if choice.finish_reason != "stop":
            raise GenerationError("Teacher 응답이 정상 완료되지 않았습니다.")
        if getattr(choice.message, "refusal", None):
            raise GenerationError("Teacher가 생성 요청을 거절했습니다.")
        text = (choice.message.content or "").strip()
        if not text:
            raise GenerationError("Teacher가 빈 설명문을 반환했습니다.")
        return text
    except GenerationError:
        raise
    except Exception as exc:
        raise GenerationError(
            f"Teacher 호출에 실패했습니다 ({type(exc).__name__}). 모델 접근·연결·사용 한도를 확인하세요."
        ) from exc


# -------------------------
# 9) SFT 메시지 생성(툴콜 궤적)
# -------------------------
def create_sft_messages(
    text_name: str,
    report_json: Dict[str, Any],
    ctx_numbered: List[Dict[str, Any]],
    raw_docs: List[Dict[str, Any]],
    sections: List[str],
    used_queries: List[str],
    final_report: str,
) -> List[Dict[str, Any]]:
    """
    messages:
      - user: 과제/요구
      - assistant: 검색 의사 표현 + tool_calls(search)
      - tool(search): 검색 결과(요약 id+title)
      - assistant: fetch 호출
      - tool(fetch): 각 선택 문서의 요약(제목/URL/발췌)
      - assistant: 최종 보고서(근거 [n] 포함)
    """
    # 1) user 요구
    syms = report_json.get("symptoms") or []
    user_msg = {
        "role": "user",
        "content": (
            "제공된 진단명·증상·근거로 반려견 안구 질환 설명문을 작성하세요. "
            "섹션은 다음 순서로 고정하고, 각 문장의 사실은 근거 [n]로 인용하세요.\n"
            f"- 섹션: {', '.join(sections)}\n"
            f"- 진단명: {report_json.get('diagnosis', '')}\n"
            f"- 증상: {', '.join(syms)}"
        ),
    }

    # 2) assistant: search 툴콜
    tool_calls = []
    for q in used_queries[:3] or ALIASES.get(report_json.get("diagnosis", ""), [])[:2]:
        tool_calls.append({"name": "search", "arguments": {"q": q}})
    asst_call_search = {
        "role": "assistant",
        "content": "진단/증상 기반 키워드로 검색을 시작합니다.",
        "tool_calls": tool_calls,
    }

    # 3) tool(search) 결과 요약(상위 6개만 요약)
    search_results = []
    for d in raw_docs[:6]:
        search_results.append(
            {
                "id": d.get("id") or d.get("url") or "doc",
                "title": d.get("title") or "Untitled",
                "url": d.get("url"),
                "snippet": (d.get("text") or "")[:280],
            }
        )
    tool_msg_search = {"role": "tool", "name": "search", "content": search_results}

    # 4) assistant: fetch 선택(여기서는 numbered CTX의 idx들로 fetch 시연)
    fetch_calls = [
        {"name": "fetch", "arguments": {"id": f"ctx::{c['idx']}"}} for c in ctx_numbered[:4]
    ]
    asst_call_fetch = {
        "role": "assistant",
        "content": "관련성이 높은 문서를 열람합니다.",
        "tool_calls": fetch_calls,
    }

    # 5) tool(fetch): 각 문서의 요약
    fetch_results = []
    for c in ctx_numbered[:4]:
        fetch_results.append(
            {
                "id": f"ctx::{c['idx']}",
                "title": c.get("title"),
                "url": c.get("url"),
                "excerpt": (c.get("excerpt") or "")[:600],
            }
        )
    tool_msg_fetch = {"role": "tool", "name": "fetch", "content": fetch_results}

    # 6) assistant: 최종 보고서 (teacher 출력)
    final_msg = {"role": "assistant", "content": final_report}

    return [user_msg, asst_call_search, tool_msg_search, asst_call_fetch, tool_msg_fetch, final_msg]


# -------------------------
# 10) 메인 파이프라인
# -------------------------
def process_rows(
    rows: List[Dict[str, Any]],
    out_path: str,
    use_ddg: bool,
    use_wiki: bool,
    k: int,
    ctx_sections: int,
    wiki_pages: int = 2,
    dry_run: bool = False,
    model: str | None = None,
    corpus_dir: str | Path = ROOT / "corpus",
) -> dict[str, int]:
    if k < 1 or wiki_pages < 1:
        raise ValueError("k와 wiki_pages는 양수여야 합니다.")
    if dry_run and (use_ddg or use_wiki):
        raise ValueError("dry-run에서는 외부 검색을 사용할 수 없습니다.")
    local_by_diag = load_local_corpus_by_diag(corpus_dir)
    counts = {"success": 0, "preview": 0, "error": 0}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as out_f:
        for ridx, row in enumerate(rows, 1):
            try:
                if row.get("_input_error"):
                    raise ValueError(row["_input_error"])
                text_name = (row.get("text_name") or f"row_{ridx}").strip()
                report_json = row.get("report_json") or {}
                diagnosis = (report_json.get("diagnosis") or "").strip()
                base_ctx = row.get("ctx") or []
                instr = row.get("instructions") or {}
                use_ctx_only = instr.get("use_ctx_only", True)
                if not isinstance(use_ctx_only, bool):
                    raise ValueError("use_ctx_only는 true 또는 false여야 합니다.")
                sections = instr.get("sections") or ["overview", "reasoning", "causes", "care"]

                if not diagnosis:
                    for d in DIAGNOSES:
                        if d in text_name:
                            diagnosis = d
                            report_json["diagnosis"] = d
                            break
                    if not diagnosis:
                        raise ValueError(
                            "진단명이 없습니다(report_json.normalized.diagnosis 사용 필요)."
                        )

                symptoms = report_json.get("symptoms") or []
                if not isinstance(symptoms, list) or not all(isinstance(x, str) for x in symptoms):
                    raise ValueError("symptoms는 문자열 목록이어야 합니다.")
                if not isinstance(sections, list) or not all(
                    isinstance(x, str) and x.strip() for x in sections
                ):
                    raise ValueError("sections는 비어 있지 않은 문자열 목록이어야 합니다.")
                if not isinstance(base_ctx, list) or not all(isinstance(c, dict) for c in base_ctx):
                    raise ValueError("ctx는 객체 목록이어야 합니다.")
                # CTX 빌드(해당 진단 전용)
                ctx_numbered, used_queries, raw_docs = build_ctx_for_row(
                    text_name=text_name,
                    diagnosis=diagnosis,
                    base_ctx=base_ctx,
                    use_ddg=use_ddg,
                    use_wiki=use_wiki,
                    k=k,
                    wiki_pages=wiki_pages,
                    local_by_diag=local_by_diag,
                )

                # Teacher LLM
                prompt = compose_teacher_prompt(report_json, ctx_numbered, use_ctx_only, sections)
                if dry_run:
                    out_f.write(
                        json.dumps(
                            {
                                "record_type": "preview",
                                "input": row,
                                "ctx": ctx_numbered,
                                "prompt": prompt,
                                "meta": {
                                    "text_name": text_name,
                                    "used_queries": used_queries,
                                    "external_calls": False,
                                },
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    counts["preview"] += 1
                    print(f"[PREVIEW] {text_name} (#{ridx})")
                    continue
                teacher_text = call_teacher_llm(prompt, model=model)

                # ===== 1) 서비스용 포맷 (기존) =====
                record_service = {
                    "record_type": "service",
                    "input": {
                        "text_name": text_name,
                        "report_json": report_json,
                        "ctx": ctx_numbered,
                        "instructions": instr,
                    },
                    "output": {"text": teacher_text},
                    "meta": {
                        "quality": {
                            "sections": bool(sections),
                            "citations": any(c.get("url") for c in ctx_numbered),
                            "symptom_cov": bool(report_json.get("symptoms")),
                            "length_ok": len(teacher_text) >= 80,
                            "distinct_refs>=2": (
                                len({c.get("url") for c in ctx_numbered if c.get("url")}) >= 2
                            ),
                        },
                        "text_name": text_name,
                        "used_queries": used_queries,
                    },
                }
                out_f.write(json.dumps(record_service, ensure_ascii=False) + "\n")

                # ===== 2) SFT 학습용 포맷(툴콜 포함 대화) =====
                messages = create_sft_messages(
                    text_name=text_name,
                    report_json=report_json,
                    ctx_numbered=ctx_numbered,
                    raw_docs=raw_docs,
                    sections=sections,
                    used_queries=used_queries,
                    final_report=teacher_text,
                )
                record_sft = {
                    "record_type": "sft",
                    "messages": messages,
                    "meta": {
                        "diagnosis": diagnosis,
                        "symptoms": report_json.get("symptoms") or [],
                        "ctx_count": len(ctx_numbered),
                        "text_name": text_name,
                        "used_queries": used_queries,
                        "has_citations": ("[" in teacher_text and "]" in teacher_text),
                    },
                }
                out_f.write(json.dumps(record_sft, ensure_ascii=False) + "\n")

                counts["success"] += 1
                print(f"[OK] {text_name} (#{ridx})")
            except Exception as e:
                counts["error"] += 1
                fail_rec = {
                    "record_type": "error",
                    "input": row,
                    "error": str(e)
                    if isinstance(e, (ValueError, GenerationError))
                    else f"입력 처리 실패 ({type(e).__name__}).",
                }
                out_f.write(json.dumps(fail_rec, ensure_ascii=False) + "\n")
                print(f"[FAIL] row #{ridx}")
    print(f"\n[Done] saved -> {out_path} | {counts}")
    return counts


# -------------------------
# 11) 엔트리포인트
# -------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser()
    g_in = p.add_mutually_exclusive_group(required=True)
    g_in.add_argument("--csv", type=str, help="CSV with columns: image_path, diagnosis, symptoms")
    g_in.add_argument("--json_dir", type=str, help="Folder of JSON inputs (input dict or plain)")

    p.add_argument("--out", type=str, required=True, help="Output JSONL path")
    p.add_argument("--k", type=int, default=20, help="max ctx docs")
    p.add_argument("--ctx_sections", type=int, default=3, help="(예약) 섹션 수 힌트")
    p.add_argument("--use_ddg", action="store_true", help="Use DuckDuckGo search")
    p.add_argument("--use_wiki", action="store_true", help="Use Wikipedia search")
    p.add_argument("--wiki_pages", type=int, default=2)
    p.add_argument(
        "--dry-run", action="store_true", help="외부 검색·LLM 호출 없이 컨텍스트와 프롬프트 확인"
    )
    p.add_argument("--model", default=None, help="Teacher 모델 (--model > OPENAI_MODEL > gpt-4o)")
    p.add_argument("--corpus-dir", type=Path, default=ROOT / "corpus")
    p.add_argument("--debug-dir", type=Path, default=None, help="기본: 출력 파일 옆 debug 디렉터리")
    args = p.parse_args(argv)
    if args.k < 1 or args.wiki_pages < 1:
        p.error("--k와 --wiki_pages는 양수여야 합니다.")
    if args.dry_run and (args.use_ddg or args.use_wiki):
        p.error("--dry-run과 외부 검색 옵션은 함께 사용할 수 없습니다.")
    if args.ctx_sections != 3:
        print(
            "[WARN] --ctx_sections는 하위 호환용 예약 옵션입니다. JSON instructions.sections를 사용하세요.",
            file=sys.stderr,
        )
    return args


def main():
    args = parse_args()
    global DEBUG_DIR
    DEBUG_DIR = args.debug_dir or Path(args.out).parent / "debug"
    try:
        if args.csv:
            rows = load_rows_from_csv(args.csv)
        else:
            rows = load_rows_from_json_dir(args.json_dir)
    except (ValueError, OSError) as exc:
        print(f"[Error] {exc}", file=sys.stderr)
        sys.exit(2)

    if not rows:
        print("[Error] 입력 레코드가 없습니다.")
        sys.exit(2)

    counts = process_rows(
        rows=rows,
        out_path=args.out,
        use_ddg=args.use_ddg,
        use_wiki=args.use_wiki,
        k=args.k,
        ctx_sections=args.ctx_sections,
        wiki_pages=args.wiki_pages,
        dry_run=args.dry_run,
        model=args.model,
        corpus_dir=args.corpus_dir,
    )
    if counts["error"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
