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

1. Build one canonical tree per filing.
2. Search and browse over indexed nodes.
3. Read exact sections on demand.
4. Compose answers from grounded node text.

---

## Quickstart

```bash
uv sync
cp config.example.json config.json
```

Set in `config.json`:

- `llm_backend`: `"ollama"` (default) or `"openrouter"`
- `ollama_model`: default `mistral:7b`
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

## MCP integration (Claude Desktop)

Configure Claude Desktop MCP with:

```json
{
  "mcpServers": {
    "pageindex-rag": {
      "command": "uv",
      "args": ["--directory", "/path/to/page-index-rag", "run", "rag-server"]
    }
  }
}
```

Then use tools such as:

- `list_documents`
- `search_documents`
- `get_document_overview`
- `get_document_section`
- `ingest_drop_folder`
- `remove_document`

---

## Repository layout

```text
src/pageindex/          # PageIndex engine primitives
src/fetch_sec.py        # FidoSEC-style SEC fetch flow
src/ingest.py           # indexing pipeline entrypoint
src/server.py           # MCP server
src/manage_docs.py      # document management CLI
data/drop/              # raw files waiting for ingest
data/processed/         # processed HTML/markdown assets
data/indexes/           # built PageIndex outputs
```

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
- Expand parser reliability across filing variants (HTML/XHTML edge cases).

If you are building production or research workflows around filings, policy docs, or contracts, PageIndex is designed to be a clear and inspectable retrieval substrate.
