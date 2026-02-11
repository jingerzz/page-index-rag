"""Keyword search across tree nodes."""

import re
from . import tree_store


def _flatten_nodes(structure, path="", doc_id="", doc_name=""):
    """Recursively flatten tree structure into a list of searchable nodes."""
    results = []
    if isinstance(structure, dict):
        title = structure.get("title", "")
        current_path = f"{path}/{title}" if path else title
        node = {
            "doc_id": doc_id,
            "doc_name": doc_name,
            "node_id": structure.get("node_id", ""),
            "node_path": current_path,
            "title": title,
            "summary": structure.get("summary", structure.get("prefix_summary", "")),
            "text": structure.get("text", ""),
        }
        results.append(node)
        if "nodes" in structure:
            results.extend(_flatten_nodes(structure["nodes"], current_path, doc_id, doc_name))
    elif isinstance(structure, list):
        for item in structure:
            results.extend(_flatten_nodes(item, path, doc_id, doc_name))
    return results


def _score_node(node, query_terms):
    """Score a node based on keyword matches in title, summary, text."""
    score = 0
    title_lower = node["title"].lower()
    summary_lower = node["summary"].lower()
    text_lower = node["text"].lower() if node["text"] else ""

    for term in query_terms:
        term_lower = term.lower()
        if term_lower in title_lower:
            score += 5
        if term_lower in summary_lower:
            score += 3
        if term_lower in text_lower:
            score += 1
    return score


def search_trees(query: str, max_results: int = 10, doc_id: str | None = None) -> list[dict]:
    """Search across all indexed tree nodes by keyword.

    Returns list of dicts with: doc_name, node_path, title, summary, text_snippet, score.
    """
    query_terms = [t for t in re.split(r'\s+', query.strip()) if t]
    if not query_terms:
        return []

    # Load trees
    if doc_id:
        record = tree_store.load_tree(doc_id)
        all_records = [record] if record else []
    else:
        all_records = tree_store.load_all_trees()

    # Flatten all nodes
    all_nodes = []
    for record in all_records:
        tree = record.get("tree", {})
        structure = tree.get("structure", [])
        d_id = record.get("doc_id", "")
        d_name = tree.get("doc_name", record.get("source_file", ""))
        all_nodes.extend(_flatten_nodes(structure, doc_id=d_id, doc_name=d_name))

    # Score and rank
    scored = []
    for node in all_nodes:
        score = _score_node(node, query_terms)
        if score > 0:
            text = node["text"] or ""
            # Create a snippet around the first match
            snippet = ""
            for term in query_terms:
                idx = text.lower().find(term.lower())
                if idx >= 0:
                    start = max(0, idx - 100)
                    end = min(len(text), idx + 200)
                    snippet = ("..." if start > 0 else "") + text[start:end] + ("..." if end < len(text) else "")
                    break
            if not snippet and text:
                snippet = text[:300] + ("..." if len(text) > 300 else "")

            scored.append({
                "doc_id": node["doc_id"],
                "doc_name": node["doc_name"],
                "node_id": node["node_id"],
                "node_path": node["node_path"],
                "title": node["title"],
                "summary": node["summary"],
                "text_snippet": snippet,
                "score": score,
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:max_results]


def get_document_overview(doc_id: str) -> str:
    """Get a TOC-style listing of all nodes in a document."""
    record = tree_store.load_tree(doc_id)
    if not record:
        return f"Document '{doc_id}' not found."

    tree = record.get("tree", {})
    doc_name = tree.get("doc_name", record.get("source_file", ""))
    doc_desc = tree.get("doc_description", "")
    structure = tree.get("structure", [])

    lines = [f"Document: {doc_name}"]
    if doc_desc:
        lines.append(f"Description: {doc_desc}")
    lines.append("")

    def _walk(nodes, indent=0):
        if isinstance(nodes, dict):
            nodes = [nodes]
        if not isinstance(nodes, list):
            return
        for node in nodes:
            prefix = "  " * indent
            title = node.get("title", "Untitled")
            node_id = node.get("node_id", "")
            summary = node.get("summary", node.get("prefix_summary", ""))
            line = f"{prefix}- [{node_id}] {title}"
            if summary:
                short = summary[:120] + ("..." if len(summary) > 120 else "")
                line += f" — {short}"
            lines.append(line)
            if "nodes" in node:
                _walk(node["nodes"], indent + 1)

    _walk(structure)
    return "\n".join(lines)
