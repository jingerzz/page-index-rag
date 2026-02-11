"""Unit tests for hierarchy-faithful HTML → Markdown converter and indexer HTML path."""

import asyncio
import sys
import tempfile
from pathlib import Path

# Allow importing from src (project root so src.parsers, src.pageindex work)
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

from src.parsers.html_to_markdown import html_to_markdown


def test_html_to_markdown_basic_headings_and_paragraphs():
    """Given HTML with h1, h2, and p, output has #, ## in order and body text."""
    html = (
        "<html><body>"
        "<h1>Item 1</h1><p>Text.</p>"
        "<h2>Item 1A</h2><p>More.</p>"
        "</body></html>"
    )
    md = html_to_markdown(html)
    assert md.strip().startswith("# Item 1"), f"Expected '# Item 1' at start, got: {md[:80]!r}"
    assert "## Item 1A" in md, f"Expected '## Item 1A' in output, got: {md!r}"
    assert "Text." in md
    assert "More." in md


def test_html_to_markdown_from_string():
    """Converter accepts HTML string and returns markdown."""
    html = "<html><body><h1>Title</h1><p>Body.</p></body></html>"
    md = html_to_markdown(html)
    assert "# Title" in md
    assert "Body." in md


def test_html_to_markdown_strips_script_style():
    """Script and style elements are removed (no JS/CSS in output)."""
    html = (
        "<html><body>"
        "<script>alert(1);</script>"
        "<style>.x { color: red; }</style>"
        "<h1>Head</h1><p>Content.</p>"
        "</body></html>"
    )
    md = html_to_markdown(html)
    assert "alert" not in md
    assert "color" not in md
    assert "# Head" in md
    assert "Content." in md


def test_html_to_markdown_multiple_levels():
    """h1–h3 map to #–### in order."""
    html = (
        "<html><body>"
        "<h1>Part I</h1>"
        "<h2>Item 1</h2><p>P1</p>"
        "<h3>Sub</h3><p>P2</p>"
        "</body></html>"
    )
    md = html_to_markdown(html)
    assert "# Part I" in md
    assert "## Item 1" in md
    assert "### Sub" in md
    assert "P1" in md and "P2" in md


def test_sec_fallback_form4_table_sections():
    """Form 3/4: when no h1–h6, Table I / Table II lines become ## so we get nodes."""
    html = (
        "<html><body>"
        "<div><p>Table I - Non-Derivative Securities Acquired, Disposed of...</p><p>Row data.</p></div>"
        "<div><p>Table II - Derivative Securities Acquired, Disposed of...</p><p>Options.</p></div>"
        "</body></html>"
    )
    md = html_to_markdown(html)
    assert "## Table I" in md or "## Table I -" in md, f"Expected Table I heading, got: {md[:200]!r}"
    assert "## Table II" in md or "## Table II -" in md, f"Expected Table II heading, got: {md[:300]!r}"


def test_indexer_html_produces_multi_node_tree():
    """Indexing HTML with multiple headings produces a tree with depth >= 2 (no LLM)."""
    from src.pageindex import md_to_tree

    html = (
        "<html><body>"
        "<h1>Item 1</h1><p>Text.</p>"
        "<h2>Item 1A</h2><p>More.</p>"
        "</body></html>"
    )
    md = html_to_markdown(html)
    assert "# Item 1" in md and "## Item 1A" in md

    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
        tmp_md = Path(f.name)
    try:
        tmp_md.write_text(md, encoding="utf-8")
        tree_data = asyncio.run(
            md_to_tree(
                str(tmp_md),
                if_add_node_summary="no",
                if_add_doc_description="no",
                if_add_node_text="yes",
            )
        )
        structure = tree_data["structure"]
        assert len(structure) >= 1, "expected at least one root node"
        # Either multiple roots or one root with children (depth >= 2)
        has_depth = len(structure) >= 2 or (structure[0].get("nodes") and len(structure[0]["nodes"]) >= 1)
        assert has_depth, "expected multiple root nodes or nested nodes (depth >= 2)"
        # Check expected titles appear
        root_titles = [n["title"] for n in structure]
        assert "Item 1" in root_titles, f"expected 'Item 1' in {root_titles}"
    finally:
        tmp_md.unlink(missing_ok=True)


if __name__ == "__main__":
    test_html_to_markdown_basic_headings_and_paragraphs()
    test_html_to_markdown_from_string()
    test_html_to_markdown_strips_script_style()
    test_html_to_markdown_multiple_levels()
    test_sec_fallback_form4_table_sections()
    test_indexer_html_produces_multi_node_tree()
    print("All tests passed.")
