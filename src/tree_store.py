"""JSON file-based storage for indexed document trees."""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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


def save_tree(source_file: str, tree_data: dict, metadata: dict | None = None) -> str:
    """Save a tree to disk. Returns doc_id."""
    INDEXES_DIR.mkdir(parents=True, exist_ok=True)
    doc_id = _make_doc_id(source_file)
    record = {
        "doc_id": doc_id,
        "source_file": source_file,
        "metadata": metadata or {},
        "tree": tree_data,
    }
    path = INDEXES_DIR / f"{doc_id}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return doc_id


def load_tree(doc_id: str) -> dict | None:
    """Load a single tree by doc_id."""
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
            results.append({
                "doc_id": data.get("doc_id", path.stem),
                "source_file": data.get("source_file", ""),
                "doc_name": doc_name,
                "doc_description": doc_desc,
                "node_count": node_count,
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
    """Load all tree records (full data)."""
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
