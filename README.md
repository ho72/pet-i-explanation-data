# peti_make_explain_data

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
