# Changelog

All notable changes to pageindex-rag. This log helps coding agents understand what was built, what was changed from source projects, and why.

## Source Projects

This project combines code from two sources:
- **PageIndex** (`C:\Users\jxie0\Dropbox\Claude Code\PageIndex\`) — tree-based PDF/Markdown indexing engine
- **investment-rag** (`C:\Users\jxie0\investment-rag\`) — MCP server, CLI tools, file parsers, Rich UI

---

## [Unreleased]

*Record of recent agent activity for continuity. Implemented per PR-SEC-HTML-to-Markdown.md and follow-up CLI UX (SEC = HTML only, fetch-sec).*

### Added

- **Hierarchy-faithful HTML → Markdown** — New converter in `src/parsers/html_to_markdown.py` that maps HTML heading tags (`h1`–`h6`) to Markdown `#`–`######` and preserves document order. Used when indexing `.html`/`.htm` so that the tree is built from structure instead of flat text. For best results with SEC filings, use EDGAR primary HTML (or hierarchy-faithful Markdown). Logic: parse with BeautifulSoup, remove script/style, walk DOM in order; headings emit markdown headers, block elements (p, div, li, table, etc.) emit text; tables as line-based text. Input: `str | Path`; encoding UTF-8 with latin-1 fallback for file paths. Exported from `src/parsers/__init__.py` as `html_to_markdown`.

- **Tests for HTML → Markdown** — `tests/test_html_to_markdown.py`: unit tests for converter (headings/paragraphs, string input, script/style stripping, multiple heading levels) and integration test that runs converter → temp MD → `md_to_tree(..., if_add_node_summary='no', if_add_doc_description='no')` and asserts tree has multiple nodes (depth ≥ 2). Run: `uv run python tests/test_html_to_markdown.py`.

- **SEC filing fetch (HTML only)** — New command `uv run fetch-sec` in `src/fetch_sec.py`. Fetches **HTML** (not PDF) from SEC EDGAR; entry point in `pyproject.toml`: `fetch-sec = "src.fetch_sec:main"`.

- **Interactive SEC fetch flow** — `uv run fetch-sec` (and `uv run fetch-sec CAT`) use an **interactive** CLI like investment-rag: (1) **SEC User-Agent** — if missing or placeholder in `config.json`, prompt once and save as `sec_user_agent`; (2) **Ticker** — from argv or prompt; (3) **Form filter** — prompt “Filter by form type(s)? (e.g. 10-K,10-Q) [leave blank for all]”; (4) **Table** — Rich table of recent filings (#, Date, Type, Accession), up to 20; (5) **Selection** — prompt “Select filings (e.g. 1,3,6 or 1-5 or ‘all’)”; (6) **Download** — selected filings as HTML to `data/drop/`; (7) **Index now?** — Confirm.ask; if y, call `indexer.index_document()` for each and move to `data/processed/`. Helpers: `get_company_info(ticker)`, `get_filings_all_forms(cik, form_filters, limit)`, `_parse_selection(selection, max_val)` for `1`, `1-5`, `all`.

- **Config: sec_user_agent** — `config.example.json` and docs include `sec_user_agent` (Your Name (your.email@domain.com)) for SEC EDGAR; fetch-sec prompts once if missing or placeholder.

### Changed

- **fetch-sec: non-interactive → interactive** — Replaced argparse-only `uv run fetch-sec TICKER -f FORM -n INDEX` with the interactive flow above (ticker optional arg, form filter and selection via prompts, Rich table, “Index now?”). Aligns with investment-rag’s `fetch-sec` UX.

- **CLI / docs: SEC = HTML only, interactive** — CLI_CHEATSHEET.md and README describe interactive steps (User-Agent, ticker, form filter, table, selection, index now?). Commands table and `src/cli.py` help: “Interactive SEC fetch: ticker, form filter, table, select filings, then index?”

- **Indexer HTML path** — `.html`/`.htm` files are now converted to hierarchy-faithful Markdown and passed to `md_to_tree()` instead of being flattened and wrapped in a single `# filename` heading. In `src/indexer.py`: new branch `elif suffix in (".html", ".htm")` calls `html_to_markdown(filepath)`, writes to temp `.md`, runs `md_to_tree()` with same options as `.md`, then cleans up temp file/dir. Produces multi-node trees for SEC EDGAR HTML (and any HTML with headings) with no LLM calls for structure. `parse_file()` and `parse_html()` are unchanged; only indexer routing for HTML changed.

- **CLI / docs: SEC = HTML only** — CLI user experience for SEC filings is now “fetch HTML, never PDF.” `CLI_CHEATSHEET.md`: Daily Workflow includes `uv run fetch-sec AAPL -f 10-K`; Commands table adds `fetch-sec`; new section “SEC Filings (HTML only — no PDF)” explains why HTML and documents `fetch-sec`. Supported File Types table notes HTML as “preferred for SEC filings.” `src/cli.py`: help lists `uv run fetch-sec` and workflow step “For SEC filings: run fetch-sec (HTML only).”

- **README** — Technical Notes: added bullet “HTML indexing: Converted to hierarchy-faithful Markdown … best results with SEC EDGAR primary HTML; no LLM calls for structure.” Supported File Types table: `.html`/`.htm` row now “Hierarchy-faithful Markdown (h1–h6 → #–######) -> md_to_tree().”

- **Docs and cheatsheet refocused on interactive CLI and SEC HTML → PageIndex RAG** — **CLI_CHEATSHEET.md**: Removed “drop files first” as primary workflow, Supported File Types table, Quick Python Access, long Daily Workflow; reframed around Primary Workflow = fetch-sec → manage-docs → rag-server; SEC Interactive Flow (7 steps) as core; shortened Folders, Claude Desktop, Troubleshooting. **README.md**: Removed “Drop PDF/HTML/CSV…” as primary flow, long How It Works with multiple formats, full Project Structure, Supported File Types table, long Technical Notes (PDF/CSV/etc.); reframed around interactive CLI for SEC EDGAR HTML → PageIndex-style RAG; How It Works = Fetch (interactive fetch-sec) → Index (“Index now?” or ingest) → Search; Usage = primary = fetch-sec, supporting = ingest/manage-docs/rag-server; single Technical Note on SEC HTML path. Both docs now center on: **interactive CLI → SEC EDGAR HTML → PageIndex-style RAG**.

- **BeautifulSoup XMLParsedAsHTMLWarning suppressed** — SEC EDGAR sometimes serves XHTML/XML; we parse as HTML for heading and text extraction (h1, p, div, etc.). BeautifulSoup warns when using the HTML parser on XML-like content. In all three call sites we now filter that warning: `src/parsers/html_to_markdown.py`, `src/parsers/html_parser.py`, `src/fetch_sec.py`. Each imports `XMLParsedAsHTMLWarning` from bs4 and runs `warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)` so the console stays quiet. We do not switch to an XML parser; our logic is HTML-tag–oriented and the HTML parser works for SEC content.

- **LLM default: Ollama (Mistral) instead of OpenRouter (Gemini)** — `src/llm.py` now supports two backends selected by `config.json`: `llm_backend` `"ollama"` (default) or `"openrouter"`. When `ollama`, the client uses `ollama_base_url` (default `http://localhost:11434/v1`) and `ollama_model` (default `mistral:7b`); no API key required. When `openrouter`, behavior is unchanged (OpenRouter base URL, `openrouter_api_key`, `model` e.g. `google/gemini-2.5-flash`). All three entrypoints (`llm_call`, `llm_call_with_finish_reason`, `llm_call_async`) use the resolved backend; explicit `model` argument still overrides per-call when provided. **README** and **config.example.json** updated: default is Mistral via Ollama; OpenRouter is optional.

- **A/B test script: Mistral vs Gemini** — `scripts/ab_test_mistral_vs_gemini.py` compares Mistral 7B (Ollama) vs Gemini 2.5 Flash (OpenRouter) for (1) latency: node summary 3× and doc description 2× per backend; (2) quality: side-by-side full section summary. Uses same prompts as PageIndex (`generate_node_summary`, `generate_doc_description`). Run: `uv run python scripts/ab_test_mistral_vs_gemini.py`. Requires Ollama + `ollama pull mistral:7b` for Mistral; `config.json` with `openrouter_api_key` for Gemini.

- **HTML→Markdown: Form 3/4 fallback** — Form 3 and Form 4 filings are table-heavy and often have no h1–h6 or "Item 1." style headings; they use "Table I", "Table II", "Section 1", etc. The SEC heading fallback in `src/parsers/html_to_markdown.py` now also matches `Table\s+[IVXLCDM]+`, `Table\s+\d+`, `Section\s+[IVXLCDM]+`, and `Section\s+\d+`, so Form 3/4 get ## headings and nonzero nodes instead of 0 nodes. Test: `test_sec_fallback_form4_table_sections()`.

- **fetch-sec: unique filenames for same form+date** — When multiple filings share the same form and date (e.g. two Form 4s on the same day), they used to write to the same path (e.g. `CAT_4_20260209.html`). The first index would move the file to processed, so the second hit errno 2 (file not found). Filenames now include the accession number (e.g. `CAT_4_20260209_000123456722000001.html`) so each filing has a unique path. In `src/fetch_sec.py`: `_download_one` uses `accession_to_path_part(acc)` in the filename.

- **manage-docs UI simplified** — Replaced Rich table (No., Doc ID, Name, Nodes, Description) with a **numbered list**: each line is `  No.  Filing name` so the selection number is always visible and not affected by table column collapse. Removed Description column (not useful); removed duplicate Doc ID/Name columns in favor of a single filing name column. Selection prompt: “Enter number(s) to delete (e.g. 1, 3-5, or all), or 'none' to cancel”. In `src/manage_docs.py`: dropped Table import; list is printed with `console.print(f"  [cyan]{i:>3}[/cyan]  {name}")` per doc.

- **Doc description generation disabled** — Indexing no longer generates a document description (one-sentence summary). `if_add_doc_description` is set to `"no"` in `src/indexer.py` for all paths (MD, HTML, other) and in `src/pageindex/config.yaml` for the PDF path. Reduces indexing time, avoids possible file/errno issues (e.g. errno 2) during that step, and manage-docs no longer shows descriptions. Existing code that reads `doc_description` (tree_store, server, tree_search) still works via `.get("doc_description", "")`.

### Files touched (agent reference)

| Action | Path |
|--------|------|
| Add | `src/parsers/html_to_markdown.py` |
| Add | `tests/test_html_to_markdown.py` |
| Add | `src/fetch_sec.py` (later rewritten for interactive flow) |
| Modify | `src/indexer.py` (HTML branch, import `html_to_markdown`) |
| Modify | `src/parsers/__init__.py` (export `html_to_markdown`) |
| Modify | `pyproject.toml` (script `fetch-sec`) |
| Modify | `src/fetch_sec.py` (interactive: identity, ticker, form filter, Rich table, selection, index now?) |
| Modify | `config.example.json` (add `sec_user_agent`) |
| Modify | `CLI_CHEATSHEET.md` (workflow, commands, SEC section — interactive steps; later streamlined: primary = fetch-sec, removed Supported File Types, Quick Python Access, drop-files-first) |
| Modify | `README.md` (Technical Notes, Supported File Types, SEC interactive, config; later streamlined: interactive CLI + SEC HTML → PageIndex RAG only, removed multi-format/Project Structure/long Technical Notes) |
| Modify | `src/cli.py` (help text: fetch-sec interactive; workflow step 1 = Ollama default, optional OpenRouter) |
| Modify | `src/parsers/html_to_markdown.py` (suppress XMLParsedAsHTMLWarning; Form 3/4: Table I, Table II, Section 1 fallback) |
| Modify | `tests/test_html_to_markdown.py` (add test_sec_fallback_form4_table_sections) |
| Modify | `src/parsers/html_parser.py` (suppress XMLParsedAsHTMLWarning) |
| Modify | `src/fetch_sec.py` (suppress XMLParsedAsHTMLWarning) |
| Modify | `src/llm.py` (backend selection: ollama default, openrouter optional; _get_backend, _get_ollama_*, _resolve_model_and_client) |
| Modify | `config.example.json` (llm_backend, ollama_base_url, ollama_model) |
| Add | `scripts/ab_test_mistral_vs_gemini.py` |
| Modify | `src/manage_docs.py` (numbered list only: No. + filing name; no table, no Description/Doc ID columns) |
| Modify | `src/fetch_sec.py` (unique filenames: include accession so same form+date don't overwrite) |
| Modify | `src/indexer.py` (if_add_doc_description="no" for MD, HTML, other) |
| Modify | `src/pageindex/config.yaml` (if_add_doc_description: "no") |

### Backward compatibility

- Any code that calls `parse_file()` or `parse_html()` directly still works; only the indexer’s routing for `.html`/`.htm` changed. PDF path and `.md`/`.markdown` path unchanged.

---

## [0.1.2] — 2025-02-10

### Fixed
- **`add_page_number_to_toc` type safety** — LLM sometimes returns a single dict instead of a list of dicts. Now wraps a single dict in a list and filters out non-dict items before processing. Also only deletes the `'start'` key when the item is actually a dict.
- **`process_none_page_numbers` empty/malformed result guard** — Only accesses `result[0]` when `result` is non-empty and the first element is a dict with a `physical_index` key. Prevents `IndexError` and `TypeError` on unexpected LLM output shapes.

### Verified
- **CAT 2025 10K PDF** — Full end-to-end indexing now completes successfully. 58 root nodes extracted. Document is searchable (e.g. `search_trees('revenue')` returns 10 relevant results including "CONSOLIDATED SALES AND REVENUES", "Sales and Revenues by Segment").

---

## [0.1.1] — 2025-02-10

### Fixed
- **PDF TOC JSON parsing crash** — `toc_transformer` in `page_index.py` called `json.loads()` directly on accumulated LLM output. When Gemini returned valid JSON followed by trailing text (e.g. explanations, duplicate objects), this raised `JSONDecodeError: Extra data`. Now uses `extract_json()` which handles this gracefully.
- **`extract_json` robustness** — Added `_try_parse_json()` helper in `utils.py` that catches "Extra data" errors and uses `json.JSONDecoder.raw_decode()` to extract the first valid JSON object/array, ignoring trailing garbage.
- **`toc_transformer` continuation safety** — Added max 5 continuation attempts to prevent infinite loops. Also handles cases where LLM returns the TOC array directly without the `{table_of_contents: [...]}` wrapper object.

### Changed
- **`max_tokens` default raised to 16384** — Was 8192. Large PDF TOC extraction (e.g. 200-page 10K filings) can produce 4000–8000 token JSON responses. 8192 risked truncation. OpenRouter pre-checks affordability against this ceiling but only charges actual tokens generated, so the higher default costs nothing extra for short responses.

### Added (by QA agent)
- **`max_tokens` config support** — `_get_max_tokens()` in `llm.py` reads from `config.json` (key: `max_tokens`, default: 16384). Applied to all three LLM functions (`llm_call`, `llm_call_with_finish_reason`, `llm_call_async`).
- **`.gitignore`** — Project-level gitignore covering `config.json` (API key), `__pycache__/`, `.venv/`, `logs/`, data files.
- **`config.example.json`** updated with `max_tokens` field.

---

## [0.1.0] — 2025-02-10

Initial implementation. Built the full project from scratch by combining PageIndex and investment-rag.

### Project scaffold
- `pyproject.toml` with hatchling build, 13 dependencies, 4 entry points (`rag-server`, `ingest`, `manage-docs`, `pageindex-rag`)
- `[tool.hatch.build.targets.wheel] packages = ["src"]` — required because source lives in `src/` not a package matching the project name
- `config.example.json` — template with `openrouter_api_key` and `model` fields
- Directory structure: `data/drop/`, `data/processed/`, `data/indexes/`

### Parsers (copied verbatim from investment-rag)
- `src/parsers/__init__.py` — router with `PARSERS` dict and `parse_file()`
- `src/parsers/pdf_parser.py` — pypdf-based PDF text extraction
- `src/parsers/html_parser.py` — BeautifulSoup HTML parsing
- `src/parsers/csv_parser.py` — pandas CSV/Excel to natural language
- `src/parsers/text_parser.py` — plain text with ticker/quarter detection from filename

### LLM client (new, replaces PageIndex's OpenAI direct calls)
- `src/llm.py` — Three functions matching PageIndex's original signatures:
  - `llm_call()` replaces `ChatGPT_API()`
  - `llm_call_with_finish_reason()` replaces `ChatGPT_API_with_finish_reason()`
  - `llm_call_async()` replaces `ChatGPT_API_async()`
- Uses `openai.OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")`
- API key from `config.json` with `OPENROUTER_API_KEY` env var fallback
- Model from `config.json`, default `google/gemini-2.5-flash`
- `finish_reason` normalization: maps both `"length"` and `"max_tokens"` to `"max_output_reached"` (Gemini may return either)

### PageIndex core (adapted from PageIndex repo)

**`src/pageindex/utils.py`** — Key changes from original:
- **Removed** the 3 LLM functions (`ChatGPT_API`, `ChatGPT_API_with_finish_reason`, `ChatGPT_API_async`) and `CHATGPT_API_KEY` global
- **Added** aliased imports: `from ..llm import llm_call as ChatGPT_API, ...` — so all existing call sites work unchanged
- **`count_tokens()`** — Changed to always use `gpt-4o` encoding via tiktoken (approximate but fine for size thresholds). Original passed model name directly which would fail for Gemini model IDs.
- **`get_page_tokens()`** — Same tiktoken fix, hardcoded to `gpt-4o` encoding
- All other utility functions (tree helpers, JSON extraction, PDF helpers, summary generators, `ConfigLoader`, `JsonLogger`) copied as-is

**`src/pageindex/config.yaml`** — Changes from original:
- `model`: `"gpt-4o-2024-11-20"` → `"google/gemini-2.5-flash"`
- `if_add_node_text`: `"no"` → `"yes"` (needed for search to have text content)
- `if_add_doc_description`: `"no"` → `"yes"` (useful for document listing)

**`src/pageindex/page_index.py`** — Changes from original:
- **Import fix**: `from .utils import *` (relative import for new package structure)
- **`generate_toc_continue()`** line 499: changed `model="gpt-4o-2024-11-20"` default → `model=None` (picks up config default)
- **`single_toc_item_index_fixer()`** line 732: same hardcoded model default → `model=None`
- All LLM calls work via the aliased imports in `utils.py` — no other changes needed

**`src/pageindex/page_index_md.py`** — Changes from original:
- **Import fix**: removed `try/except` import fallback, uses `from .utils import *` directly
- **Removed** `if __name__ == "__main__"` test block (not needed in library context)

**`src/pageindex/__init__.py`** — New file exposing `page_index`, `page_index_main`, `md_to_tree`

### New modules

**`src/tree_store.py`** — JSON file persistence in `data/indexes/`:
- Doc IDs: sanitized filename stem + 8-char MD5 hash (e.g. `my_document_a1b2c3d4`)
- Functions: `save_tree()`, `load_tree()`, `list_trees()`, `delete_tree()`, `load_all_trees()`
- Each file stores: `{doc_id, source_file, metadata, tree}`

**`src/indexer.py`** — Orchestrator routing by file type:
- `.pdf` → `page_index()` (full PageIndex pipeline with TOC detection)
- `.md`/`.markdown` → `md_to_tree()` (heading-based tree)
- Everything else → parse via investment-rag parsers, wrap as markdown with `# filename` heading, feed to `md_to_tree()`
- Temp file cleanup for the markdown-wrapping path

**`src/tree_search.py`** — Keyword search across tree nodes:
- `search_trees(query, max_results, doc_id)` — flattens all trees, scores by keyword match: title (5x weight), summary (3x), text (1x)
- `get_document_overview(doc_id)` — TOC-style indented listing with node IDs and summary snippets
- Returns structured results with doc_name, node_path, title, summary, text_snippet, score

### MCP server (adapted from investment-rag pattern)
- `src/server.py` — 6 tools via `FastMCP("pageindex-rag")` with stdio transport:
  - `search_documents`, `get_document_section`, `get_document_overview`, `list_documents`, `ingest_drop_folder`, `remove_document`
- **`ThreadPoolExecutor`** for indexing — PageIndex's `page_index_main()` calls `asyncio.run()` internally, which conflicts with MCP's event loop. Wrapping in a thread pool avoids nested `asyncio.run()`.
- Removed all investment-rag-specific tools (SEC EDGAR, FMP transcripts, ChromaDB)

### CLI tools (adapted from investment-rag)

**`src/ingest.py`** — Changes from original:
- Removed ticker/collection prompts (not finance-specific)
- Replaced `chunker.chunk_document()` + `db.add_documents()` with `indexer.index_document()`
- Added file size column to Rich table
- Added `Confirm.ask()` before starting (original jumped straight to per-file prompts)
- Uses `ThreadPoolExecutor` for same asyncio reason as server
- Supported extensions include `.md`/`.markdown` (not in original PARSERS dict)

**`src/manage_docs.py`** — Changes from original:
- Removed collection chooser (no collections concept — all docs are trees)
- Calls `tree_store.list_trees()` / `delete_tree()` instead of `db.list_documents()` / `db.remove_by_source()`
- Shows doc_id, name, node count, description instead of source_file, ticker, chunks

**`src/cli.py`** — Simple help text (new, no equivalent in investment-rag)

### Documentation
- `README.md` — Setup, usage, architecture, MCP tools, file types, technical notes
- `QA_TEST_PLAN.md` — 22 tests across 9 phases with exact commands and pass/fail criteria
- `CLI_CHEATSHEET.md` — Quick reference for daily use

---

## Architecture Decisions

| Decision | Rationale |
|---|---|
| OpenRouter + OpenAI SDK | Same `.chat.completions.create()` API, just change `base_url`. No new dependencies. |
| tiktoken with gpt-4o encoding | Gemini doesn't have a tiktoken tokenizer. gpt-4o is close enough for size threshold checks (not billing). |
| JSON file storage (not SQLite/ChromaDB) | Vectorless design — trees are small, keyword search is fast, no DB to manage. |
| ThreadPoolExecutor for indexing | PageIndex uses `asyncio.run()` internally. MCP server and Rich CLI also use async. Thread isolation avoids nested event loop errors. |
| Single global `max_tokens` | OpenRouter charges actual tokens, not the ceiling. One config value is simpler than per-call tuning. 16384 covers worst case (large PDF TOC). |
| `extract_json` with `raw_decode` fallback | Gemini sometimes appends explanatory text after JSON. `raw_decode` grabs the first valid object and ignores the rest. |
