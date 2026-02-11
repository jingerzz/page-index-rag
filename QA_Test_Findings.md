# QA Test Findings — pageindex-rag

**Test plan:** `QA_Test_Plan.md`  
**Environment:** Windows 10, PowerShell, working directory `C:\Users\jxie0\pageindex-rag`  
**Run date (initial QA):** 2025-02-10

This document summarizes the initial QA run, follow-up work, discussions, and handoff notes for the coding assistant.

---

## Executive summary

1. **Initial QA run** (no API key): Phases 1–2, 4–5, 6 (partial), 8–9 passed. Phase 3 (LLM) failed with 401. Phase 7 (PDF indexing) was skipped (no test PDF).
2. **API key added:** User provided an OpenRouter API key; it was saved to `config.json`.
3. **402 (credits) fix:** LLM calls were failing with 402 (“You requested up to 65535 tokens, but can only afford …”). The client was not setting `max_tokens`, so the default was too large. We added an explicit `max_tokens` to all LLM calls.
4. **Configurable `max_tokens`:** `max_tokens` is now read from `config.json` (key `max_tokens`, default 8192). Rationale: 4096 is enough for markdown/summaries; PDF TOC extraction can return large JSON, so 8192 is the intended default for real PDF indexing. User added credits to support this.
5. **Security:** A project `.gitignore` was added. It includes `config.json` so the API key is never committed. `config.example.json` was updated to show `max_tokens`; copy it to `config.json` and add your key locally.
6. **Real PDF test (CAT 10K):** We ran the full PDF indexing pipeline on `CAT_2025_10K.pdf` (copied to `data/drop/`). TOC detection and processing started successfully, but indexing failed in `toc_transformer` with `json.decoder.JSONDecodeError: Extra data: line 379 column 2`. The model returned output that was not a single clean JSON object (e.g. extra text or multiple JSON blobs). This is a **parsing/robustness issue** in the PDF TOC step, not a `max_tokens` limit.

**Handoff for coding assistant:** Re-run Phase 3 (LLM) and Phase 7 (PDF) per the test plan now that the key and credits are in place. Fix PDF TOC parsing to handle malformed or multi-part LLM JSON (e.g. extract first valid JSON array or strip trailing text before `json.loads`).

---

## What we did and discussed

### Initial QA run (no API key)

- Ran all phases from `QA_Test_Plan.md`. Phases 1–2, 4–5, 8–9 passed. Phase 3 failed (401). Phase 6 passed (tree built; LLM summaries failed with 401). Phase 7 skipped (no PDF).
- Created `QA_Test_Findings.md` with phase-by-phase results.

### API key and config

- User provided OpenRouter API key. We created `config.json` from `config.example.json` and stored the key there. T1.3 (config loading) then showed key length > 0 and model name.

### LLM 402 and max_tokens

- After adding the key, LLM tests failed with **402** (“This request requires more credits, or fewer max_tokens. You requested up to 65535 tokens …”). The OpenAI client was not passing `max_tokens`, so the provider default (65535) was used and exceeded the user’s credit limit.
- We set `max_tokens=4096` in `src/llm.py` so tests could pass. Then we made it **configurable**: added `_get_max_tokens()` in `src/llm.py` reading from `config.json` with default **8192**, and use it in `llm_call`, `llm_call_with_finish_reason`, and `llm_call_async`.
- **Discussion:** 4096 is enough for markdown indexing and short summaries. For PDF indexing, the pipeline asks the model for large table-of-contents JSON; 8192 is the intended default so long PDFs (e.g. 10K) can return full TOC in one response. User added credits; `config.json` and `config.example.json` now include `"max_tokens": 8192`.

### Security

- **Recommendation:** Ensure `config.json` is not committed (it contains the API key).  
- **Done:** Added a project-level `.gitignore` with `config.json`, `__pycache__/`, `.venv/`, `logs/`, and other common entries. No project `.gitignore` existed before (only `.venv/.gitignore`).

### Real PDF test (CAT 10K)

- Copied `CAT 2025 10K.pdf` from the user’s path to `data/drop/CAT_2025_10K.pdf`.
- Ran `index_document(path)` for that PDF. Pipeline ran for ~55s: “Parsing PDF…”, “toc found”, “process_toc_with_page_numbers”, “start toc_transformer”. Then failure in `page_index.toc_transformer` at `json.loads(last_complete)` → `JSONDecodeError: Extra data: line 379 column 2`. So the LLM returned content that is not a single valid JSON object (e.g. multiple objects or trailing text). **Recommended fix:** Harden TOC parsing (e.g. in `page_index.py` / `utils.extract_json` or equivalent): extract the first complete JSON array/object from the response, or strip non-JSON trailing text, before calling `json.loads`.

---

## QA results summary (after follow-up)

| Phase | Description | Initial result | After follow-up |
|-------|-------------|---------------|-----------------|
| 1 | Environment & Build | PASS (T1.3 partial: no key) | PASS (key in config) |
| 2 | Parsers | PASS (T2.4 skipped) | Unchanged |
| 3 | LLM Client | FAIL (401) | **PASS** (key + max_tokens fix; T3.1–T3.3 re-run) |
| 4 | Tree Store | PASS | Unchanged |
| 5 | Tree Search | PASS | Unchanged |
| 6 | Markdown Indexing | PASS (summaries 401) | PASS with key |
| 7 | PDF Indexing | SKIP | **Attempted** – TOC parsing error (see above) |
| 8 | MCP Server | PASS | Unchanged |
| 9 | Edge Cases | PASS | Unchanged |

---

## Detailed QA results (initial run)

### Phase 1: Environment & Build

| Test | Result | Details |
|------|--------|--------|
| **T1.1** — uv sync | **PASS** | Exit code 0. Resolved 62 packages, audited 62 packages. |
| **T1.2** — Entry points | **PASS** | `pageindex-rag` printed help. `ingest` ran and reported "No supported files found". `manage-docs` reported "No documents indexed yet". |
| **T1.3** — Config loading | **Partial** (then PASS) | Initially key_len: 0. After adding key: model and key length OK. |

### Phase 2: Parsers

| Test | Result | Details |
|------|--------|--------|
| **T2.1** — Text parser | **PASS** | Text and metadata with `source_file`, `file_type` (text). |
| **T2.2** — CSV parser | **PASS** | Natural-language rows and metadata with `rows`, `columns`. |
| **T2.3** — HTML parser | **PASS** | Extracted text (no tags) and metadata. |
| **T2.4** — PDF parser | **SKIP** | No PDF in `data/drop/` initially; later CAT 10K used for real test. |
| **T2.5** — parse_file router | **PASS** | Printed `routed: text` for `.txt` file. |

### Phase 3: LLM Client

| Test | Result | Details |
|------|--------|--------|
| **T3.1** — Sync LLM call | **FAIL** → **PASS** | First 401 (no key). Then 402 (max_tokens). After setting configurable max_tokens (8192) and adding credits: response "HELLO", PASS. |
| **T3.2** — LLM with finish reason | **Not run** → **PASS** | reason: finished, content OK. |
| **T3.3** — Async LLM call | **Not run** → **PASS** | response "ASYNC_OK", PASS. |

### Phase 4: Tree Store

| Test | Result | Details |
|------|--------|--------|
| **T4.1** — Save/load/delete | **PASS** | Saved doc_id, loaded tree, verified doc_name, delete cleared tree. |
| **T4.2** — List trees | **PASS** | Tree appeared in list; cleanup after test. |

### Phase 5: Tree Search

| Test | Result | Details |
|------|--------|--------|
| **T5.1** — Search matching nodes | **PASS** | Query "machine learning" returned 1 result; overview contained "Introduction". |
| **T5.2** — Search with doc_id filter | **PASS** | Filtered to doc_a: 1 result; filtered to doc_b: 0 results. |

### Phase 6: Markdown Indexing

| Test | Result | Details |
|------|--------|--------|
| **T6.1** — Index markdown | **PASS** | Markdown indexed; tree had 2 root nodes. Initially LLM 401; with key, indexing and summaries work. |

### Phase 7: PDF Indexing

| Test | Result | Details |
|------|--------|--------|
| **T7.1** — Index PDF via ingest | **SKIP** → **Attempted** | CAT 10K copied to `data/drop/`. Direct `index_document()` run failed in `toc_transformer` (JSONDecodeError: Extra data). |
| **T7.2** — Verify indexed PDF | **SKIP** | Not run (indexing did not complete). |
| **T7.3** — Search indexed PDF | **SKIP** | Not run. |

### Phase 8: MCP Server

| Test | Result | Details |
|------|--------|--------|
| **T8.1** — Server starts | **PASS** | No import/crash errors. |
| **T8.2** — list_documents | **PASS** | Correct message when no docs. |
| **T8.3** — search_documents | **PASS** | Found "Revenue" / "revenue". |
| **T8.4** — get_document_section | **PASS** | Section text including "novel approach". |
| **T8.5** — remove_document | **PASS** | Document removed. |

### Phase 9: Edge Cases

| Test | Result | Details |
|------|--------|--------|
| **T9.1** — Unsupported file type | **PASS** | ValueError for .xyz. |
| **T9.2** — Empty drop folder | **PASS** | "No supported files found". |
| **T9.3** — Search with no docs | **PASS** | 0 results, no errors. |
| **T9.4** — Load nonexistent tree | **PASS** | None. |

---

## Code and config changes made

- **`src/llm.py`:** Added `_get_max_tokens()` reading `config.json` (default 8192). All three LLM entrypoints use `max_tokens=_get_max_tokens()` instead of a hardcoded value.
- **`config.json`:** Created/updated with `openrouter_api_key`, `model`, and `max_tokens` (8192). **Do not commit;** it is in `.gitignore`.
- **`config.example.json`:** Added `max_tokens: 8192` so new setups get a sensible default.
- **`.gitignore`:** New project-level file; includes `config.json`, `__pycache__/`, `.venv/`, `logs/`, and other common patterns.

---

## Recommendations for coding assistant

1. **Re-run QA:** With key and credits in place, re-run Phase 3 (T3.1–T3.3) and Phase 7 (T7.1–T7.3) from `QA_Test_Plan.md` to confirm LLM and PDF indexing.
2. **Fix PDF TOC parsing:** In `src/pageindex/page_index.py`, `toc_transformer` (and any similar path) calls `json.loads(last_complete)`. When the LLM returns extra text or multiple JSON objects, this raises `JSONDecodeError: Extra data`. Harden by: extracting the first complete JSON array/object from the response (e.g. find matching `[...]` or `{...}`), or using/reusing `extract_json` in `utils.py` to strip markdown and take the first valid JSON, then `json.loads` on that only.
3. **Optional – T1.3 in CI:** If running QA in CI without secrets, consider allowing key_len 0 as a warning rather than failure so environment/build and parser phases still pass.
4. **Secrets:** Keep using `config.example.json` in the repo; ensure every developer copies to `config.json` and adds their key locally. `config.json` is already in `.gitignore`.

---

*Summary and findings from QA run and follow-up; for handoff to coding assistant.*
