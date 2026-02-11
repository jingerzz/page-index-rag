# QA Test Plan — pageindex-rag

This document is a step-by-step QA plan that an agent or human can follow to verify the project works end-to-end. Each test has clear pass/fail criteria.

## Prerequisites

- Working directory: `C:\Users\jxie0\pageindex-rag`
- Python 3.11+
- `uv` installed
- A valid `config.json` with a real OpenRouter API key (copy from `config.example.json`)

---

## Phase 1: Environment & Build

### T1.1 — uv sync installs without errors
```bash
uv sync
```
**Pass:** Exit code 0, no build errors. All 62+ packages installed.

### T1.2 — Entry points are registered
```bash
uv run pageindex-rag
uv run ingest --help 2>&1 || uv run ingest
uv run manage-docs
```
**Pass:** `pageindex-rag` prints help text. `ingest` runs (may say "no files found"). `manage-docs` runs (may say "no documents indexed").

### T1.3 — Config loading works
```bash
uv run python -c "from src.llm import _load_config, _get_api_key, _get_model; c=_load_config(); print('key_len:', len(_get_api_key())); print('model:', _get_model())"
```
**Pass:** Prints key length > 0 and model name (e.g. `google/gemini-2.5-flash`).

---

## Phase 2: Parsers (unit-level)

### T2.1 — Text parser
```bash
uv run python -c "
from pathlib import Path; from src.parsers.text_parser import parse_text
p = Path('data/drop/test.txt'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text('Hello world')
text, meta = parse_text(p)
print('text:', repr(text[:50])); print('meta:', meta)
p.unlink()
"
```
**Pass:** Prints text and metadata dict with `source_file` and `file_type`.

### T2.2 — CSV parser
```bash
uv run python -c "
from pathlib import Path; from src.parsers.csv_parser import parse_csv
p = Path('data/drop/test.csv'); p.write_text('Name,Age\nAlice,30\nBob,25')
text, meta = parse_csv(p)
print('text:', repr(text[:80])); print('meta:', meta)
p.unlink()
"
```
**Pass:** Prints natural language rows and metadata with `rows` and `columns`.

### T2.3 — HTML parser
```bash
uv run python -c "
from pathlib import Path; from src.parsers.html_parser import parse_html
p = Path('data/drop/test.html'); p.write_text('<html><body><h1>Title</h1><p>Content</p></body></html>')
text, meta = parse_html(p)
print('text:', repr(text[:80])); print('meta:', meta)
p.unlink()
"
```
**Pass:** Prints extracted text (no HTML tags) and metadata.

### T2.4 — PDF parser
```bash
uv run python -c "
from pathlib import Path; from src.parsers.pdf_parser import parse_pdf
# Only run if a test PDF exists
import glob; pdfs = glob.glob('data/drop/*.pdf')
if pdfs:
    text, meta = parse_pdf(Path(pdfs[0]))
    print('pages:', meta.get('pages')); print('text_len:', len(text))
else:
    print('SKIP: no PDF in data/drop/')
"
```
**Pass:** If a PDF exists, prints page count and text length. Otherwise prints SKIP.

### T2.5 — parse_file router
```bash
uv run python -c "
from pathlib import Path; from src.parsers import parse_file
p = Path('data/drop/test.txt'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text('router test')
text, meta = parse_file(p)
print('routed:', meta['file_type'])
p.unlink()
"
```
**Pass:** Prints `routed: text`.

---

## Phase 3: LLM Client

### T3.1 — Synchronous LLM call
```bash
uv run python -c "
from src.llm import llm_call
result = llm_call(prompt='Reply with exactly: HELLO')
print('response:', repr(result[:50]))
assert 'HELLO' in result.upper(), 'LLM did not respond correctly'
print('PASS')
"
```
**Pass:** Prints response containing "HELLO" and "PASS". Requires valid API key.

### T3.2 — LLM call with finish reason
```bash
uv run python -c "
from src.llm import llm_call_with_finish_reason
content, reason = llm_call_with_finish_reason(prompt='Say hi')
print('reason:', reason); print('content:', repr(content[:50]))
assert reason == 'finished', f'Unexpected reason: {reason}'
print('PASS')
"
```
**Pass:** Finish reason is `finished`.

### T3.3 — Async LLM call
```bash
uv run python -c "
import asyncio; from src.llm import llm_call_async
result = asyncio.run(llm_call_async(prompt='Reply with exactly: ASYNC_OK'))
print('response:', repr(result[:50]))
assert 'ASYNC_OK' in result.upper(), 'Async LLM failed'
print('PASS')
"
```
**Pass:** Response contains "ASYNC_OK".

---

## Phase 4: Tree Store

### T4.1 — Save and load a tree
```bash
uv run python -c "
from src.tree_store import save_tree, load_tree, delete_tree
doc_id = save_tree('test_doc.pdf', {'doc_name': 'Test', 'structure': [{'title': 'Ch1', 'node_id': '0001'}]}, {'test': True})
print('saved:', doc_id)
loaded = load_tree(doc_id)
assert loaded is not None, 'Load failed'
assert loaded['tree']['doc_name'] == 'Test'
print('loaded OK')
delete_tree(doc_id)
assert load_tree(doc_id) is None, 'Delete failed'
print('PASS: save/load/delete cycle works')
"
```
**Pass:** Prints saved doc_id, loaded OK, PASS.

### T4.2 — List trees
```bash
uv run python -c "
from src.tree_store import save_tree, list_trees, delete_tree
doc_id = save_tree('list_test.txt', {'doc_name': 'ListTest', 'structure': []})
trees = list_trees()
print(f'{len(trees)} tree(s) found')
assert any(t['doc_id'] == doc_id for t in trees), 'Not in list'
delete_tree(doc_id)
print('PASS')
"
```
**Pass:** Tree appears in list, then is cleaned up.

---

## Phase 5: Tree Search

### T5.1 — Search finds matching nodes
```bash
uv run python -c "
from src.tree_store import save_tree, delete_tree
from src.tree_search import search_trees, get_document_overview
doc_id = save_tree('search_test.pdf', {
    'doc_name': 'SearchDoc',
    'structure': [
        {'title': 'Introduction', 'node_id': '0001', 'summary': 'Overview of machine learning concepts', 'text': 'Machine learning is a subset of AI.'},
        {'title': 'Methods', 'node_id': '0002', 'summary': 'Various ML methods', 'text': 'We use neural networks and decision trees.'}
    ]
})
results = search_trees('machine learning')
print(f'{len(results)} result(s)')
assert len(results) > 0, 'No results found'
assert results[0]['title'] in ('Introduction', 'Methods')
print('search OK')

overview = get_document_overview(doc_id)
assert 'Introduction' in overview
print('overview OK')

delete_tree(doc_id)
print('PASS')
"
```
**Pass:** Search returns results, overview contains section titles.

### T5.2 — Search with doc_id filter
```bash
uv run python -c "
from src.tree_store import save_tree, delete_tree
from src.tree_search import search_trees
id1 = save_tree('doc_a.pdf', {'doc_name': 'DocA', 'structure': [{'title': 'Alpha', 'node_id': '0001', 'summary': 'alpha content', 'text': 'alpha'}]})
id2 = save_tree('doc_b.pdf', {'doc_name': 'DocB', 'structure': [{'title': 'Beta', 'node_id': '0001', 'summary': 'beta content', 'text': 'beta'}]})
r1 = search_trees('alpha', doc_id=id1)
r2 = search_trees('alpha', doc_id=id2)
print(f'filtered to doc_a: {len(r1)} results, filtered to doc_b: {len(r2)} results')
assert len(r1) > 0 and len(r2) == 0, 'Filter not working'
delete_tree(id1); delete_tree(id2)
print('PASS')
"
```
**Pass:** Search filtered to doc_a finds results; filtered to doc_b finds none.

---

## Phase 6: Markdown Indexing (end-to-end, requires API key)

### T6.1 — Index a markdown file
```bash
uv run python -c "
from pathlib import Path
from src.indexer import index_document
from src.tree_store import load_tree, delete_tree

# Create test markdown
md = Path('data/drop/test_qa.md')
md.parent.mkdir(parents=True, exist_ok=True)
md.write_text('# Chapter 1\n\nThis is chapter one content about testing.\n\n## Section 1.1\n\nDetails about unit tests.\n\n# Chapter 2\n\nThis is chapter two about deployment.\n')

doc_id = index_document(md)
print(f'Indexed as: {doc_id}')

tree = load_tree(doc_id)
assert tree is not None, 'Tree not saved'
structure = tree['tree']['structure']
print(f'Nodes: {len(structure)}')
assert len(structure) >= 2, f'Expected at least 2 root nodes, got {len(structure)}'
print('PASS')

# Cleanup
delete_tree(doc_id)
md.unlink(missing_ok=True)
"
```
**Pass:** Markdown is indexed, tree has at least 2 root nodes (Chapter 1, Chapter 2). Requires API key for summary generation.

---

## Phase 7: PDF Indexing (end-to-end, requires API key + test PDF)

### T7.1 — Index a PDF via ingest CLI
1. Place a small PDF (< 20 pages) in `data/drop/`
2. Run:
```bash
uv run ingest
```
3. Follow the prompts (confirm indexing).

**Pass:** File is indexed, moved to `data/processed/`, and a JSON tree file appears in `data/indexes/`.

### T7.2 — Verify indexed PDF
```bash
uv run manage-docs
```
**Pass:** Shows the indexed document with doc_id, name, and node count.

### T7.3 — Search the indexed PDF
```bash
uv run python -c "
from src.tree_search import search_trees
results = search_trees('introduction')  # or any keyword from your PDF
for r in results[:3]:
    print(f'{r[\"doc_name\"]} | {r[\"title\"]} | score={r[\"score\"]}')
"
```
**Pass:** Returns relevant results from the indexed PDF.

---

## Phase 8: MCP Server

### T8.1 — Server starts without error
```bash
timeout 5 uv run rag-server 2>&1 || true
```
**Pass:** No Python import errors or crashes. Server may hang waiting for stdio input (expected), then timeout kills it. Check stderr for errors.

### T8.2 — MCP tool: list_documents
```bash
uv run python -c "
from src.server import list_documents
print(list_documents())
"
```
**Pass:** Returns a string listing indexed documents (or 'No documents indexed yet').

### T8.3 — MCP tool: search_documents
```bash
uv run python -c "
from src.tree_store import save_tree, delete_tree
doc_id = save_tree('mcp_test.pdf', {
    'doc_name': 'MCPTest',
    'structure': [{'title': 'Revenue', 'node_id': '0001', 'summary': 'Q4 revenue analysis', 'text': 'Revenue grew 15% year over year.'}]
})
from src.server import search_documents
result = search_documents('revenue')
print(result)
assert 'Revenue' in result or 'revenue' in result
delete_tree(doc_id)
print('PASS')
"
```
**Pass:** Search returns matching content.

### T8.4 — MCP tool: get_document_section
```bash
uv run python -c "
from src.tree_store import save_tree, delete_tree
doc_id = save_tree('section_test.pdf', {
    'doc_name': 'SectionTest',
    'structure': [{'title': 'Abstract', 'node_id': '0001', 'summary': 'Paper abstract', 'text': 'This paper presents a novel approach.'}]
})
from src.server import get_document_section
result = get_document_section(doc_id, '0001')
print(result)
assert 'novel approach' in result
delete_tree(doc_id)
print('PASS')
"
```
**Pass:** Returns the full text of the node.

### T8.5 — MCP tool: remove_document
```bash
uv run python -c "
from src.tree_store import save_tree, load_tree
from src.server import remove_document
doc_id = save_tree('remove_test.pdf', {'doc_name': 'RemoveTest', 'structure': []})
result = remove_document(doc_id)
print(result)
assert 'Removed' in result
assert load_tree(doc_id) is None
print('PASS')
"
```
**Pass:** Document is removed.

---

## Phase 9: Edge Cases

### T9.1 — Unsupported file type
```bash
uv run python -c "
from pathlib import Path; from src.parsers import parse_file
try:
    parse_file(Path('test.xyz'))
except ValueError as e:
    print(f'Correctly raised: {e}')
    print('PASS')
"
```
**Pass:** Raises ValueError for unsupported extension.

### T9.2 — Empty drop folder
```bash
uv run ingest
```
**Pass:** Prints "No supported files found" message, exits cleanly.

### T9.3 — Search with no indexed documents
```bash
uv run python -c "
from src.tree_search import search_trees
results = search_trees('anything')
print(f'Results: {len(results)}')
assert len(results) == 0
print('PASS')
"
```
**Pass:** Returns empty list, no errors.

### T9.4 — Load nonexistent tree
```bash
uv run python -c "
from src.tree_store import load_tree
result = load_tree('nonexistent_id')
assert result is None
print('PASS')
"
```
**Pass:** Returns None.

---

## Summary

| Phase | Tests | Requires API Key |
|---|---|---|
| 1. Environment | T1.1–T1.3 | No |
| 2. Parsers | T2.1–T2.5 | No |
| 3. LLM Client | T3.1–T3.3 | **Yes** |
| 4. Tree Store | T4.1–T4.2 | No |
| 5. Tree Search | T5.1–T5.2 | No |
| 6. Markdown Indexing | T6.1 | **Yes** |
| 7. PDF Indexing | T7.1–T7.3 | **Yes** + test PDF |
| 8. MCP Server | T8.1–T8.5 | No (except T8.1 startup) |
| 9. Edge Cases | T9.1–T9.4 | No |

**Phases 1–2, 4–5, 8–9 can be run without an API key.** Phases 3, 6, 7 require a valid OpenRouter API key in `config.json`.
