"""MCP server exposing document RAG tools to Claude Desktop."""

import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import tree_store, tree_search, indexer
from .parsers import PARSERS

# All logging to stderr (stdout is MCP protocol channel)
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("pageindex-rag")

ROOT = Path(__file__).resolve().parent.parent
DROP_DIR = ROOT / "data" / "drop"
PROCESSED_DIR = ROOT / "data" / "processed"

mcp = FastMCP("pageindex-rag")

# Thread pool for indexing (PageIndex calls asyncio.run() internally)
_executor = ThreadPoolExecutor(max_workers=1)


# ── Search tools ──────────────────────────────────────────────────────────────


@mcp.tool()
def search_documents(query: str, doc_id: str = "") -> str:
    """Search across all indexed documents by keyword.

    Args:
        query: Search query (keywords)
        doc_id: Optional document ID to restrict search to a single document
    """
    results = tree_search.search_trees(query, max_results=10, doc_id=doc_id or None)
    if not results:
        return "No matching results found."

    parts = []
    for r in results:
        header = f"[{r['doc_name']}] {r['node_path']} (score: {r['score']})"
        summary = f"Summary: {r['summary']}" if r['summary'] else ""
        snippet = f"Snippet: {r['text_snippet']}" if r['text_snippet'] else ""
        section = "\n".join(filter(None, [header, summary, snippet]))
        parts.append(section)
    return "\n\n---\n\n".join(parts)


@mcp.tool()
def get_document_section(doc_id: str, node_id: str) -> str:
    """Get the full text of a specific section/node in a document.

    Args:
        doc_id: The document ID
        node_id: The node ID (e.g. "0001", "0005")
    """
    record = tree_store.load_tree(doc_id)
    if not record:
        return f"Document '{doc_id}' not found."

    tree = record.get("tree", {})
    structure = tree.get("structure", [])

    # Find node by node_id
    def _find_node(nodes, target_id):
        if isinstance(nodes, dict):
            nodes = [nodes]
        if not isinstance(nodes, list):
            return None
        for node in nodes:
            if node.get("node_id") == target_id:
                return node
            if "nodes" in node:
                found = _find_node(node["nodes"], target_id)
                if found:
                    return found
        return None

    node = _find_node(structure, node_id)
    if not node:
        return f"Node '{node_id}' not found in document '{doc_id}'."

    title = node.get("title", "Untitled")
    text = node.get("text", "")
    summary = node.get("summary", node.get("prefix_summary", ""))

    parts = [f"# {title}"]
    if summary:
        parts.append(f"\n**Summary:** {summary}")
    if text:
        parts.append(f"\n{text}")
    else:
        parts.append("\n(No text content available for this node)")

    return "\n".join(parts)


@mcp.tool()
def get_document_overview(doc_id: str) -> str:
    """Get a table-of-contents overview of a document.

    Args:
        doc_id: The document ID
    """
    return tree_search.get_document_overview(doc_id)


@mcp.tool()
def list_documents() -> str:
    """List all indexed documents in the RAG database."""
    docs = tree_store.list_trees()
    if not docs:
        return "No documents indexed yet. Drop files in data/drop/ and run ingest."

    lines = [f"**{len(docs)} document(s) indexed:**\n"]
    for doc in docs:
        desc = f" — {doc['doc_description']}" if doc.get("doc_description") else ""
        lines.append(f"- **{doc['doc_name']}** (id: `{doc['doc_id']}`, {doc['node_count']} nodes){desc}")
    return "\n".join(lines)


# ── Ingestion tools ───────────────────────────────────────────────────────────


@mcp.tool()
def ingest_drop_folder() -> str:
    """Process and index any supported files in the data/drop/ folder."""
    import shutil

    DROP_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    files = [f for f in sorted(DROP_DIR.iterdir()) if f.is_file() and f.suffix.lower() in PARSERS or f.suffix.lower() in (".md", ".markdown")]
    if not files:
        return "No supported files found in the drop folder."

    results = []
    for filepath in files:
        try:
            # Run indexing in thread pool to avoid nested asyncio.run() issues
            future = _executor.submit(indexer.index_document, filepath)
            doc_id = future.result(timeout=600)

            # Move to processed
            dest = PROCESSED_DIR / filepath.name
            if dest.exists():
                dest = PROCESSED_DIR / f"{filepath.stem}_{id(filepath)}{filepath.suffix}"
            shutil.move(str(filepath), str(dest))

            results.append(f"OK {filepath.name} -> doc_id: {doc_id}")
        except Exception as e:
            results.append(f"FAIL {filepath.name} — {e}")

    return f"Processed {len(files)} file(s):\n" + "\n".join(results)


@mcp.tool()
def remove_document(doc_id: str) -> str:
    """Remove an indexed document from the database.

    Args:
        doc_id: The document ID to remove
    """
    deleted = tree_store.delete_tree(doc_id)
    if deleted:
        return f"Removed document '{doc_id}'."
    return f"Document '{doc_id}' not found."


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
