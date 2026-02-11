# Contributing to PageIndex

Thanks for your interest in contributing.

## Development setup

```bash
uv sync
cp config.example.json config.json
```

## Common commands

```bash
uv run pageindex-rag --help
uv run fetch-sec
uv run ingest
uv run manage-docs
uv run rag-server
```

## Contribution guidelines

- Keep changes scoped and easy to review.
- Prefer explicit, inspectable retrieval behavior over hidden heuristics.
- Preserve hierarchy-faithful parsing behavior where possible.
- Include tests for parser or indexing behavior when touching core logic.
- Update docs (`README.md`, `docs/`) when behavior changes.

## Pull requests

Please include:

1. Problem statement and motivation.
2. What changed and why.
3. How to validate (commands + expected outcomes).
4. Any known limitations.

## Reporting issues

When filing a bug, include:

- input file type/source (e.g., SEC 10-K HTML),
- exact command run,
- error trace,
- expected vs actual behavior.

## Code of conduct

Be respectful and constructive. This project aims to support serious infrastructure and research workflows.
