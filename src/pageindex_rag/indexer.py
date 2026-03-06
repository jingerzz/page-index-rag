"""Orchestrator: parse -> tree build -> embed -> store."""

import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path

from .pageindex import page_index, md_to_tree
from .pageindex.utils import count_tokens
from .parsers import parse_file
from .parsers.html_to_markdown import html_to_markdown
from . import tree_store
from .llm import _get_summary_token_threshold, _get_thinning_threshold

logger = logging.getLogger("pageindex-rag")

# ── Short-filing fast-path constants ─────────────────────────────────────────

# Forms that ALWAYS get full tree indexing (long, structured filings).
# Everything else → raw single-node storage (no LLM calls).
FULL_INDEX_FORMS: frozenset[str] = frozenset({
    "10-K", "10-Q", "S-1", "S-3", "S-4", "S-11",
    "20-F", "40-F", "DEF 14A", "DEFA14A", "DEF 14C",
    "6-K", "F-1", "F-3", "F-4", "N-CSR", "N-CSRS", "ARS",
})

# If a "raw" filing exceeds this token count, do full indexing anyway.
RAW_INDEX_TOKEN_LIMIT: int = 15_000

# Regex: extract form type from SEC filename like AAPL_10-K_20240216_abc123.html
# Matches between the first '_' and the '_YYYYMMDD_' date segment.
_FORM_RE = re.compile(r"^[A-Z0-9.-]+?_(.+?)_\d{8}_")


def _extract_form_type(filename: str) -> str | None:
    """Parse form type from SEC-style filename. Returns None if unparseable."""
    m = _FORM_RE.match(filename)
    return m.group(1) if m else None


def _normalize_form(form: str) -> str:
    """Normalize form string for allowlist comparison.

    Strips '/A' amendment suffix and 'Form ' prefix so that e.g.
    '10-K/A' matches '10-K' and 'Form 4' matches '4'.
    """
    form = form.strip()
    if form.endswith("/A"):
        form = form[:-2]
    if form.startswith("Form "):
        form = form[5:]
    return form


def _should_full_index(form: str | None, token_count: int) -> bool:
    """Decide whether a filing needs full tree indexing.

    Returns True (full index) when:
      - form type is unknown (None) — can't classify, be safe
      - normalized form is in the FULL_INDEX_FORMS allowlist
      - token count exceeds RAW_INDEX_TOKEN_LIMIT (safety net)
    """
    if form is None:
        return True
    if _normalize_form(form) in FULL_INDEX_FORMS:
        return True
    if token_count > RAW_INDEX_TOKEN_LIMIT:
        logger.info(
            f"Form '{form}' normally raw-stored but has {token_count} tokens "
            f"(> {RAW_INDEX_TOKEN_LIMIT}), using full indexing"
        )
        return True
    return False


def _build_raw_tree(source_name: str, form: str | None, markdown_text: str) -> dict:
    """Build a minimal single-node tree for short filings. No LLM calls."""
    form_label = form or "Unknown"
    pseudo_summary = markdown_text[:300].replace("\n", " ").strip()
    if len(markdown_text) > 300:
        pseudo_summary += "..."

    return {
        "doc_name": source_name,
        "doc_description": f"{form_label} filing (raw-stored, no hierarchical index)",
        "index_mode": "raw",
        "structure": [
            {
                "node_id": "0000",
                "title": source_name,
                "summary": pseudo_summary,
                "text": markdown_text,
            }
        ],
    }


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
        thin = _get_thinning_threshold()
        tree_data = asyncio.run(md_to_tree(
            str(filepath),
            if_thinning=thin > 0,
            min_token_threshold=thin if thin > 0 else None,
            if_add_node_summary="yes",
            summary_token_threshold=_get_summary_token_threshold(),
            if_add_node_text="yes",
            if_add_doc_description="no",
        ))
    elif suffix in (".html", ".htm"):
        markdown_str = html_to_markdown(filepath)

        # Short-filing routing: check form type and token count
        form_type = _extract_form_type(source_file)
        if form_type:
            meta["form_type"] = form_type
        token_count = count_tokens(markdown_str)

        if not _should_full_index(form_type, token_count):
            logger.info(
                f"Raw-storing {source_file} (form={form_type}, "
                f"{token_count} tokens) — skipping LLM indexing"
            )
            tree_data = _build_raw_tree(filepath.stem, form_type, markdown_str)
        else:
            tmp_dir = tempfile.mkdtemp()
            tmp_md = Path(tmp_dir) / f"{filepath.stem}.md"
            tmp_md.write_text(markdown_str, encoding="utf-8")
            thin = _get_thinning_threshold()
            try:
                tree_data = asyncio.run(md_to_tree(
                    str(tmp_md),
                    if_thinning=thin > 0,
                    min_token_threshold=thin if thin > 0 else None,
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

        thin = _get_thinning_threshold()
        try:
            tree_data = asyncio.run(md_to_tree(
                str(tmp_md),
                if_thinning=thin > 0,
                min_token_threshold=thin if thin > 0 else None,
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
