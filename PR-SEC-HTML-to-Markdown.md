# PR: SEC EDGAR HTML → hierarchy-faithful Markdown → md_to_tree()

## Summary

Switch the SEC filing path to **EDGAR primary HTML → clean, hierarchy-faithful Markdown → md_to_tree()** instead of treating HTML as flat text (or using PDF). This gives accurate trees with minimal or no LLM cost for structure, and aligns with PageIndex’s design that the tree is built from `#` / `##` / `###` heading levels.

**Goal:** For `.html` / `.htm` (and optionally when we detect EDGAR), convert the document to Markdown that **preserves heading hierarchy** (e.g. `h1`→`#`, `h2`→`##`, `h3`→`###`), then run `md_to_tree()` on that Markdown. Do **not** use the current path that flattens HTML to plain text and wraps it in a single `# filename` heading (which produces a one-node tree).

---

## Background

- **Current behavior for HTML:** `indexer.index_document()` routes `.html` to the “everything else” branch: `parse_file()` → `parse_html()` returns **flat text** (BeautifulSoup `get_text()`), then the indexer wraps it as `# {filename}\n\n{text}` and passes that to `md_to_tree()`. Result: one root node, no real hierarchy.
- **Current behavior for PDF:** Uses PageIndex’s full pipeline (TOC detection, extraction, page numbers, etc.) with many LLM calls—expensive and sometimes brittle.
- **Desired behavior for SEC filings:** EDGAR primary HTML has semantic structure (headings, sections, Item 1, Item 1A, etc.). If we convert that to Markdown where heading levels are preserved, `md_to_tree()` can build the tree from `#`/`##`/`###` **without any LLM calls for structure**. Optional LLM can still be used for node summaries and doc description.

---

## Scope

1. **Add a hierarchy-faithful HTML → Markdown converter** suitable for EDGAR (and general HTML with headings).
2. **Change the indexer** so that `.html`/`.htm` use this converter and then `md_to_tree()`, instead of `parse_file()` + single-heading wrap.
3. **Keep existing `parse_html()`** for any callers that still want flat text; do not remove it. The indexer simply stops using it for the main HTML indexing path.
4. **Leave PDF path unchanged** (still use `page_index()` for `.pdf`). PDF remains the path for non-SEC or when only PDF is available.
5. **No change to** `.md`/`.markdown` handling (still direct `md_to_tree()` on file).

---

## Acceptance criteria

- [ ] **New converter:** A function or small module that takes HTML (string or `Path`) and returns a Markdown string where:
  - `<h1>` → `# Title`
  - `<h2>` → `## Title`
  - `<h3>` → `### Title`
  - (and so on for `h4`–`h6` if desired)
  - Non-heading content is preserved (e.g. paragraphs, lists, tables as readable text). Code blocks can be preserved as fenced blocks if straightforward.
  - No requirement to support every HTML tag; focus on headings + body text so that `md_to_tree()` gets a valid, hierarchy-faithful Markdown document.
- [ ] **Indexer:** For `suffix in (".html", ".htm")`, the indexer:
  - Uses the new HTML→Markdown converter to produce a Markdown string.
  - Writes it to a temp file (or passes content to a path `md_to_tree()` can read).
  - Calls `md_to_tree()` with the same options as for `.md` (e.g. `if_add_node_summary="yes"`, `if_add_node_text="yes"`, `if_add_doc_description="yes"`).
  - Does **not** use `parse_file()` + single `# filename` wrap for HTML.
- [ ] **Tree shape:** Indexing a small test HTML file that has multiple `h1`/`h2`/`h3` produces a tree with multiple root nodes and/or nested nodes (depth ≥ 2), not a single root node.
- [ ] **Ingest:** `uv run ingest` still picks up `.html`/`.htm` from `data/drop/` and indexes them (no change to `SUPPORTED_EXTENSIONS` needed if HTML is already there; confirm ingest works with the new path).
- [ ] **Backward compatibility:** Any code that calls `parse_file()` or `parse_html()` directly still works; only the indexer’s routing for HTML changes.
- [ ] **Docs:** README or relevant docstrings mention that for best results with SEC filings, use EDGAR primary HTML (or hierarchy-faithful Markdown); optionally note that HTML is now converted to heading-preserving Markdown before indexing.

---

## Technical approach

### 1. New module: HTML → Markdown (hierarchy-faithful)

- **Suggested path:** `src/parsers/html_to_markdown.py` (or `edgar_html_to_markdown.py` if you want to stress EDGAR).
- **Input:** `Path` or HTML string.
- **Output:** Markdown string.
- **Logic (high level):**
  - Parse HTML with BeautifulSoup (already a dependency).
  - Walk the DOM in order. For each element:
    - If it’s `<h1>`…`<h6>`, emit `#`…`######` + space + inner text (strip tags), then newline.
    - If it’s a block-level element (e.g. `<p>`, `<div>`, `<li>`), emit text content (and newlines); optionally convert `<table>` to a simple Markdown table or line-based text so it doesn’t break structure.
  - Preserve order so that heading levels and content order match the document. Avoid emitting duplicate headings if the same text appears under different tags; prefer first occurrence or a simple rule.
- **SEC/EDGAR:** Many EDGAR filings use `<div>` with classes or IDs for sections rather than strict `<h1>`/`<h2>`. A first version can rely only on `h1`–`h6`; a follow-up can add optional heuristics (e.g. divs with class/id containing “item”, “part”, “section”) to emit `##` or `###` so that hierarchy is preserved. PR can be merged with `h1`–`h6` only and EDGAR-specific tweaks done later.
- **Edge cases:** Script/style removed (like current `parse_html`). Handle encoding (UTF-8 / latin-1 fallback) so that EDGAR HTML loads correctly.

### 2. Indexer change

- **File:** `src/indexer.py`.
- **Current “everything else” branch:**  
  `text, parse_meta = parse_file(filepath)` then `md_content = f"# {filepath.stem}\n\n{text}"` then `md_to_tree(tmp_md, ...)`.
- **New behavior for HTML:**  
  If `suffix in (".html", ".htm")`:
  - Call the new converter to get `markdown_str`.
  - Write `markdown_str` to a temp file (e.g. `tmp_md = Path(tempfile.mkdtemp()) / f"{filepath.stem}.md"`).
  - Call `asyncio.run(md_to_tree(str(tmp_md), if_add_node_summary="yes", summary_token_threshold=200, if_add_node_text="yes", if_add_doc_description="yes"))`.
  - Clean up temp file/dir as in the existing “everything else” branch.
- **Else:** Keep current behavior (parse_file + single heading wrap) for other types (e.g. `.txt`, `.csv`).

### 3. Optional: EDGAR detection

- Not required for this PR. If you want to be explicit later, we could add a heuristic (e.g. filename pattern, or a meta/comment in the HTML) to use a stricter “EDGAR” conversion; for now, “all HTML uses hierarchy-faithful conversion” is enough.

### 4. Tests

- **Unit test for converter:** Given a small HTML string with e.g. one `h1`, two `h2`, and a few `p`, assert the output contains `# …`, `## …`, `## …` in order and no spurious extra `#` for body text.
- **Integration test for indexer:** Create a temp HTML file with multiple headings; call `index_document(path)`; load the tree; assert `len(structure) >= 2` (or assert a specific node title exists and depth > 1). Then delete the doc and temp file.

### 5. Files to touch (checklist)

- [ ] **Add** `src/parsers/html_to_markdown.py` (or equivalent) with the converter function and encoding/script-style handling.
- [ ] **Modify** `src/indexer.py`: add branch for `.html`/`.htm` that uses the new converter + temp Markdown file + `md_to_tree()`; leave PDF and `.md`/`.markdown` and “everything else” logic unchanged except that “everything else” no longer applies to HTML.
- [ ] **Optional:** Export the new function from `src/parsers/__init__.py` (e.g. `html_to_markdown`) if other code should use it; indexer can import from `parsers.html_to_markdown` directly if preferred.
- [ ] **Docs:** Short note in README (e.g. “SEC filings”) and/or indexer docstring that HTML is converted to hierarchy-faithful Markdown for tree building.
- [ ] **Test:** Add a test in `tests/` or under a `tests/` directory (if none, add one or two manual test commands in a comment in the PR) to verify multi-node tree from HTML.

---

## Out of scope for this PR

- Changing PDF indexing.
- Adding SEC-specific metadata (e.g. ticker, form type) to the tree; can be a follow-up.
- Fetching EDGAR HTML from the web (this PR assumes the user drops a local HTML file into `data/drop/` or passes a path to the indexer).
- Supporting non-EDGAR HTML with no headings (fallback: they get a flat or minimal tree; that’s acceptable).

---

## Testing commands (for the agent)

After implementing:

1. **Converter unit test (conceptual):**
   ```python
   html = "<html><body><h1>Item 1</h1><p>Text.</p><h2>Item 1A</h2><p>More.</p></body></html>"
   md = html_to_markdown(html)  # or from file
   assert md.strip().startswith("# Item 1")
   assert "## Item 1A" in md
   assert "Text." in md
   ```

2. **Indexer integration:**
   - Save the same HTML to `data/drop/test_sec.html`.
   - Run `uv run ingest` (or `index_document(Path("data/drop/test_sec.html"))`).
   - Run `uv run manage-docs` and/or load the tree and assert at least 2 top-level nodes (e.g. “Item 1”, “Item 1A”).
   - Search: `search_trees("Item 1A")` returns at least one result.

3. **Regression:** Existing QA tests for parsers (T2.3 HTML parser) still pass. Note: T2.3 tests `parse_html()` (flat text); that function is unchanged, so it should still pass. The new converter is used only by the indexer for the HTML path.

---

## References

- PageIndex Markdown tree building: `src/pageindex/page_index_md.py` — `extract_nodes_from_markdown()` uses regex `^(#{1,6})\s+(.+)$` for headings.
- Current indexer routing: `src/indexer.py` — `index_document()`.
- Current HTML parsing (flat): `src/parsers/html_parser.py` — `parse_html()`.
- Ingest supported extensions: `src/ingest.py` — `SUPPORTED_EXTENSIONS`.

---

## Changelog (for after merge)

Suggested entry for `CHANGELOG.md`:

```markdown
## [Unreleased] or [0.2.0] — YYYY-MM-DD

### Added
- **Hierarchy-faithful HTML → Markdown** — New converter in `src/parsers/html_to_markdown.py` that maps HTML heading tags (`h1`–`h6`) to Markdown `#`–`######` and preserves document order. Used when indexing `.html`/`.htm` so that the tree is built from structure instead of flat text.

### Changed
- **Indexer HTML path** — `.html`/`.htm` files are now converted to hierarchy-faithful Markdown and passed to `md_to_tree()` instead of being flattened and wrapped in a single `# filename` heading. Produces multi-node trees for SEC EDGAR HTML (and any HTML with headings) with no LLM calls for structure.
```

---

*PR written for coding agent; implement per acceptance criteria and technical approach above.*
