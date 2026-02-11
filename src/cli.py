"""Simple help text listing available commands."""


def main():
    print("""
pageindex-rag — Tree-based, vectorless document RAG
====================================================

Available commands:

  uv run fetch-sec     Interactive SEC fetch: ticker, form filter, table, select filings, then index?
  uv run ingest        Drop files in data/drop/ and index them interactively
  uv run manage-docs   List and delete indexed documents
  uv run rag-server    Start the MCP server (stdio transport for Claude Desktop)

Workflow:
  1. Copy config.example.json to config.json. Default LLM is Ollama (run Ollama, e.g. ollama pull mistral:7b). For Gemini set llm_backend to openrouter and add openrouter_api_key.
  2. For SEC filings: run 'uv run fetch-sec' or 'uv run fetch-sec TICKER' (interactive; HTML only). Other files: drop into data/drop/
  3. Run 'uv run ingest' to build tree indexes
  4. Run 'uv run rag-server' or configure Claude Desktop to use the MCP server

Claude Desktop config (claude_desktop_config.json):
  {
    "mcpServers": {
      "pageindex-rag": {
        "command": "uv",
        "args": ["--directory", "C:/Users/jxie0/pageindex-rag", "run", "rag-server"]
      }
    }
  }
""")


if __name__ == "__main__":
    main()
