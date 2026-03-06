"""PageIndex reasoning-based retrieval for tree-structured documents.

Implements the PageIndex approach:
  1. Build hierarchical tree index with summaries
  2. Use LLM reasoning to navigate tree and select relevant nodes
  3. Retrieve full raw text from selected nodes for answers

This is a "vectorless" approach - no embeddings or vector DB needed.
Keyword search is available as a fast baseline, but reasoning-based
selection is preferred for complex queries.

Reference: PageIndex framework (VectifyAI) - achieved 98.7% on FinanceBench
"""

import logging
import re
from . import tree_store, llm

logger = logging.getLogger("pageindex-rag")

# Threshold for when to use LLM reasoning vs keyword search
REASONING_THRESHOLD = 3
"""Keyword score below which we invoke LLM reasoning.
Simple queries with clear keyword matches don't need reasoning.
Complex queries benefit from LLM navigation of the tree structure."""


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


# Titles to skip in reasoning overview (noise/boilerplate)
_NOISE_TITLES = {
    "table of contents", "index", "signatures", "power of attorney",
    "exhibit index", "certifications",
}


def _is_noise_node(node: dict) -> bool:
    """True if node title is generic boilerplate that won't help reasoning."""
    title = node.get("title", "").strip().lower()
    return title in _NOISE_TITLES


def _make_snippet(text: str, query_terms: list[str]) -> str:
    """Create a text snippet around the first keyword match."""
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
    return snippet


def _keyword_search(all_nodes: list[dict], query_terms: list[str], max_results: int) -> list[dict]:
    """Pure keyword search over flattened nodes."""
    scored = []
    for node in all_nodes:
        score = _score_node(node, query_terms)
        if score > 0:
            text = node["text"] or ""
            snippet = _make_snippet(text, query_terms)
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


def search_trees(query: str, max_results: int = 10, doc_id: str | None = None) -> list[dict]:
    """Search across all indexed tree nodes by keyword, with semantic fallback.

    Runs keyword search first. If the top keyword score is below
    SEMANTIC_FALLBACK_THRESHOLD and semantic search is enabled,
    blends in embedding-based results for better recall.

    Returns list of dicts with: doc_name, node_path, title, summary,
    text_snippet, score.
    """
    query_terms = [t for t in re.split(r'\s+', query.strip()) if t]
    if not query_terms:
        return []

    if doc_id:
        record = tree_store.load_tree(doc_id)
        all_records = [record] if record else []
    else:
        all_records = tree_store.load_all_trees()

    all_nodes = []
    for record in all_records:
        tree = record.get("tree", {})
        structure = tree.get("structure", [])
        d_id = record.get("doc_id", "")
        d_name = tree.get("doc_name", record.get("source_file", ""))
        all_nodes.extend(_flatten_nodes(structure, doc_id=d_id, doc_name=d_name))

    keyword_results = _keyword_search(all_nodes, query_terms, max_results)

    top_keyword_score = keyword_results[0]["score"] if keyword_results else 0

    if top_keyword_score >= SEMANTIC_FALLBACK_THRESHOLD:
        return keyword_results

    semantic_results = _try_semantic_search(query, all_records, max_results)

    if not semantic_results:
        return keyword_results

    return _blend_results(keyword_results, semantic_results, max_results)


def _try_semantic_search(query: str, records: list[dict], max_results: int) -> list[dict]:
    """Attempt semantic search. Returns empty list if unavailable or disabled."""
    try:
        from . import embeddings
    except ImportError:
        return []

    if not embeddings.is_enabled():
        return []

    has_embeddings = any(r.get("embeddings") for r in records)
    if not has_embeddings:
        return []

    try:
        return embeddings.semantic_search(query, records, max_results=max_results)
    except Exception as e:
        logger.debug(f"Semantic search failed (non-fatal): {e}")
        return []


def _build_tree_overview_for_llm(structure, max_depth=3, current_depth=0) -> str:
    """Build a condensed tree overview for LLM reasoning.
    
    Includes node_id, title, and summary for navigation decisions.
    Limits depth to keep context manageable.
    """
    lines = []
    if isinstance(structure, dict):
        structure = [structure]
    if not isinstance(structure, list):
        return ""
    
    for node in structure:
        if _is_noise_node(node):
            continue
        node_id = node.get("node_id", "")
        title = node.get("title", "Untitled")
        summary = node.get("summary", node.get("prefix_summary", ""))

        indent = "  " * current_depth
        line = f"{indent}[{node_id}] {title}"
        if summary and len(summary) > 20:
            short_summary = summary[:200].replace('\n', ' ')
            if len(summary) > 200:
                short_summary += "..."
            line += f" - {short_summary}"
        lines.append(line)

        # Recurse into children if within depth limit
        if "nodes" in node and current_depth < max_depth:
            child_lines = _build_tree_overview_for_llm(node["nodes"], max_depth, current_depth + 1)
            if child_lines:
                lines.append(child_lines)
    
    return "\n".join(lines)


def _reasoning_based_search(
    query: str,
    record: dict,
    max_results: int = 5
) -> list[dict]:
    """Use LLM to reason over tree structure and select relevant nodes.
    
    This is the core PageIndex approach:
    1. Present document tree (with summaries) to LLM
    2. Ask LLM to identify most relevant node_ids for the query
    3. Return full nodes for selected IDs
    
    Args:
        query: User's search query
        record: Tree record from tree_store
        max_results: Maximum nodes to return
    
    Returns:
        List of node dicts selected by LLM reasoning
    """
    tree = record.get("tree", {})
    structure = tree.get("structure", [])
    doc_id = record.get("doc_id", "")
    doc_name = tree.get("doc_name", record.get("source_file", ""))
    
    if not structure:
        return []
    
    # Build condensed tree overview
    tree_overview = _build_tree_overview_for_llm(structure, max_depth=3)
    
    # Construct prompt for LLM reasoning
    prompt = f"""You are an expert document analysis system. Your task is to identify which sections of a document are most relevant to a user's query.

USER QUERY: "{query}"

DOCUMENT STRUCTURE (tree format):
{tree_overview}

INSTRUCTIONS:
1. Analyze the query to understand what information is needed
2. Review the document tree structure above
3. Identify the most relevant node_ids that likely contain the answer
4. Return ONLY a comma-separated list of node_ids (e.g., "0005, 0012, 0023")
5. Select at most {max_results} nodes, prioritizing the most relevant
6. If no sections seem relevant, return "NONE"

RELEVANT NODE_IDS:"""

    try:
        # Call LLM for reasoning
        response = llm.llm_call(prompt=prompt)
        response = response.strip()
        
        if response.upper() == "NONE" or not response:
            return []
        
        # Parse node_ids from response
        selected_ids = [nid.strip() for nid in response.split(",") if nid.strip()]
        
        # Fetch full nodes for selected IDs
        all_nodes = _flatten_nodes(structure, doc_id=doc_id, doc_name=doc_name)
        node_map = {n["node_id"]: n for n in all_nodes}
        
        results = []
        for node_id in selected_ids[:max_results]:
            if node_id in node_map:
                node = node_map[node_id]
                text = node["text"] or ""
                snippet = text[:300] + ("..." if len(text) > 300 else "")
                results.append({
                    "doc_id": node["doc_id"],
                    "doc_name": node["doc_name"],
                    "node_id": node["node_id"],
                    "node_path": node["node_path"],
                    "title": node["title"],
                    "summary": node["summary"],
                    "text_snippet": snippet,
                    "score": 10,  # High score to indicate LLM-selected
                    "selected_by": "llm_reasoning",
                })
        
        logger.info(f"LLM reasoning selected {len(results)} nodes for query: {query[:50]}...")
        return results
        
    except Exception as e:
        logger.warning(f"LLM reasoning search failed: {e}")
        return []


def search_trees(
    query: str,
    max_results: int = 10,
    doc_id: str | None = None,
    use_reasoning: bool = True
) -> list[dict]:
    """Search across indexed tree nodes using PageIndex approach.
    
    Strategy:
      1. Run keyword search first (fast baseline)
      2. If keyword results are weak (max score below threshold) AND use_reasoning=True,
         invoke LLM to reason over tree structure
      3. Return best results from either approach
    
    Args:
        query: Search query
        max_results: Maximum results to return
        doc_id: Optional document ID to search within
        use_reasoning: Whether to use LLM reasoning for complex queries
    
    Returns:
        List of result dicts with node info and text snippets
    """
    query_terms = [t for t in re.split(r'\s+', query.strip()) if t]
    if not query_terms:
        return []

    if doc_id:
        record = tree_store.load_tree(doc_id)
        all_records = [record] if record else []
    else:
        record = None
        all_records = tree_store.load_all_trees()

    all_nodes = []
    for rec in all_records:
        tree = rec.get("tree", {})
        structure = tree.get("structure", [])
        d_id = rec.get("doc_id", "")
        d_name = tree.get("doc_name", rec.get("source_file", ""))
        all_nodes.extend(_flatten_nodes(structure, doc_id=d_id, doc_name=d_name))

    # Step 1: Keyword search (always run - fast baseline)
    keyword_results = _keyword_search(all_nodes, query_terms, max_results)
    top_keyword_score = keyword_results[0]["score"] if keyword_results else 0
    
    # Step 2: If keyword results are strong, return them
    if top_keyword_score >= REASONING_THRESHOLD:
        logger.debug(f"Keyword search sufficient (score={top_keyword_score}), skipping reasoning")
        return keyword_results
    
    # Step 3: If reasoning enabled and we have a specific document, use LLM
    if use_reasoning and record and top_keyword_score < REASONING_THRESHOLD:
        logger.info(f"Keyword search weak (score={top_keyword_score}), invoking LLM reasoning")
        reasoning_results = _reasoning_based_search(query, record, max_results)
        
        if reasoning_results:
            # Blend: reasoning results first, then keyword results as fallback
            seen = {(r["doc_id"], r["node_id"]) for r in reasoning_results}
            for kr in keyword_results:
                key = (kr["doc_id"], kr["node_id"])
                if key not in seen and len(reasoning_results) < max_results:
                    reasoning_results.append(kr)
            return reasoning_results
    
    # Fall back to keyword results
    return keyword_results


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
    if tree.get("index_mode") == "raw":
        lines.append("[Raw-stored filing — full text in single node, no hierarchical tree]")
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
            if _is_noise_node(node):
                line += " (boilerplate)"
            elif summary:
                short = summary[:120] + ("..." if len(summary) > 120 else "")
                line += f" — {short}"
            lines.append(line)
            if "nodes" in node:
                _walk(node["nodes"], indent + 1)

    _walk(structure)
    return "\n".join(lines)
