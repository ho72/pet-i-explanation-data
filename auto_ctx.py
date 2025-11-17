#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_ctx_pipeline_full.py
-------------------------
JSON(dir) or CSV → (진단별 로컬 KB + DuckDuckGo + Wikipedia) 검색 → CTX 구성 →
Teacher LLM(근거 제한) → 검증 → SFT JSONL 자동 생성(툴콜 궤적 포함)

필수:
    pip install ddgs wikipedia openai

예시:
    python auto_ctx.py \
        --json_dir ./data/final_json \
        --out ./data/sft_samples.jsonl \
        --k 20 --ctx_sections 3 \
        --use_ddg --use_wiki
"""
from __future__ import annotations
import argparse, csv, json, os, sys, re, traceback
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple

import os, unicodedata, pathlib

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
DIAGNOSES = ["결막염", "궤양성각막질환", "백내장", "안검염", "안검내반증", "유루증"]
ALIASES = {
    "결막염": ["conjunctivitis dog", "canine conjunctivitis", "conjunctiva dog"],
    "궤양성각막질환": ["corneal ulcer dog", "ulcerative keratitis dog", "canine corneal ulcer"],
    "백내장": ["cataract dog", "canine cataract", "lens opacity dog"],
    "안검염": ["blepharitis dog", "canine blepharitis", "eyelid inflammation dog"],
    "안검내반증": ["entropion dog", "eyelid entropion dog"],
    "유루증": ["epiphora dog", "tear staining dog", "excessive tearing dog"],
}

# 권위 도메인 비상 시드(진단별 URL 보장)
SEED_KB: Dict[str, List[Dict[str, str]]] = {
    "결막염": [
        {"id":"seed_merck_conj","title":"Merck Vet Manual — Conjunctiva (dogs)","text":"반려견 결막염은 감염, 알레르기, 자극 요인 등으로 결막 충혈·분비물 증가가 흔합니다. 임상에서는 충혈, 부종(chemosis), 분비물 성상(수양/점액/고름)을 평가합니다.","source":"seed","url":"https://www.merckvetmanual.com/eye-diseases-and-disorders/ophthalmology/conjunctiva"},
        {"id":"seed_vca_conj","title":"VCA — Conjunctivitis in Dogs","text":"결막 염증은 통증/깜빡임 증가와 함께 분비물·충혈을 동반할 수 있습니다. 원인 규명과 위생 관리가 중요합니다.","source":"seed","url":"https://vcahospitals.com/know-your-pet/conjunctivitis-in-dogs"},
    ],
    "궤양성각막질환": [
        {"id":"seed_merck_ulcer","title":"Merck Vet Manual — Corneal Ulcers (dogs)","text":"각막 궤양은 표면 결손과 통증, 눈물 과다, 혼탁이 특징입니다. 지연 시 감염 악화·용해성 궤양·천공 위험이 큽니다.","source":"seed","url":"https://www.merckvetmanual.com/eye-diseases-and-disorders/corneal-disease/corneal-ulcers-in-dogs"},
        {"id":"seed_vca_ulcer","title":"VCA — Corneal Ulcers in Dogs","text":"범위는 표층부터 심부 궤양까지 다양하며 형광염색 검사가 유용합니다. 신속한 처치가 예후에 중요합니다.","source":"seed","url":"https://vcahospitals.com/know-your-pet/corneal-ulcers-in-dogs"},
    ],
    "백내장": [
        {"id":"seed_cornell_cat","title":"Cornell — Canine Cataracts","text":"백내장은 수정체 혼탁으로 시력 저하를 유발합니다. 노령·유전·대사성 요인 등 원인이 다양하며 핵경화와 감별이 필요합니다.","source":"seed","url":"https://www.vet.cornell.edu/departments-centers-and-institutes/riney-canine-health-center/canine-health-information/canine-cataracts"},
        {"id":"seed_vca_cat","title":"VCA — Cataracts in Dogs","text":"동공 뒤 혼탁과 반사 저하가 단서이며, 진행도에 따라 관리·수술 여부를 결정합니다.","source":"seed","url":"https://vcahospitals.com/know-your-pet/cataracts-in-dogs"},
    ],
    "안검염": [
        {"id":"seed_vca_bleph","title":"VCA — Blepharitis in Dogs","text":"안검 가장자리 염증으로 발적·비후·딱지·가려움이 흔합니다. 원인 다양하며 위생 및 원인 치료가 핵심입니다.","source":"seed","url":"https://vcahospitals.com/know-your-pet/blepharitis-in-dogs"}
    ],
    "안검내반증": [
        {"id":"seed_vca_entro","title":"VCA — Eyelid Entropion in Dogs","text":"안검 내반은 속눈썹/피부가 각막을 문질러 궤양 위험을 높입니다. 중증은 수술 교정이 권장됩니다.","source":"seed","url":"https://vcahospitals.com/know-your-pet/eyelid-entropion-in-dogs"}
    ],
    "유루증": [
        {"id":"seed_vca_epi","title":"VCA — Eye Discharge (Epiphora) in Dogs","text":"눈물 배출 장애 또는 과다 분비로 눈가 젖음과 착색이 생깁니다. 원인에 따라 관리와 치료가 달라집니다.","source":"seed","url":"https://vcahospitals.com/know-your-pet/eye-discharge-or-epiphora-in-dogs"}
    ],
}

# -------------------------
# 2) 디버그 스냅샷
# -------------------------
def save_retrieval_debug(text_name: str, stage: str, items: List[Dict[str, Any]]) -> None:
    try:
        Path("debug").mkdir(exist_ok=True)
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
        with open(f"debug/{text_name}.{stage}.json", "w", encoding="utf-8") as f:
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
                            "id": f"ddg::{hash((q,url)) & 0xfffffff:x}",
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
                text = page.content[: max_chars]
                out.append(
                    {
                        "id": f"wiki::{hash((t,query)) & 0xfffffff:x}",
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
    try:
        host = re.sub(r"^https?://", "", url).split("/")[0].lower()
        return any(host.endswith(d) for d in PREFERRED_DOMAINS)
    except Exception:
        return False

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
    seen = set(); out = []
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
        if domain_ok(url): score += 5
        if d.get("source") == "ddg": score += 1
        if d.get("source") == "wiki": score += 1
        if "dog" in (d.get("title") or "").lower(): score += 1
        scored.append((score, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in scored[:top_n]]

def inject_seed_if_needed(docs: List[Dict[str, Any]], diagnosis: str) -> List[Dict[str, Any]]:
    if any(d.get("url") for d in docs):
        return docs
    return (SEED_KB.get(diagnosis, [])[:1] + docs)

# -------------------------
# 5) 로컬 코퍼스(진단별로만 사용)
# -------------------------
def load_local_corpus_by_diag(corpus_dir: str = "./corpus") -> Dict[str, List[Dict[str, Any]]]:
    """
    corpus/*.txt 파일 중 파일명에 진단명이 포함된 것만
    해당 진단의 문서로 매핑한다.
    """
    by_diag: Dict[str, List[Dict[str, Any]]] = {d: [] for d in DIAGNOSES}
    #p = Path(corpus_dir)
    p = pathlib.Path("./corpus")
    for q in p.glob("*"):
        nfc = unicodedata.normalize("NFC", q.name)
        if nfc != q.name:
            q.rename(q.with_name(nfc))
            #print("RENAMED:", q.name, "->", nfc)
    if not p.exists():
        return by_diag
    for fp in p.glob("*.txt"):
        name = fp.stem
        target_diag: Optional[str] = None
        for d in DIAGNOSES:
            if d in name:
                target_diag = d
                break
        if not target_diag:
            continue
        try:
            text = fp.read_text(encoding="utf-8").strip()
            if not text:
                continue
            lines = text.splitlines()
            title = lines[0][:200] if lines else fp.stem
            body = "\n".join(lines[1:]) if len(lines) > 1 else ""
            by_diag[target_diag].append(
                {
                    "id": f"local::{fp.stem}",
                    "title": title,
                    "text": body[:6000],
                    "url": None,
                    "source": "local",
                }
            )
        except Exception:
            continue
    return by_diag

# -------------------------
# 6) 입력 로딩
# -------------------------
def load_rows_from_csv(csv_path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            diag = (r.get("diagnosis") or "").strip()
            sym = (r.get("symptoms") or "").strip()
            symlist = [s.strip() for s in re.split(r"[;,]\s*|\]\s*\[", sym) if s.strip()] if sym else []
            rows.append(
                {
                    "text_name": Path(r.get("image_path","")).stem or f"row_{len(rows)+1}",
                    "report_json": {"diagnosis": diag, "symptoms": symlist},
                    "ctx": [],
                    "instructions": {"freeze_report": True, "use_ctx_only": True, "sections": ["overview","reasoning","causes","care"]},
                }
            )
    return rows

def _normalize_json_input(obj: Dict[str, Any]) -> Dict[str, Any]:
    text_name = obj.get("text_name") or "unknown"
    norm = obj.get("normalized") or {}
    diag = (norm.get("diagnosis") or "").strip().split("/")[0]
    syms = norm.get("symptoms") or []
    report_json = obj.get("report_json") or {"diagnosis": diag, "symptoms": syms}
    instr = obj.get("instructions") or {"freeze_report": True, "use_ctx_only": True, "sections": ["overview","reasoning","causes","care"]}
    base_ctx = obj.get("ctx") or []
    return {
        "text_name": text_name,
        "report_json": report_json,
        "ctx": base_ctx,
        "instructions": instr,
    }

def load_rows_from_json_dir(json_dir: str) -> List[Dict[str, Any]]:
    rows = []
    for fp in Path(json_dir).glob("*.json"):
        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
            obj = raw.get("input") if isinstance(raw, dict) and "input" in raw else raw
            if not isinstance(obj, dict):
                continue
            rows.append(_normalize_json_input(obj))
        except Exception:
            continue
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
                    "id": f"base::{c.get('idx',0)}",
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
    merged = filter_docs(merged, min_len=120)
    save_retrieval_debug(text_name, "after_filter1", merged)

    merged = prefer_authority(merged, top_n=max(k*2, 12))
    merged = dedup_near_duplicates(merged, by_url=True)
    save_retrieval_debug(text_name, "after_dedup", merged)

    # 6) 부족 시 완화
    if len(merged) < max(4, k//2):
        print(f"[{text_name}] fallback: relax filters (ddg/wiki retry + shorter len)")
        relaxed: List[Dict[str, Any]] = []
        for d in local_docs:
            if (d.get("text") or ""):
                relaxed.append(d)
        # (다시 시드/필터/스코어/디듀프)
        relaxed = inject_seed_if_needed(relaxed, diagnosis)
        relaxed = filter_docs(relaxed, min_len=60)
        relaxed = prefer_authority(relaxed, top_n=max(k*2, 12))
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
    report_json: Dict[str, Any],
    ctx: List[Dict[str, Any]],
    use_ctx_only: bool,
    sections: List[str]
) -> str:
    ctx_lines = []
    for c in ctx[:20]:
        line = (
            f"[{c['idx']}] {c.get('title','Untitled')} :: {c.get('url') or 'no-url'}\n"
            f"{(c.get('excerpt') or '')[:1500]}"
        )
        ctx_lines.append(line)
    ctx_block = "\n\n".join(ctx_lines)

    diag = report_json.get("diagnosis") or ""
    syms = report_json.get("symptoms") or []
    #sec_str = ", ".join(sections)
    SECTION_MAP = {
        "overview": "질병에 대한 설명",
        "reasoning": "진단 근거",
        "causes": "주요 발생 원인",
        "care": "관리 방법"
    }
    mapped_sections = [SECTION_MAP.get(s, s) for s in sections]
    sec_str = ", ".join(mapped_sections)
    prompt = f"""당신은 반려동물 안과 보고서를 작성하는 수의사입니다.
아래 [CTX]는 사용자가 검색한 '핵심 근거 자료'입니다.
[CTX]의 내용을 **사실의 근거로 사용하되, 당신의 전문 지식을 더하여** 보호자가 이해하기 쉽게 **자세하고 친절하게 설명**해주세요.
[CTX]에서 직접 인용한 사실은 [n]으로 표기하고, 당신의 지식으로 부연 설명하는 부분은 표기하지 않아도 됩니다.

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
- 인용 비율: 전체 문장의 80% 이상은 [번호] 인용 포함
- CTX로 지지되지 않는 내용은 작성하지 말 것
- 중요 단어나 문장에는 **굵은 글씨** 사용
- 마크다운으로 작성

지금부터 위 요구사항을 엄격히 지켜 보고서를 작성하세요.
"""
    return prompt

def call_teacher_llm(
    prompt: str,
    model: Optional[str] = None,
    api_key_env: str = "OPENAI_API_KEY",
    timeout: int = 60,
) -> Optional[str]:
    """OpenAI Chat Completions (환경변수에서 키를 읽음)."""
    try:
        #api_key = os.getenv(api_key_env, "")
        api_key = <YOUT_API_KEY>
        if not api_key:
            print(f"[LLM] 환경변수 {api_key_env}가 비어있습니다. 규칙 기반 폴백을 사용합니다.")
            return None
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        use_model = model or os.getenv("OPENAI_MODEL", "gpt-4o")
        resp = client.chat.completions.create(
            model=use_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            timeout=timeout,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or None
    except Exception as e:
        print("[LLM] 호출 실패:", repr(e))
        return None

def rule_based_report(report_json: Dict[str, Any], ctx: List[Dict[str, Any]], sections: List[str]) -> str:
    return "오류 - 풀백"

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
    final_report: str
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
            "이 사진을 보고 반려견 안과 보고서를 작성하세요. "
            "섹션은 다음 순서로 고정하고, 각 문장의 사실은 근거 [n]로 인용하세요.\n"
            f"- 섹션: {', '.join(sections)}\n"
            f"- 진단명: {report_json.get('diagnosis','')}\n"
            f"- 증상: {', '.join(syms)}"
        )
    }

    # 2) assistant: search 툴콜
    tool_calls = []
    for q in used_queries[:3] or ALIASES.get(report_json.get("diagnosis",""), [])[:2]:
        tool_calls.append({"name": "search", "arguments": {"q": q}})
    asst_call_search = {
        "role": "assistant",
        "content": "진단/증상 기반 키워드로 검색을 시작합니다.",
        "tool_calls": tool_calls
    }

    # 3) tool(search) 결과 요약(상위 6개만 요약)
    search_results = []
    for d in raw_docs[:6]:
        search_results.append({
            "id": d.get("id") or d.get("url") or "doc",
            "title": d.get("title") or "Untitled",
            "url": d.get("url"),
            "snippet": (d.get("text") or "")[:280]
        })
    tool_msg_search = {
        "role": "tool",
        "name": "search",
        "content": search_results
    }

    # 4) assistant: fetch 선택(여기서는 numbered CTX의 idx들로 fetch 시연)
    fetch_calls = [{"name": "fetch", "arguments": {"id": f"ctx::{c['idx']}"}} for c in ctx_numbered[:4]]
    asst_call_fetch = {
        "role": "assistant",
        "content": "관련성이 높은 문서를 열람합니다.",
        "tool_calls": fetch_calls
    }

    # 5) tool(fetch): 각 문서의 요약
    fetch_results = []
    for c in ctx_numbered[:4]:
        fetch_results.append({
            "id": f"ctx::{c['idx']}",
            "title": c.get("title"),
            "url": c.get("url"),
            "excerpt": (c.get("excerpt") or "")[:600]
        })
    tool_msg_fetch = {
        "role": "tool",
        "name": "fetch",
        "content": fetch_results
    }

    # 6) assistant: 최종 보고서 (teacher 출력)
    final_msg = {
        "role": "assistant",
        "content": final_report
    }

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
) -> None:
    local_by_diag = load_local_corpus_by_diag("./corpus")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as out_f:
        for ridx, row in enumerate(rows, 1):
            try:
                text_name = (row.get("text_name") or f"row_{ridx}").strip()
                report_json = row.get("report_json") or {}
                diagnosis = (report_json.get("diagnosis") or "").strip()
                base_ctx = row.get("ctx") or []
                instr = row.get("instructions") or {}
                use_ctx_only = bool(instr.get("use_ctx_only", True))
                sections = instr.get("sections") or ["overview", "reasoning", "causes", "care"]

                if not diagnosis:
                    for d in DIAGNOSES:
                        if d in text_name:
                            diagnosis = d
                            report_json["diagnosis"] = d
                            break
                    if not diagnosis:
                        raise ValueError("진단명이 없습니다(report_json.normalized.diagnosis 사용 필요).")

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
                teacher_text = call_teacher_llm(prompt, model="gpt-4o")
                if not teacher_text:
                    print("[PIPELINE] LLM 실패 → 규칙 기반 폴백으로 전환합니다.")
                    teacher_text = rule_based_report(report_json, ctx_numbered, sections)

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
                            "distinct_refs>=2": (len({c.get('url') for c in ctx_numbered if c.get('url')}) >= 2),
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
                    final_report=teacher_text
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
                    }
                }
                out_f.write(json.dumps(record_sft, ensure_ascii=False) + "\n")

                print(f"[OK] {text_name} (#{ridx})")
            except Exception as e:
                traceback.print_exc()
                fail_rec = {
                    "record_type": "error",
                    "input": row,
                    "output": {"text": "규칙 기반 폴백 리포트: 처리 중 예외가 발생했습니다."},
                    "error": repr(e),
                }
                out_f.write(json.dumps(fail_rec, ensure_ascii=False) + "\n")
                print(f"[FAIL] row #{ridx}")
    print(f"\n[Done] saved -> {out_path}")

# -------------------------
# 11) 엔트리포인트
# -------------------------
def parse_args():
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
    return p.parse_args()

def main():
    args = parse_args()
    if args.csv:
        rows = load_rows_from_csv(args.csv)
    else:
        rows = load_rows_from_json_dir(args.json_dir)

    if not rows:
        print("[Error] 입력 레코드가 없습니다.")
        sys.exit(2)

    process_rows(
        rows=rows,
        out_path=args.out,
        use_ddg=args.use_ddg,
        use_wiki=args.use_wiki,
        k=args.k,
        ctx_sections=args.ctx_sections,
        wiki_pages=args.wiki_pages,
    )

if __name__ == "__main__":
    main()
