# CLI Cheatsheet — pageindex-rag

Interactive CLI for SEC EDGAR filings (HTML) → PageIndex-style tree RAG. No vector DB; trees are built from heading hierarchy and searched by keyword.

## First-Time Setup

```bash
cd "C:\Users\jxie0\pageindex-rag"
uv sync
cp config.example.json config.json
# Edit config.json: llm_backend (default "ollama"; use "openrouter" for Gemini), sec_user_agent (Your Name your.email@domain.com). For Ollama: run Ollama and e.g. ollama pull mistral:7b. For OpenRouter: set openrouter_api_key.
```

## Moving to a local folder (e.g. out of Dropbox)

1. Copy or move the whole `pageindex-rag` folder to the new location (e.g. `C:\Users\jxie0\pageindex-rag`).
2. (Optional) Delete the `.venv` folder in the new location, then run `uv sync` there to create a fresh venv.
3. Copy `config.json` into the new folder if it wasn’t included (or recreate from `config.example.json`).
4. In Claude Desktop MCP config (`%APPDATA%\Claude\claude_desktop_config.json`), set the server path to the new folder, e.g. `"args": ["--directory", "C:\\Users\\jxie0\\pageindex-rag", "run", "rag-server"]`, then restart Claude Desktop.

## Primary Workflow

```bash
uv run fetch-sec           # interactive: ticker → form filter → table → select filings → Index now?
uv run fetch-sec CAT       # same, ticker CAT provided
uv run manage-docs         # list or delete indexed docs
uv run rag-server          # MCP server for Claude Desktop (optional)
```

## Commands

| Command | What it does |
|---------|--------------|
| `uv run fetch-sec [TICKER]` | Interactive SEC fetch: User-Agent (if needed) → ticker → form filter → table of filings → select (e.g. 1, 1-5, all) → download HTML → "Index now?" (y = index + move to processed) |
| `uv run ingest` | Index any files already in `data/drop/` (e.g. if you said "n" to Index now?) |
| `uv run manage-docs` | Numbered list of filing names; delete by number (e.g. 1, 3-5, all) |
| `uv run rag-server` | Start MCP server (stdio) for Claude Desktop |
| `uv run pageindex-rag` | Show help |

## SEC Interactive Flow (fetch-sec)

1. **SEC User-Agent** — Prompted once if missing in `config.json`; saved as `sec_user_agent`.
2. **Ticker** — Enter symbol or pass: `uv run fetch-sec CAT`.
3. **Form filter** — e.g. `10-K,10-Q` or blank for all.
4. **Table** — Recent filings (#, Date, Type, Accession); pick by number.
5. **Select** — `1`, `1,3,5`, `1-5`, or `all`.
6. **Download** — Selected filings saved as HTML to `data/drop/`.
7. **Index now?** — `y` = index into tree and move to `data/processed/`; `n` = run `uv run ingest` later.

## Folders

- `data/drop/` — Fetched HTML lands here; ingest also picks up files here.
- `data/processed/` — Files moved here after indexing (when you answer "Index now?" or after ingest).
- `data/indexes/` — JSON tree files (one per document).

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

Then: "List my documents", "Search for X", "Show overview of doc Y", "Read section Z".

## Troubleshooting

| Issue | Fix |
|-------|-----|
| SEC rejects requests | Set `sec_user_agent` in config (real name and email). Run `uv run fetch-sec` once to be prompted. |
| No filings / ticker not found | Check ticker symbol; try form filter blank. |
| LLM errors when indexing | If using Ollama (default): ensure Ollama is running and e.g. `ollama pull mistral:7b`. If using OpenRouter: set `openrouter_api_key` and `llm_backend` to `openrouter`. |
| MCP not connecting | Fix path in Claude Desktop config, restart Claude. |
