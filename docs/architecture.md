# Architecture

## Brand architecture

- **PageIndex** is the core indexing and retrieval engine.
- **FidoSEC** is the SEC filing acquisition/use-case layer that feeds PageIndex.

## Runtime flow

1. `fetch-sec` retrieves SEC filings into `data/drop/`.
2. `ingest` parses supported files and builds PageIndex trees.
3. Trees and metadata are persisted under `data/indexes/`.
4. `manage-docs` and MCP tools expose retrieval and document operations.

## Core modules

- `src/pageindex/`: tree primitives and indexing utilities.
- `src/parsers/`: input normalization (HTML/PDF/CSV/Text).
- `src/server.py`: MCP tool surface.
- `src/tree_search.py` + `src/tree_store.py`: retrieval and storage logic.

## Design principles

- Structure-first retrieval over embeddings-first retrieval.
- Explicit, inspectable node-level access.
- Interoperability with agent clients via MCP.
