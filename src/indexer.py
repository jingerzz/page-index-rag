"""Orchestrator: parse -> tree build -> embed -> store."""

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from .pageindex import page_index, md_to_tree
from .parsers import parse_file
from .parsers.html_to_markdown import html_to_markdown
from . import tree_store
from .llm import _get_summary_token_threshold

logger = logging.getLogger("pageindex-rag")


def _generate_embeddings_if_enabled(tree_data: dict) -> dict[str, list[float]] | None:
    """Generate node embeddings if semantic search is enabled.

    Returns embeddings dict or None if disabled/unavailable.
    """
    try:
        from . import embeddings
    except ImportError:
        return None

    if not embeddings.is_enabled():
        logger.info("Semantic search disabled in config, skipping embeddings")
        return None

    structure = tree_data.get("structure", [])
    if not structure:
        return None

    try:
        result = embeddings.generate_node_embeddings(structure)
        if result:
            logger.info(f"Generated {len(result)} node embeddings")
        return result or None
    except Exception as e:
        logger.warning(f"Embedding generation failed (non-fatal): {e}")
        return None


def index_document(filepath: str | Path, metadata: dict | None = None) -> str:
    """Index a document and store its tree. Returns doc_id.

    Routes by file type:
      - .pdf -> PageIndex page_index() (TOC detection, page-based tree)
      - .md/.markdown -> PageIndex md_to_tree()
      - .html/.htm -> hierarchy-faithful HTML->Markdown, then md_to_tree()
      - Everything else -> parse to text, wrap as markdown, feed to md_to_tree()

    If semantic search is enabled, generates embeddings for all nodes
    and stores them alongside the tree data.
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
            summary_token_threshold=_get_summary_token_threshold(),
            if_add_node_text="yes",
            if_add_doc_description="no",
        ))
    elif suffix in (".html", ".htm"):
        markdown_str = html_to_markdown(filepath)
        tmp_dir = tempfile.mkdtemp()
        tmp_md = Path(tmp_dir) / f"{filepath.stem}.md"
        tmp_md.write_text(markdown_str, encoding="utf-8")
        try:
            tree_data = asyncio.run(md_to_tree(
                str(tmp_md),
                if_add_node_summary="yes",
                summary_token_threshold=_get_summary_token_threshold(),
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
        text, parse_meta = parse_file(filepath)
        meta.update(parse_meta)

        tmp_dir = tempfile.mkdtemp()
        tmp_md = Path(tmp_dir) / f"{filepath.stem}.md"
        md_content = f"# {filepath.stem}\n\n{text}"
        tmp_md.write_text(md_content, encoding="utf-8")

        try:
            tree_data = asyncio.run(md_to_tree(
                str(tmp_md),
                if_add_node_summary="yes",
                summary_token_threshold=_get_summary_token_threshold(),
                if_add_node_text="yes",
                if_add_doc_description="no",
            ))
        finally:
            tmp_md.unlink(missing_ok=True)
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

    node_embeddings = _generate_embeddings_if_enabled(tree_data)

    doc_id = tree_store.save_tree(source_file, tree_data, meta, embeddings=node_embeddings)
    return doc_id
