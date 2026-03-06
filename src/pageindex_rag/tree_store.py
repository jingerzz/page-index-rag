"""JSON file-based storage for indexed document trees and embeddings.

Each document is stored as a single JSON file in data/indexes/ with the structure:
{
    "doc_id": "...",
    "source_file": "...",
    "metadata": {...},
    "tree": { "doc_name": "...", "structure": [...] },
    "embeddings": { "node_id": [float, ...], ... }  // optional
}

Embeddings are stored alongside the tree so no separate vector DB is needed.
They add ~50-200KB per document depending on node count.
"""

import hashlib
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger("pageindex-rag")

ROOT = Path(__file__).resolve().parent.parent.parent
INDEXES_DIR = ROOT / "data" / "indexes"


def _sanitize(name: str) -> str:
    """Sanitize a filename to be safe across OS."""
    name = re.sub(r'[^\w\s\-.]', '_', name)
    name = re.sub(r'\s+', '_', name)
    return name[:80]


def _make_doc_id(source_file: str) -> str:
    """Create a doc_id from source filename + 8-char MD5 hash."""
    stem = Path(source_file).stem
    h = hashlib.md5(source_file.encode()).hexdigest()[:8]
    return f"{_sanitize(stem)}_{h}"


def save_tree(
    source_file: str,
    tree_data: dict,
    metadata: dict | None = None,
    embeddings: dict[str, list[float]] | None = None,
) -> str:
    """Save a tree to disk. Returns doc_id.

    Args:
        source_file: Original filename.
        tree_data: The tree structure dict.
        metadata: Optional metadata dict.
        embeddings: Optional dict mapping node_id -> embedding vector.
    """
    INDEXES_DIR.mkdir(parents=True, exist_ok=True)
    doc_id = _make_doc_id(source_file)
    record = {
        "doc_id": doc_id,
        "source_file": source_file,
        "metadata": metadata or {},
        "tree": tree_data,
    }
    if embeddings:
        record["embeddings"] = embeddings

    path = INDEXES_DIR / f"{doc_id}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return doc_id


def save_embeddings(doc_id: str, embeddings: dict[str, list[float]]) -> bool:
    """Add or update embeddings for an existing document.

    This loads the document, adds/replaces the embeddings key, and saves.
    Useful for adding embeddings to documents that were indexed before
    semantic search was enabled.

    Args:
        doc_id: The document ID.
        embeddings: Dict mapping node_id -> embedding vector.

    Returns:
        True if successful, False if document not found.
    """
    path = INDEXES_DIR / f"{doc_id}.json"
    if not path.exists():
        return False

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["embeddings"] = embeddings
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        logger.error(f"Failed to save embeddings for {doc_id}: {e}")
        return False


def has_embeddings(doc_id: str) -> bool:
    """Check if a document has embeddings stored."""
    path = INDEXES_DIR / f"{doc_id}.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        emb = data.get("embeddings", {})
        return bool(emb)
    except Exception:
        return False


def load_tree(doc_id: str) -> dict | None:
    """Load a single tree by doc_id (includes embeddings if present)."""
    path = INDEXES_DIR / f"{doc_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_trees() -> list[dict]:
    """List all indexed documents (summary info only)."""
    INDEXES_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for path in sorted(INDEXES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            tree = data.get("tree", {})
            doc_name = tree.get("doc_name", data.get("source_file", path.stem))
            doc_desc = tree.get("doc_description", "")
            structure = tree.get("structure", [])
            node_count = _count_nodes(structure)
            has_emb = bool(data.get("embeddings"))
            index_mode = tree.get("index_mode", "tree")
            form_type = data.get("metadata", {}).get("form_type", "")
            results.append({
                "doc_id": data.get("doc_id", path.stem),
                "source_file": data.get("source_file", ""),
                "doc_name": doc_name,
                "doc_description": doc_desc,
                "node_count": node_count,
                "has_embeddings": has_emb,
                "index_mode": index_mode,
                "form_type": form_type,
            })
        except Exception:
            continue
    return results


def delete_tree(doc_id: str) -> bool:
    """Delete a tree by doc_id. Returns True if deleted."""
    path = INDEXES_DIR / f"{doc_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def load_all_trees() -> list[dict]:
    """Load all tree records (full data including embeddings)."""
    INDEXES_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for path in sorted(INDEXES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append(data)
        except Exception:
            continue
    return results


def _count_nodes(structure) -> int:
    """Recursively count nodes in a tree structure."""
    if isinstance(structure, dict):
        count = 1
        if 'nodes' in structure:
            count += _count_nodes(structure['nodes'])
        return count
    elif isinstance(structure, list):
        return sum(_count_nodes(item) for item in structure)
    return 0
