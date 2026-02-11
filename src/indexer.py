"""Orchestrator: parse -> tree build -> store."""

import asyncio
import os
import tempfile
from pathlib import Path

from .pageindex import page_index, md_to_tree
from .parsers import parse_file
from .parsers.html_to_markdown import html_to_markdown
from . import tree_store


def index_document(filepath: str | Path, metadata: dict | None = None) -> str:
    """Index a document and store its tree. Returns doc_id.

    Routes by file type:
      - .pdf -> PageIndex page_index() (TOC detection, page-based tree)
      - .md/.markdown -> PageIndex md_to_tree()
      - .html/.htm -> hierarchy-faithful HTML→Markdown, then md_to_tree() (no flat wrap)
      - Everything else -> parse to text, wrap as markdown, feed to md_to_tree()
    """
    filepath = Path(filepath)
    suffix = filepath.suffix.lower()
    source_file = filepath.name
    meta = metadata or {}

    if suffix == ".pdf":
        tree_data = page_index(str(filepath))
    elif suffix in (".md", ".markdown"):
        tree_data = asyncio.run(md_to_tree(
            str(filepath),
            if_add_node_summary="yes",
            summary_token_threshold=200,
            if_add_node_text="yes",
            if_add_doc_description="no",
        ))
    elif suffix in (".html", ".htm"):
        # Hierarchy-faithful HTML → Markdown, then md_to_tree() (SEC EDGAR, etc.)
        markdown_str = html_to_markdown(filepath)
        tmp_dir = tempfile.mkdtemp()
        tmp_md = Path(tmp_dir) / f"{filepath.stem}.md"
        tmp_md.write_text(markdown_str, encoding="utf-8")
        try:
            tree_data = asyncio.run(md_to_tree(
                str(tmp_md),
                if_add_node_summary="yes",
                summary_token_threshold=200,
                if_add_node_text="yes",
                if_add_doc_description="no",
            ))
        finally:
            tmp_md.unlink(missing_ok=True)
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass
    else:
        # Parse to text via parsers, wrap as markdown, feed to md_to_tree
        text, parse_meta = parse_file(filepath)
        meta.update(parse_meta)

        # Write text as a temporary markdown file with a single heading
        tmp_dir = tempfile.mkdtemp()
        tmp_md = Path(tmp_dir) / f"{filepath.stem}.md"
        md_content = f"# {filepath.stem}\n\n{text}"
        tmp_md.write_text(md_content, encoding="utf-8")

        try:
            tree_data = asyncio.run(md_to_tree(
                str(tmp_md),
                if_add_node_summary="yes",
                summary_token_threshold=200,
                if_add_node_text="yes",
                if_add_doc_description="no",
            ))
        finally:
            # Clean up temp file
            tmp_md.unlink(missing_ok=True)
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

    doc_id = tree_store.save_tree(source_file, tree_data, meta)
    return doc_id
