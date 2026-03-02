# PageIndex RAG — Agent Guidance

Vectorless, structure-first RAG engine for SEC EDGAR filings and long documents.
You have access to a PageIndex RAG MCP server that lets you fetch, index, search, and analyze SEC filings for any publicly traded company — all through natural language.

---

## How to Think About This Tool

PageIndex RAG implements a **vectorless, reasoning-based RAG** approach inspired by the [PageIndex framework](https://pageindex.ai). Instead of relying on embeddings and vector similarity, it uses:

1. **Hierarchical tree structure** — Document sections organized like a table of contents
2. **LLM reasoning** — When keyword search is insufficient, the LLM navigates the tree to find relevant sections
3. **Raw text retrieval** — Answers always come from full section text, never from summaries

This approach achieved **98.7% accuracy on FinanceBench**, outperforming traditional vector-based RAG for financial document analysis.

### PageIndex Philosophy

```
┌─────────────────────────────────────────────────────────────┐
│  REASONING PHASE (uses summaries for navigation)            │
│  ├── LLM reads tree structure with section summaries        │
│  ├── Decides which node_ids are relevant                    │
│  └── Returns selected node_ids                              │
│                                                             │
│  RETRIEVAL PHASE (uses raw text for answers)                │
│  ├── Fetch full raw text for selected node_ids              │
│  └── Use raw text to answer the question                    │
└─────────────────────────────────────────────────────────────┘
```

**Key Principles:**
- **Summaries = Navigation aid only** — Help the LLM decide where to look
- **Keyword search = Fast baseline** — Instant retrieval for clear matches
- **LLM reasoning = Fallback** — When keywords are weak, LLM navigates the tree
- **Raw text = Always the answer source** — Never use summaries for final answers

**What you can do:**
- Fetch any SEC filing for any public company (10-K, 10-Q, 8-K, proxy statements, ownership forms, etc.)
- Search across filings by keyword with optional LLM reasoning fallback
- Read specific sections in full (always raw text)
- Compare filings year-over-year or across companies
- Ask the same question across multiple filings at once

**What the user experiences:** They ask a question in natural language ("What are Tesla's biggest risk factors?" or "Compare Caterpillar's revenue discussion in 2023 vs 2024") and you handle all the technical details.

---

## MCP Tools Reference

### Filing Lifecycle Tools

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `check_company_indexed(ticker)` | Check if filings exist for a company | **ALWAYS call first** before fetching. Avoids redundant downloads and SEC rate limits. |
| `fetch_company_filings(ticker, forms, max_filings, auto_index)` | Download + index filings from SEC EDGAR | Only when `check_company_indexed` shows the company is not indexed or is missing the filing type you need. Returns immediately — indexing runs in the background. |
| `check_indexing_status(batch_id)` | Monitor background indexing progress | **ALWAYS call after fetch_company_filings or ingest_drop_folder.** Poll until status shows "COMPLETE" before proceeding to analysis. |
| `ingest_drop_folder()` | Index files manually placed in `data/drop/` | Rarely needed — `fetch_company_filings` auto-indexes by default. Use only for manually dropped files. Runs in the background like fetch. |
| `remove_document(doc_id)` | Delete an indexed document | When the user asks to clean up, or when you need to re-index a filing. |

### Search and Retrieval Tools

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `search_with_citations(query, doc_id, max_results)` | **PageIndex reasoning-based search** with full source citations | Primary search tool. Uses keyword search first; invokes LLM reasoning when needed. Returns doc_id + node_id pairs. |
| `get_document_overview(doc_id)` | Table of contents for a document | First step after indexing — understand the filing's structure before drilling in. |
| `get_document_section(doc_id, node_id)` | **Full raw text** of a specific section | After search identifies a relevant section, read the **complete raw text** for analysis. Never returns summaries as answers. |

### Analysis Tools

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `batch_query(query, doc_ids)` | Same question across multiple documents | Tracking a topic across quarters, or comparing how multiple companies discuss the same issue. |

### Embedding Tools (Optional)

> **Note:** This implementation uses a **vectorless** approach by default (`semantic_search: false`). Embeddings are optional and disabled by default.

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `embed_documents(doc_ids)` | Generate semantic search embeddings | Only if you explicitly enable `semantic_search: true` in config. Not needed for the PageIndex reasoning-based approach. |

### Utility Tools

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `list_documents()` | List all indexed documents | When you need to see what's available, find doc_ids, or help the user choose a filing. Shows `[semantic]` tag for documents with embeddings. |

---

## PageIndex Retrieval Methodology

### How Search Works

The `search_with_citations()` tool implements the PageIndex two-stage retrieval:

**Stage 1: Keyword Search (Fast Baseline)**
- Scans all nodes for query term matches
- Scoring: Title match (5 pts) + Summary match (3 pts) + Text match (1 pt)
- If top score ≥ 3: Return keyword results directly (reasoning not needed)

**Stage 2: LLM Reasoning (When Keywords Weak)**
- Triggered when top keyword score < 3
- LLM receives condensed tree structure (titles + summaries)
- LLM selects most relevant node_ids based on query intent
- Returns reasoning-selected nodes + keyword fallback

### Why This Matters for Financial Documents

| Approach | Problem | PageIndex Solution |
|----------|---------|-------------------|
| Vector similarity | "climate risk" ≠ "environmental impact" | LLM reasoning understands semantic equivalence |
| Chunking | Loses section boundaries | Preserves hierarchical structure (Items, Parts) |
| Summary answers | Misses precise figures/legal text | Always returns raw text for verification |

### Retrieval Guarantees

- ✅ **No vector database required** — Pure tree navigation
- ✅ **Traceable sources** — Every result has doc_id + node_id
- ✅ **Raw text answers** — Summaries never used for final answers
- ✅ **Structure-aware** — Understands SEC filing organization (Items, Parts, Tables)

---

## Workflow Recipes

### Recipe 1: Analyze a Company's Filing (Most Common)

This is the default workflow for any question about a specific company's SEC filings.

```
Step 1: check_company_indexed(ticker)
        → If indexed: note the doc_ids, proceed to Step 4
        → If NOT indexed: proceed to Step 2

Step 2: fetch_company_filings(ticker, forms="10-K", max_filings=2)
        → Returns IMMEDIATELY with a batch_id
        → Indexing runs in the background (30-120 seconds per filing)

Step 3: check_indexing_status(batch_id)
        → Poll until status shows "COMPLETE"
        → Note the new doc_ids from the completed jobs
        → Tell the user "Indexing in progress..." while waiting

Step 4: get_document_overview(doc_id)
        → Understand the filing structure
        → Identify which sections are relevant

Step 5: search_with_citations(query, doc_id=doc_id)
        → Find the specific sections that answer the user's question
        → Note the node_ids from results

Step 6: get_document_section(doc_id, node_id)
        → Read the full text of relevant sections

Step 7: Synthesize and present findings
        → Cite sources using doc_id and node_id
        → Quote relevant passages
```

**IMPORTANT: The fetch → poll → analyze pattern avoids MCP timeouts.**
`fetch_company_filings` returns in ~1-3 seconds (download only).
Indexing happens in the background (5-30 seconds depending on document size
and summary settings). Poll `check_indexing_status` every 5 seconds until 
complete, then proceed with analysis.

**Indexing Speed:**
- `summary_token_threshold: 5000` → ~0.5-2s (minimal LLM summaries)
- `summary_token_threshold: 1000` → ~10-30s (more summaries for navigation)
- No embeddings generated (vectorless approach)

### Recipe 2: Year-over-Year Comparison

```
Step 1: check_company_indexed(ticker)
        → Ensure at least 2 years of the same filing type are indexed

Step 2: fetch_company_filings(ticker, forms="10-K", max_filings=3) if needed
        → Returns immediately, poll check_indexing_status() until complete

Step 3: For sections of interest, read both versions:
        get_document_section(doc_id_year1, node_id)
        get_document_section(doc_id_year2, node_id)

Step 4: Present side-by-side analysis with specific changes highlighted
```

### Recipe 3: Cross-Company Comparison

```
Step 1: For each company: check_company_indexed(ticker)
Step 2: Fetch missing filings (same form type for fair comparison)
Step 3: batch_query(query, doc_ids="doc1,doc2,doc3")
Step 4: Drill into top results per company with get_document_section
Step 5: Present comparative analysis with citations
```

### Recipe 4: Earnings / Quarterly Analysis

```
Step 1: check_company_indexed(ticker)
Step 2: fetch_company_filings(ticker, forms="10-Q,8-K", max_filings=3)
Step 3: get_document_overview for the 10-Q → find financial statements, MD&A
Step 4: search_with_citations("revenue earnings guidance", doc_id=...)
Step 5: Read key sections: MD&A, Financial Statements, Risk Factors
Step 6: Synthesize quarterly performance summary
```

### Recipe 5: Quick Topic Search Across All Filings

```
Step 1: search_with_citations(query)     # No doc_id = search everything
Step 2: Review results, identify most relevant filings
Step 3: Drill into specific sections as needed
```

---

## Decision Tree: Which Search Tool?

```
User's question
    │
    ├─ "What filings do we have?" ──────────────→ list_documents()
    │
    ├─ "What's in this filing?" ────────────────→ get_document_overview(doc_id)
    │
    ├─ Need to find relevant sections? ─────────→ search_with_citations()
    │
    ├─ Need full section text? ─────────────────→ get_document_section()
    │
    └─ Same question, multiple filings? ────────→ batch_query()
```

---

## SEC Filing Domain Knowledge

### Filing Types and What They Contain

| Form | What It Is | Key Sections to Look For |
|------|-----------|--------------------------|
| **10-K** | Annual report (most comprehensive) | Item 1: Business, Item 1A: Risk Factors, Item 7: MD&A, Item 8: Financial Statements |
| **10-Q** | Quarterly report | Part I Item 1: Financial Statements, Part I Item 2: MD&A, Part II Item 1A: Risk Factors |
| **8-K** | Current report (material events) | Item 2.02: Results of Operations, Item 5.02: Officer Changes, Item 9.01: Exhibits |
| **DEF 14A** | Proxy statement | Executive Compensation, Board of Directors, Shareholder Proposals |
| **S-1** | IPO registration | Risk Factors (extensive), Use of Proceeds, Business, Management |
| **Form 3/4** | Insider ownership/transactions | Derivative securities, non-derivative holdings |
| **Form 144** | Insider trading notice | Planned sales of restricted stock |
| **20-F** | Foreign company annual report | Similar to 10-K but for non-US issuers |

### Common Analysis Patterns

**Risk Factor Analysis (10-K Item 1A):**
- Search for "risk factors" to find the section
- Read the full section — it's often very long (use streaming)
- Compare year-over-year to find NEW risks added

**Management Discussion (10-K Item 7 / 10-Q Part I Item 2):**
- The most narrative section — management's own analysis
- Contains revenue drivers, cost discussion, liquidity analysis
- Best source for understanding business trends

**Financial Statements (10-K Item 8 / 10-Q Part I Item 1):**
- Income statement, balance sheet, cash flow
- Notes to financial statements contain critical details
- Search for specific line items by name

**Executive Compensation (DEF 14A):**
- Named executive officer compensation tables
- Performance metrics and incentive structure
- Peer group benchmarking

---

## Rules and Best Practices

### ALWAYS Do

- Call `check_company_indexed()` before `fetch_company_filings()` — this avoids redundant downloads and protects against SEC rate limits
- Use specific form filters (`forms="10-K"`) instead of fetching everything
- Keep `max_filings` small (2-5) — you can always fetch more later
- Cite sources with doc_id and node_id when presenting findings to the user
- Use `search_with_citations()` as the primary search tool
- Read the document overview before searching — it helps you target the right sections

### NEVER Do

- Fetch the same company repeatedly without checking first
- Request all filings with blank form filter (wastes time and hits rate limits)
- Ignore rate limit warnings — if rate limited, tell the user to wait 10 minutes
- Present filing data without citing the source document and section
- Assume a filing is indexed without checking

### Rate Limit Handling

SEC EDGAR allows max 10 requests/second. PageIndex uses ~6.6 req/s with jitter.

If you encounter a rate limit error:
1. Tell the user: "SEC rate limit reached. We need to wait about 10 minutes."
2. Suggest working with already-indexed filings in the meantime
3. Do NOT retry immediately

### Error Recovery

| Error | What to Do |
|-------|-----------|
| "Document not found" | Run `list_documents()` to see available doc_ids |
| "Ticker not found" | Verify the ticker symbol is correct (e.g., BRK-B not BRKB) |
| "No matching results" | Try broader search terms, or check `get_document_overview` for section names |
| "Rate limit exceeded" | Wait 10 minutes, use already-indexed documents |
| "No filings found" | The company may not have that filing type, try different `forms` |

---

## Citation Format

When presenting information from filings, always cite the source:

**Inline citation:**
> According to Caterpillar's 2024 10-K filing (doc: `CAT_10-K_20240216_d03268e6`, section: Item 1A Risk Factors, node: `0015`), the company identifies supply chain disruption as a key risk...

**End-of-response citation block:**
```
Sources:
- [1] CAT_10-K_20240216_d03268e6, node 0015: "Item 1A: Risk Factors"
- [2] CAT_10-K_20240216_d03268e6, node 0023: "Item 7: Management's Discussion and Analysis"
```

---

## Skills Available

This project includes analysis skills in the `skills/` directory. When the user's request matches a skill's trigger conditions, load and follow the skill's workflow:

| Skill | Trigger | Path |
|-------|---------|------|
| SEC Filing Analysis | Any request to analyze a company's SEC filings | `skills/sec-filing-analysis/SKILL.md` |
| Earnings Analysis | Post-earnings analysis, quarterly results | `skills/earnings-analysis/SKILL.md` |
| Risk Factor Comparison | Year-over-year risk analysis, risk changes | `skills/risk-factor-comparison/SKILL.md` |
| Company Overview | Company profile, multi-filing synthesis | `skills/company-overview/SKILL.md` |
| Competitive Analysis | Cross-company comparison | `skills/competitive-analysis/SKILL.md` |
| Filing Navigator | Navigating large/complex filings | `skills/filing-navigator/SKILL.md` |

## Commands Available

Slash commands are defined in the `commands/` directory:

| Command | Description |
|---------|-------------|
| `/analyze [ticker]` | Full company SEC filing analysis |
| `/risks [ticker]` | Risk factor deep dive |
| `/compare [ticker] [year1] [year2]` | Year-over-year filing comparison |
| `/earnings [ticker] [quarter]` | Quarterly earnings analysis from filings |
| `/overview [ticker]` | Company filing overview and TOC |
| `/search-filings [query]` | Search across all indexed documents |

---

## Semantic Search

PageIndex RAG supports hybrid search: keyword search runs first for speed and precision, and when keyword results are low-confidence (the query uses different wording than the filing text), semantic search via local embeddings kicks in as a fallback.

**How it works:**
- During indexing, each section's title + summary is embedded using a local Ollama model (`nomic-embed-text` by default)
- Embeddings are stored in the same JSON file as the tree structure (no separate vector DB)
- At search time, keyword search runs first. If the top keyword score is below threshold, semantic results are blended in
- This catches queries like "environmental impact" matching a section titled "Climate Change and Sustainability Risks"

**Requirements:**
- Ollama running with `nomic-embed-text` model: `ollama pull nomic-embed-text`
- `"semantic_search": true` in config.json (default)

**For documents indexed before semantic search was enabled:**
- Run `embed_documents("all")` to backfill embeddings
- Or `embed_documents("doc_id1,doc_id2")` for specific documents

**To disable:** Set `"semantic_search": false` in config.json. Keyword search continues to work normally.

---

## Project Structure

- `src/server.py` — MCP server (FastMCP, stdio transport)
- `src/indexer.py` — Document parsing orchestrator
- `src/pageindex/` — Core engine (PDF tree builder, markdown tree builder)
- `src/parsers/` — File parsers (HTML, PDF, CSV, text); HTML parser is SEC-aware
- `src/tree_search.py` — Keyword search with weighted scoring
- `src/tree_store.py` — JSON single-file-per-document storage
- `src/fetch_sec.py`, `src/sec_fetcher.py` — SEC EDGAR downloader
- `src/llm.py` — LLM backend (Ollama via OpenAI-compatible API)
- `data/drop/` — Drop folder for files to index
- `data/indexes/` — Stored JSON tree indexes
- `config.json` — Runtime config (Ollama model, SEC user agent, embedding settings)

## Running

- `uv run rag-server` — Start MCP server
- `uv run fetch-sec` — Interactive SEC filing downloader
- `uv run ingest` — Index files from drop folder
- `uv run manage-docs` — List/delete indexed documents
