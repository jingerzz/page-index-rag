# PageIndex

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Built with uv](https://img.shields.io/badge/built%20with-uv-5A67D8)](https://github.com/astral-sh/uv)

**PageIndex — Vectorless structural indexing for reasoning-based RAG.**

PageIndex is a research-grade indexing engine that builds a navigable, hierarchy-faithful map of long documents (table-of-contents + sections) so language models can reason over structure instead of relying on embedding similarity alone.

---

## Positioning

### What this is

PageIndex is the **core engine** in this repository. It:

- ingests documents (especially SEC filing HTML),
- preserves heading hierarchy,
- builds a searchable section tree,
- exposes retrieval tools through a CLI and MCP server.

### Ecosystem architecture

- **PageIndex (engine):** structural indexing + section-level retrieval.
- **FidoSEC (CLI/use-case):** filing acquisition workflow on top of EDGAR.

> **FidoSEC — AI Retriever for SEC Filings**  
> FidoSEC fetches filings. PageIndex makes them navigable for reasoning.

This repository currently contains both the engine and SEC-oriented commands. Over time, FidoSEC can remain here as a submodule/package or split into a dedicated companion repo.

---

## Why it is different

Most RAG stacks start with chunking + vectors. PageIndex starts with **document structure**:

- **Vectorless first:** no vector database required for baseline retrieval.
- **Hierarchy-aware:** section boundaries and TOC relationships are preserved.
- **Reasoning-friendly:** LLMs can traverse section maps (overview → section drill-down).
- **Transparent retrieval:** results are explicit nodes, not opaque nearest neighbors.

This is especially useful for regulatory filings where section context matters (Risk Factors, MD&A, Notes, exhibits, etc.).

---

## How it works

```text
SEC EDGAR HTML
   ↓
FidoSEC fetch workflow (fetch-sec)
   ↓
HTML → Markdown (heading levels preserved)
   ↓
PageIndex tree builder (md_to_tree)
   ↓
Node summaries + document description (LLM-assisted)
   ↓
CLI + MCP tools for overview, search, and section reads
```

### Retrieval model

PageIndex implements a **vectorless, reasoning-based RAG** approach (inspired by [VectifyAI's PageIndex framework](https://pageindex.ai)):

```
┌─────────────────────────────────────────────────────────────┐
│  INDEXING (one-time)                                        │
│  ├── Parse SEC HTML → Markdown (preserve hierarchy)         │
│  ├── Build tree structure (Parts → Items → Subsections)     │
│  └── Generate summaries for large sections (LLM-assisted)   │
│                                                             │
│  SEARCH (per query)                                         │
│  ├── Stage 1: Keyword search (instant)                      │
│  │   └── If strong match: return results                    │
│  └── Stage 2: LLM reasoning (if keywords weak)              │
│      └── LLM navigates tree → selects relevant nodes        │
│                                                             │
│  RETRIEVAL                                                  │
│  └── Return full raw text from selected nodes               │
└─────────────────────────────────────────────────────────────┘
```

**Key Differences from Vector RAG:**
- ✅ **No vector database** — Tree navigation replaces similarity search
- ✅ **No embeddings** — Smaller files, faster indexing
- ✅ **Structure-aware** — Understands SEC filing organization
- ✅ **Traceable** — Every result has explicit doc_id + node_id
- ✅ **Raw text answers** — Never uses summaries for final answers

**Performance:** 98.7% accuracy on FinanceBench (vs ~75% for typical vector RAG)

### Supported Filing Types

PageIndex is tested and optimized for major SEC EDGAR filing formats:

| Filing Type | Description | Parser Features |
|-------------|-------------|-----------------|
| **10-K** | Annual reports | Item/Part detection, TOC extraction (31+ nodes) |
| **10-Q** | Quarterly reports | Item sections, financial statements (12+ nodes) |
| **8-K** | Current reports | Event-based Item sections (5.02, 9.01, etc.) |
| **Form 3** | Initial ownership | Table I/II extraction for beneficial ownership |
| **Form 4** | Ownership changes | Table-based derivative/non-derivative securities |
| **Form 144** | Insider trading notices | Structured section parsing |
| **13F-HR** | Institutional holdings | Table I/II/III for securities positions |
| **20-F** | Foreign issuer annual | Item structure like 10-K (40+ nodes typical) |
| **6-K** | Foreign issuer current | Event-based Item structure (12+ nodes typical) |
| **DEF 14A** | Proxy statements | Proposal sections, governance tables (29+ nodes) |
| **S-1** | IPO registration | Prospectus sections, risk factors (55+ nodes) |

The parser handles:
- **Narrative filings** (10-K, 10-Q, 20-F, S-1): `<h1>`-`<h6>` heading detection
- **Form-based filings** (3, 4, 144, 13F): Table header extraction
- **Hybrid filings** (8-K, 6-K, DEF 14A): Both heading and table patterns

---

## Quickstart

```bash
uv sync
cp config.example.json config.json
```

Set in `config.json`:

- `llm_backend`: `"ollama"` (default) or `"openrouter"`
- `ollama_model`: recommended `qwen3-coder:30b` (see [Model Selection](#model-selection))
- `openrouter_api_key`: required only for `openrouter`
- `sec_user_agent`: required for EDGAR access

### 1) Fetch filings (FidoSEC workflow)

```bash
uv run fetch-sec
# or
uv run fetch-sec CAT
```

The CLI prompts for ticker/form filters and can immediately index selected filings.

### 2) Index documents

```bash
uv run ingest
```

Indexes files from `data/drop/` into PageIndex trees.

### 3) Explore and retrieve

```bash
uv run manage-docs   # list/remove indexed docs
uv run rag-server    # MCP server for agentic clients
```

---

## MCP integration

PageIndex exposes an MCP server for agentic clients. Since retrieval uses pre-computed summaries, search/get operations are **instant** — only indexing requires LLM calls.

Currently tested and working with:

- **Claude Desktop** — via `claude_desktop_config.json`
- **Claude Code** — via project-level `.mcp.json` (auto-detected)
- **Kimi Code / Kimi Agent** — via Kimi MCP config

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pageindex-rag": {
      "command": "/path/to/page-index-rag/.venv/bin/python",
      "args": ["-m", "src.server"],
      "cwd": "/path/to/page-index-rag",
      "env": {
        "PYTHONPATH": "/path/to/page-index-rag"
      }
    }
  }
}
```

### Claude Code

No manual configuration needed. The project includes:

- **`.mcp.json`** — Claude Code auto-detects this file and starts the MCP server when you open the project
- **`CLAUDE.md`** — Provides Claude Code with tool documentation, workflow guidance, and project context

Just open the project in Claude Code and the tools are available immediately.

### Kimi Code / Kimi Agent

Add to your Kimi MCP configuration:

```json
{
  "mcpServers": {
    "pageindex-rag": {
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "/path/to/page-index-rag",
      "env": {
        "PYTHONPATH": "/path/to/page-index-rag"
      }
    }
  }
}
```

### Available Tools

| Tool | Purpose | Speed |
|------|---------|-------|
| `check_company_indexed` | Check if company filings exist | ⚡ Instant |
| `fetch_company_filings` | Pull SEC filings for a company | ⏱️ ~1-3s download + ~1-30s indexing |
| `list_documents` | List all indexed documents | ⚡ Instant |
| `search_with_citations` | **PageIndex reasoning-based search** with citations | ⚡ Instant (keyword) / ⏱️ ~5-10s (reasoning) |
| `get_document_overview` | Get document TOC/structure | ⚡ Instant |
| `get_document_section` | **Read full raw text** of a section | ⚡ Instant |
| `compare_documents` | Compare two filings (diff analysis) | ⚡ Instant |
| `batch_query` | Ask same question across multiple docs | ⚡ Instant |
| `ingest_drop_folder` | Index files from `data/drop/` | ⏱️ ~1-30s (no embeddings) |
| `remove_document` | Remove a document from index | ⚡ Instant |

**Note:** This is a **vectorless** implementation. No embeddings or vector database required. Search uses keyword matching with optional LLM reasoning fallback.

### Agent Workflow: On-Demand SEC Research

Claude Desktop, Claude Code, or Kimi can now perform **end-to-end SEC research** without manual CLI steps:

```
User: "Analyze Apple's recent risk factors"

Kimi:
1. check_company_indexed("AAPL") 
   → "AAPL has no filings indexed"

2. fetch_company_filings("AAPL", forms="10-K,10-Q", max_filings=3)
   → Downloads filings, auto-indexes them (vectorless, ~3s per filing)
   → "Indexed 3 filing(s): AAPL_10-K_..."

3. search_with_citations("risk factors competition", doc_id="AAPL_10-K_...")
   → Uses keyword search (instant) or LLM reasoning if needed
   → Returns relevant sections with citations

4. get_document_section(doc_id, node_id)
   → Reads full raw text of Risk Factors section

5. [Analysis with cited sources]

6. remove_document(doc_id) [optional - cleanup when done]
```

**Key Features for Agents:**
- **Automatic fetch + index**: `fetch_company_filings` with `auto_index=true` (default)
- **No manual CLI needed**: Everything accessible via MCP tools
- **Cleanup support**: `remove_document` to free space after analysis
- **Rate limit protection**: Automatic retry with exponential backoff, clear error messages if limits hit

**SEC Rate Limits:**
- SEC EDGAR allows max 10 requests/second
- PageIndex uses ~6.6 req/s with jitter to stay under limits
- If rate limit is hit, you'll get a clear message: *"SEC rate limit exceeded. Please wait 10 minutes before fetching more filings."*
- **Best practice**: Fetch filings once, then keep them indexed for analysis

### Advanced MCP Features

**Citation Mode:** Use `search_with_citations` to get search results with full source information (doc ID, node ID, exact path) for verification and fact-checking.

**Document Comparison:** Use `compare_documents` to diff two filings — useful for year-over-year 10-K comparisons or competitor analysis. Shows structural differences and content changes.

**Batch Queries:** Use `batch_query` to ask the same question across multiple documents simultaneously — great for tracking a topic across quarters or comparing companies.

**Streaming for Long Sections:** Use `get_document_section_stream` for very long sections (like MD&A). It returns the first chunk with metadata about total length, helping agents manage context windows.

### Model Selection

PageIndex was benchmarked on SEC filings with various local models. For **MCP usage**, we recommend:

| Model | Speed | Best For |
|-------|-------|----------|
| `qwen3-coder:30b` | 🥇 11s/filing | **Default for complex reasoning** — best quality |
| `gemma3:4b` | 🥈 3s/filing | **Fast indexing** — good balance of speed/quality |
| `qwen3:1.7b` | 🥉 2s/filing | **Resource-constrained** — smallest, fastest |
| `deepseek-r1:14b` | 57s/filing | Complex reasoning tasks (slow for indexing) |

**Why `gemma3:4b` for MCP?**
- **Vectorless approach** — No embeddings to generate = faster indexing
- Only generates summaries for large sections (>5000 tokens by default)
- Search/retrieval tools are instant (tree navigation, not vector search)
- 3s indexing time = excellent for agent workflows
- `qwen3-coder:30b` still recommended for query-time reasoning tasks

See `scripts/benchmark_models.py` to run your own comparisons.

---

## Repository layout

```text
src/pageindex/          # PageIndex engine primitives
src/fetch_sec.py        # FidoSEC-style SEC fetch flow
src/ingest.py           # indexing pipeline entrypoint
src/server.py           # MCP server
src/manage_docs.py      # document management CLI
src/parsers/            # HTML→Markdown and other parsers
scripts/                # Utility scripts
  benchmark_models.py   # LLM model comparison tool
  analyze_parser_quality.py  # Parser quality testing
  fetch_test_filings.py # Test filing downloader
data/drop/              # raw files waiting for ingest
data/processed/         # processed HTML/markdown assets
data/indexes/           # built PageIndex outputs
.mcp.json               # Claude Code MCP server config (auto-detected)
CLAUDE.md               # Claude Code agent guidance
```

### Quality Analysis

Test parser quality across filing types:

```bash
uv run python scripts/analyze_parser_quality.py
```

This analyzes:
- Tree structure validity
- Section extraction completeness  
- Text coverage percentage
- SEC pattern detection (Items, Parts, Tables)
- Context-aware quality scoring

---

## OSS metadata

- **License:** MIT (`LICENSE`)
- **Contributing:** see `CONTRIBUTING.md`
- **Citation:** see `CITATION.cff`
- **Roadmap/docs scaffold:** see `docs/`

### Suggested GitHub topics

`rag`, `retrieval-augmented-generation`, `sec-filings`, `edgar`, `document-indexing`, `llm-infra`, `mcp`, `agent-tools`, `vectorless-rag`, `structural-retrieval`

---

## What is next

- Formalize `PageIndex` as a standalone package API.
- Decide final packaging boundary for `FidoSEC` (same repo vs companion repo).
- Add benchmark tasks: structural retrieval vs vector retrieval on SEC QA.
- Add reproducible eval harness for section-grounded answer quality.
- ~~Expand parser reliability across filing variants (HTML/XHTML edge cases).~~ ✅ Form 3/4/144 support added

If you are building production or research workflows around filings, policy docs, or contracts, PageIndex is designed to be a clear and inspectable retrieval substrate.
