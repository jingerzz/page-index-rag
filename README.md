# pageindex-rag

Interactive CLI for **SEC EDGAR filings (HTML)** → **PageIndex-style RAG**: hierarchy-faithful trees, keyword search, no vector database. Default LLM is **Mistral 7B via Ollama** for node summaries and doc descriptions; you can switch to Gemini 2.5 Flash (OpenRouter) in config. Tree structure is built from headings (no LLM for structure on HTML).

## How It Works

1. **Fetch** — Run `uv run fetch-sec` (or `uv run fetch-sec CAT`). Interactive prompts: SEC User-Agent (once) → ticker → form filter (e.g. 10-K,10-Q) → table of recent filings → select which to download (e.g. `1`, `1-5`, `all`). Selected filings are downloaded as **HTML only** to `data/drop/`.
2. **Index** — When asked “Index these into the tree now?”, say **y** to build the tree (HTML → hierarchy-faithful Markdown → PageIndex `md_to_tree()`) and move files to `data/processed/`. Or say **n** and run `uv run ingest` later.
3. **Search** — Use `uv run manage-docs` to list indexed filings (numbered list; delete by number) or connect Claude Desktop to the MCP server and search/browse sections by keyword.

SEC HTML is converted to Markdown that preserves heading levels (h1→#, h2→##, etc.), so the tree is built from structure with **no LLM calls for TOC/structure** — unlike the PDF path.

## Setup

```bash
uv sync
cp config.example.json config.json
```

Edit `config.json`:

- **llm_backend** — `"ollama"` (default) or `"openrouter"`. With `ollama`, run Ollama and e.g. `ollama pull mistral:7b`.
- **ollama_model** — Model name when using Ollama (default `mistral:7b`).
- **openrouter_api_key** — Only needed when `llm_backend` is `openrouter`. From https://openrouter.ai/keys.
- **sec_user_agent** — Your real name and email, e.g. `Your Name (your.email@domain.com)`. Required for SEC EDGAR; you’ll be prompted once by `fetch-sec` if missing.

## Usage

**Primary: interactive SEC fetch and index**

```bash
uv run fetch-sec           # prompts: ticker → form filter → table → select → Index now?
uv run fetch-sec CAT       # ticker CAT, then form filter, table, selection
```

**Supporting commands**

```bash
uv run ingest              # index any files in data/drop/ (e.g. if you skipped "Index now?")
uv run manage-docs         # numbered list of filings; delete by number
uv run rag-server          # start MCP server for Claude Desktop
uv run pageindex-rag       # show help
```

See **CLI_CHEATSHEET.md** for the full interactive flow and troubleshooting.

## Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pageindex-rag": {
      "command": "uv",
      "args": ["--directory", "C:/Users/jxie0/pageindex-rag", "run", "rag-server"]
    }
  }
}
```

MCP tools: `list_documents`, `search_documents`, `get_document_overview`, `get_document_section`, `ingest_drop_folder`, `remove_document`.

## Technical Note

- **SEC HTML path:** HTML → `html_to_markdown()` (h1–h6 → #–######, document order preserved) → temp Markdown file → `md_to_tree()`. Tree structure comes from headings only; LLM is used only for per-node summaries and doc description. Best results with EDGAR primary HTML (what `fetch-sec` downloads). SEC sometimes serves XHTML/XML; we parse as HTML and suppress BeautifulSoup’s `XMLParsedAsHTMLWarning` so the console stays quiet.
