"""Local embedding generation and similarity search via Ollama.

Uses Ollama's OpenAI-compatible /v1/embeddings endpoint so no new
dependencies are needed beyond the existing openai SDK.

Embeddings are stored in the tree JSON files alongside the document
structure — no separate vector database required.
"""

import json
import logging
import math
from pathlib import Path

import openai

logger = logging.getLogger("pageindex-rag")

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config.json"

DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
EMBEDDING_DIMS = 768  # nomic-embed-text default
def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            return {}
    return {}


def _get_embedding_model() -> str:
    cfg = _load_config()
    return cfg.get("embedding_model", DEFAULT_EMBEDDING_MODEL)


def _get_ollama_base_url() -> str:
    cfg = _load_config()
    return cfg.get("ollama_base_url", "http://localhost:11434/v1")


def _get_client() -> openai.OpenAI:
    return openai.OpenAI(base_url=_get_ollama_base_url(), api_key="ollama")


def is_enabled() -> bool:
    """Check if semantic search is enabled in config.

    Defaults to True. Set "semantic_search": false in config.json to disable.
    """
    cfg = _load_config()
    return cfg.get("semantic_search", True)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for a list of texts using the local Ollama model.

    Args:
        texts: List of strings to embed. Empty strings are handled gracefully.

    Returns:
        List of embedding vectors (list of floats), one per input text.
        Returns empty list if embedding fails entirely.
    """
    if not texts:
        return []

    clean = [t if t.strip() else " " for t in texts]

    try:
        client = _get_client()
        response = client.embeddings.create(
            model=_get_embedding_model(),
            input=clean,
        )
        return [item.embedding for item in response.data]
    except Exception as e:
        logger.warning(f"Embedding generation failed: {e}")
        return []


def embed_single(text: str) -> list[float]:
    """Generate embedding for a single text string."""
    results = embed_texts([text])
    return results[0] if results else []


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors. Pure Python, no numpy."""
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


def generate_node_embeddings(structure: list | dict) -> dict[str, list[float]]:
    """Generate embeddings for all nodes in a tree structure.

    Each node is embedded using its title + summary concatenated.
    This is fast and compact — we embed the "meaning" of each section,
    not the full text (which would be too long for most embedding models).

    Args:
        structure: The tree structure (list of nodes or single node dict).

    Returns:
        Dict mapping node_id -> embedding vector.
    """
    nodes = _collect_nodes(structure)

    if not nodes:
        return {}

    # Cap embedding input to ~1500 chars to keep batch payloads manageable.
    # The stored summary may be full raw text (up to summary_token_threshold tokens)
    # when nodes are below the LLM threshold. First 1500 chars is sufficient
    # for semantic meaning and keeps each batch well within API limits.
    MAX_EMBED_CHARS = 1500

    texts = []
    node_ids = []
    for node_id, title, summary in nodes:
        text = summary[:MAX_EMBED_CHARS] if summary and len(summary) > MAX_EMBED_CHARS else summary
        combined = f"{title}\n{text}" if text else title
        texts.append(combined)
        node_ids.append(node_id)

    BATCH_SIZE = 64
    all_embeddings = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        batch_embeddings = embed_texts(batch)
        if not batch_embeddings:
            logger.warning(f"Embedding batch {i // BATCH_SIZE} failed, skipping")
            all_embeddings.extend([[] for _ in batch])
        else:
            all_embeddings.extend(batch_embeddings)

    result = {}
    for node_id, embedding in zip(node_ids, all_embeddings):
        if embedding:
            result[node_id] = embedding

    logger.info(f"Generated embeddings for {len(result)}/{len(nodes)} nodes")
    return result


def _collect_nodes(structure) -> list[tuple[str, str, str]]:
    """Flatten tree to list of (node_id, title, summary) tuples."""
    results = []
    if isinstance(structure, dict):
        node_id = structure.get("node_id", "")
        title = structure.get("title", "")
        summary = structure.get("summary", structure.get("prefix_summary", ""))
        if node_id:
            results.append((node_id, title, summary))
        if "nodes" in structure:
            results.extend(_collect_nodes(structure["nodes"]))
    elif isinstance(structure, list):
        for item in structure:
            results.extend(_collect_nodes(item))
    return results


def semantic_search(
    query: str,
    records: list[dict],
    max_results: int = 10,
    min_similarity: float = 0.3,
) -> list[dict]:
    """Search across documents using embedding similarity.

    Args:
        query: The search query string.
        records: List of tree records (from tree_store.load_all_trees or similar).
        max_results: Maximum results to return.
        min_similarity: Minimum cosine similarity threshold (0-1).

    Returns:
        Ranked list of result dicts matching the search_trees format:
        {doc_id, doc_name, node_id, node_path, title, summary, text_snippet, score}
    """
    query_embedding = embed_single(query)
    if not query_embedding:
        return []

    scored = []

    for record in records:
        doc_id = record.get("doc_id", "")
        tree = record.get("tree", {})
        doc_name = tree.get("doc_name", record.get("source_file", ""))
        structure = tree.get("structure", [])
        embeddings = record.get("embeddings", {})

        if not embeddings:
            continue

        flat_nodes = _flatten_for_search(structure, doc_id=doc_id, doc_name=doc_name)

        for node in flat_nodes:
            node_embedding = embeddings.get(node["node_id"])
            if not node_embedding:
                continue

            similarity = cosine_similarity(query_embedding, node_embedding)
            if similarity >= min_similarity:
                text = node.get("text", "")
                snippet = text[:300] + ("..." if len(text) > 300 else "") if text else ""

                scored.append({
                    "doc_id": node["doc_id"],
                    "doc_name": node["doc_name"],
                    "node_id": node["node_id"],
                    "node_path": node["node_path"],
                    "title": node["title"],
                    "summary": node["summary"],
                    "text_snippet": snippet,
                    "score": round(similarity * 10, 2),
                    "similarity": round(similarity, 4),
                })

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:max_results]


def _flatten_for_search(structure, path="", doc_id="", doc_name="") -> list[dict]:
    """Flatten tree structure for search (same shape as tree_search._flatten_nodes)."""
    results = []
    if isinstance(structure, dict):
        title = structure.get("title", "")
        current_path = f"{path}/{title}" if path else title
        results.append({
            "doc_id": doc_id,
            "doc_name": doc_name,
            "node_id": structure.get("node_id", ""),
            "node_path": current_path,
            "title": title,
            "summary": structure.get("summary", structure.get("prefix_summary", "")),
            "text": structure.get("text", ""),
        })
        if "nodes" in structure:
            results.extend(_flatten_for_search(structure["nodes"], current_path, doc_id, doc_name))
    elif isinstance(structure, list):
        for item in structure:
            results.extend(_flatten_for_search(item, path, doc_id, doc_name))
    return results
