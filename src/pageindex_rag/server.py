"""MCP server exposing document RAG tools to Claude Desktop.

Indexing operations (fetch + index, ingest) run in a background thread to
avoid MCP tool-call timeouts. Claude polls check_indexing_status() to know
when documents are ready for analysis.
"""

import logging
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from mcp.server.fastmcp import FastMCP

from . import tree_store, tree_search, indexer, llm
from .parsers import PARSERS
from . import sec_fetcher
from . import enhanced_sec_fetcher

# All logging to stderr (stdout is MCP protocol channel)
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("pageindex-rag")

ROOT = Path(__file__).resolve().parent.parent.parent
DROP_DIR = ROOT / "data" / "drop"
PROCESSED_DIR = ROOT / "data" / "processed"

try:
    from trading_core.transport import configure_transport
    mcp = FastMCP(**configure_transport("pageindex-rag"))
except ImportError:
    mcp = FastMCP(name="pageindex-rag")

# Thread pool for indexing (PageIndex calls asyncio.run() internally)
_executor = ThreadPoolExecutor(max_workers=1)


# ── Background indexing job tracker ──────────────────────────────────────────

class _IndexingJob:
    """Tracks the state of a single file being indexed in the background."""
    __slots__ = ("filename", "filepath", "status", "doc_id", "error", "started_at", "finished_at")

    def __init__(self, filename: str, filepath: Path):
        self.filename = filename
        self.filepath = filepath
        self.status = "queued"          # queued -> indexing -> done | failed
        self.doc_id: str | None = None
        self.error: str | None = None
        self.started_at: float = time.time()
        self.finished_at: float | None = None


class _IndexingTracker:
    """Thread-safe tracker for background indexing jobs.

    Each fetch or ingest operation creates a "batch" (a string key) containing
    one or more jobs. Claude polls check_indexing_status() to see progress.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._batches: dict[str, list[_IndexingJob]] = {}
        self._counter = 0

    def create_batch(self, files: list[tuple[str, Path]]) -> str:
        """Register a new batch of files to index. Returns a batch_id."""
        with self._lock:
            self._counter += 1
            batch_id = f"batch_{self._counter}_{int(time.time())}"
            jobs = [_IndexingJob(name, path) for name, path in files]
            self._batches[batch_id] = jobs
        return batch_id

    def get_job(self, batch_id: str, filename: str) -> _IndexingJob | None:
        with self._lock:
            for job in self._batches.get(batch_id, []):
                if job.filename == filename:
                    return job
        return None

    def get_status(self, batch_id: str | None = None) -> dict | None:
        """Get status summary for a batch or all active batches.

        Returns None if a specific batch_id is requested but not found.
        Returns an empty dict if no batches exist and batch_id is None.
        """
        with self._lock:
            if batch_id:
                if batch_id in self._batches:
                    return self._summarize_batch(batch_id, self._batches[batch_id])
                return None

            all_status = {}
            for bid, jobs in self._batches.items():
                all_status[bid] = self._summarize_batch(bid, jobs)
            return all_status

    def _summarize_batch(self, batch_id: str, jobs: list[_IndexingJob]) -> dict:
        total = len(jobs)
        done = sum(1 for j in jobs if j.status == "done")
        failed = sum(1 for j in jobs if j.status == "failed")
        indexing = sum(1 for j in jobs if j.status == "indexing")
        queued = sum(1 for j in jobs if j.status == "queued")
        elapsed = time.time() - jobs[0].started_at if jobs else 0
        return {
            "batch_id": batch_id,
            "total": total,
            "done": done,
            "failed": failed,
            "indexing": indexing,
            "queued": queued,
            "complete": done + failed == total,
            "elapsed_seconds": round(elapsed, 1),
            "jobs": [
                {
                    "filename": j.filename,
                    "status": j.status,
                    "doc_id": j.doc_id,
                    "error": j.error,
                }
                for j in jobs
            ],
        }

    def cleanup_old(self, max_age_seconds: float = 3600):
        """Remove completed batches older than max_age_seconds."""
        now = time.time()
        with self._lock:
            to_remove = []
            for bid, jobs in self._batches.items():
                if all(j.status in ("done", "failed") for j in jobs):
                    if jobs and jobs[0].finished_at and (now - jobs[0].finished_at) > max_age_seconds:
                        to_remove.append(bid)
            for bid in to_remove:
                del self._batches[bid]


_tracker = _IndexingTracker()


def _index_file_background(job: _IndexingJob):
    """Index a single file in the background thread, updating job status."""
    job.status = "indexing"
    try:
        doc_id = indexer.index_document(job.filepath)
        job.doc_id = doc_id
        job.status = "done"

        # Move to processed
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        dest = PROCESSED_DIR / job.filepath.name
        if dest.exists():
            dest = PROCESSED_DIR / f"{job.filepath.stem}_{id(job.filepath)}{job.filepath.suffix}"
        shutil.move(str(job.filepath), str(dest))

    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        logger.error(f"Background indexing failed for {job.filename}: {e}")
    finally:
        job.finished_at = time.time()


def _run_batch_indexing(batch_id: str):
    """Process all jobs in a batch sequentially in the background thread."""
    jobs = _tracker._batches.get(batch_id, [])
    for job in jobs:
        _index_file_background(job)
    logger.info(f"Batch {batch_id} complete: "
                f"{sum(1 for j in jobs if j.status == 'done')}/{len(jobs)} succeeded")


# ── Guide tool ────────────────────────────────────────────────────────────────


@mcp.tool()
def get_rag_guide() -> dict:
    """Get orientation guide for the PageIndex RAG MCP server.

    Call this FIRST before using any other sec-rag tools.
    Returns the full tool catalog, workflows, and SEC filing domain knowledge.
    """
    return {
        "server": "pageindex-rag",
        "purpose": "Vectorless, reasoning-based RAG for SEC EDGAR filings and long documents",
        "approach": "Hierarchical tree structure + LLM reasoning (98.7% accuracy on FinanceBench)",
        "workflow": [
            "1. check_company_indexed(ticker) — ALWAYS call first",
            "2. fetch_company_filings(ticker, forms, max_filings) — download + index (background)",
            "3. check_indexing_status(batch_id) — poll until COMPLETE",
            "4. get_document_overview(doc_id) — understand filing structure",
            "5. search_with_citations(query, doc_id) — find relevant sections",
            "6. get_document_section(doc_id, node_id) — read full raw text",
            "7. Synthesize and cite sources",
        ],
        "tools": {
            "lifecycle": {
                "check_company_indexed": "Check if filings exist for a ticker",
                "fetch_company_filings": "Download + index from SEC EDGAR (returns immediately)",
                "check_indexing_status": "Poll background indexing progress",
                "ingest_drop_folder": "Index manually placed files in data/drop/",
                "remove_document": "Delete an indexed document",
            },
            "search": {
                "search_with_citations": "Primary search — keyword + LLM reasoning fallback",
                "get_document_overview": "Table of contents for a document",
                "get_document_section": "Full raw text of a specific section",
                "batch_query": "Same question across multiple documents",
            },
            "utility": {
                "list_documents": "List all indexed documents with doc_ids",
                "embed_documents": "Generate semantic embeddings (optional)",
            },
        },
        "filing_types": {
            "10-K": "Annual report (Business, Risk Factors, MD&A, Financial Statements)",
            "10-Q": "Quarterly report (Financial Statements, MD&A, Risk Factors)",
            "8-K": "Current report (material events)",
            "DEF 14A": "Proxy statement (exec comp, board, shareholder proposals)",
            "S-1": "IPO registration (risk factors, use of proceeds)",
        },
        "rules": [
            "ALWAYS check_company_indexed() before fetch_company_filings()",
            "Use specific form filters (forms='10-K') — never fetch everything",
            "Keep max_filings small (2-5)",
            "Cite sources with doc_id and node_id",
            "Raw text is ALWAYS the answer source — never summaries",
            "If rate limited, wait 10 minutes",
        ],
    }


# ── Search tools ──────────────────────────────────────────────────────────────


@mcp.tool()
def get_document_section(doc_id: str, node_id: str) -> str:
    """Get the FULL RAW TEXT of a specific section/node in a document.

    USE WHEN: You found a relevant section via search or overview and need
    the COMPLETE TEXT for analysis. This always returns the full raw content,
    never just summaries.

    IMPORTANT: This implements the PageIndex philosophy - summaries are used
    for NAVIGATION ONLY, but answers must come from RAW TEXT for accuracy.

    Args:
        doc_id: The document ID (e.g. "CAT_10-K_20240216_d03268e6").
                Get this from list_documents(), search results, or fetch output.
        node_id: The node ID within the document (e.g. "0001", "0015").
                 Get this from get_document_overview() or search results.

    Returns:
        Section title and FULL RAW TEXT content. Summary shown only as context.

    Example:
        get_document_section("CAT_10-K_20240216_d03268e6", "0015")
    """
    record = tree_store.load_tree(doc_id)
    if not record:
        return f"Document '{doc_id}' not found."

    tree = record.get("tree", {})
    structure = tree.get("structure", [])

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
    
    # Show summary only as context (PageIndex: summaries for navigation only)
    if summary and summary != text:
        parts.append(f"\n> **Navigation Summary:** {summary[:200]}...")
        parts.append("> (Summary helps find sections; answers must come from full text below)")
    
    # ALWAYS return full raw text (PageIndex principle)
    if text:
        parts.append(f"\n---\n\n{text}")
    else:
        parts.append("\n(No text content available for this node)")

    return "\n".join(parts)


@mcp.tool()
def get_document_overview(doc_id: str) -> str:
    """Get a table-of-contents overview of a document's structure.

    USE WHEN: You've just indexed a filing and need to understand its structure
    before searching or reading sections. This is typically the FIRST tool to
    call after fetching/indexing, and helps you identify the right node_ids
    for subsequent get_document_section calls.

    TYPICAL NEXT STEPS after calling this:
    - search_with_citations() to find specific content within the filing
    - get_document_section() to read a section you identified from the TOC

    Args:
        doc_id: The document ID. Get this from list_documents(),
                check_company_indexed(), or fetch_company_filings() output.

    Returns:
        Hierarchical listing of all sections with node_ids and summaries.
        The indentation shows parent-child relationships between sections.

    Example:
        get_document_overview("CAT_10-K_20240216_d03268e6")
    """
    return tree_search.get_document_overview(doc_id)


@mcp.tool()
def search_with_citations(query: str, doc_id: str = "", max_results: int = 5) -> str:
    """Search documents using PageIndex reasoning-based retrieval.

    USE WHEN: The user needs verifiable references, you plan to cite sources,
    or you need precise doc_id + node_id pairs for follow-up reads.
    This is the primary search tool for all analysis workflows.

    SEARCH METHOD:
    1. Keyword search runs first (fast baseline)
    2. If results are weak, LLM reasoning navigates the document tree
    3. Returns most relevant sections based on structure + content

    This is a VECTORLESS approach - no embeddings, no vector DB.
    Follows the PageIndex framework (98.7% accuracy on FinanceBench).

    Args:
        query: Search query using key terms (e.g. "revenue segment breakdown",
               "risk factors climate", "executive compensation equity awards").
        doc_id: Optional document ID to restrict search to one filing.
                Omit to search ALL indexed documents.
        max_results: Maximum results to return (default 5, max 20).

    Returns:
        Numbered results with full citation info. Includes instructions
        for retrieving full section text via get_document_section().

    Example:
        search_with_citations("supply chain risks")
        search_with_citations("revenue by geography", doc_id="AAPL_10-K_20241101_abc123")
    """
    results = tree_search.search_trees(query, max_results=max_results, doc_id=doc_id or None, use_reasoning=True)
    if not results:
        return "No matching results found."

    parts = [f"**Search Results for: '{query}'**\n"]
    
    for i, r in enumerate(results, 1):
        citation = f"""
[{i}] **{r['doc_name']}**
    **Source:** `{r['doc_id']}` / node `{r.get('node_id', 'N/A')}`
    **Path:** {r['node_path']}
    **Relevance Score:** {r['score']}
"""
        parts.append(citation)
        
        if r.get('summary'):
            parts.append(f"    **Summary:** {r['summary'][:200]}...")
        
        if r.get('text_snippet'):
            parts.append(f"    **Excerpt:** \"{r['text_snippet'][:300]}...\"")
        
        parts.append("")
    
    parts.append("---")
    parts.append(f"*To retrieve full section text, use: get_document_section(doc_id, node_id)*")
    
    return "\n".join(parts)


@mcp.tool()
def batch_query(query: str, doc_ids: str = "") -> str:
    """Ask the same question across multiple documents simultaneously.

    USE WHEN:
    - Tracking a topic across quarterly filings (e.g. "How has guidance
      changed over the last 4 quarters?")
    - Comparing how multiple companies discuss the same issue
      (e.g. "What do AAPL, MSFT, and GOOGL say about AI?")
    - Finding which filings are most relevant for a broad question

    Args:
        query: The question or search terms to apply across all target documents.
        doc_ids: Comma-separated list of document IDs to search.
                 Use "all" or leave blank to search ALL indexed documents.
                 Example: "CAT_10-K_20230215_f79c418b,CAT_10-K_20240216_d03268e6"

    Returns:
        Per-document results showing match count and top match,
        plus a summary of total matches across all documents.

    Example:
        batch_query("supply chain disruption", doc_ids="all")
        batch_query("revenue guidance", doc_ids="CAT_10-Q_20240807_ea39b25f,CAT_10-Q_20241106_c772305d")
    """
    if doc_ids.lower() == "all" or not doc_ids.strip():
        all_docs = tree_store.list_trees()
        target_docs = [d["doc_id"] for d in all_docs]
    else:
        target_docs = [d.strip() for d in doc_ids.split(",")]
    
    if not target_docs:
        return "No documents specified or available."
    
    parts = [f"**Batch Query: '{query}'**\n"]
    parts.append(f"Searching across {len(target_docs)} document(s)...\n")
    
    results_by_doc = {}
    
    for doc_id in target_docs:
        record = tree_store.load_tree(doc_id)
        if not record:
            results_by_doc[doc_id] = {"error": "Document not found"}
            continue
        
        doc_name = record.get("tree", {}).get("doc_name", doc_id)
        
        search_results = tree_search.search_trees(query, max_results=3, doc_id=doc_id)
        
        results_by_doc[doc_id] = {
            "name": doc_name,
            "results": search_results,
            "match_count": len(search_results)
        }
    
    for doc_id, data in results_by_doc.items():
        if "error" in data:
            parts.append(f"**{doc_id}:** {data['error']}")
            continue
        
        parts.append(f"**{data['name']}** (`{doc_id}`)")
        parts.append(f"  Matches: {data['match_count']}")
        
        if data['results']:
            best_match = data['results'][0]
            parts.append(f"  Top match: {best_match['node_path']}")
            if best_match.get('summary'):
                parts.append(f"  Summary: {best_match['summary'][:150]}...")
        
        parts.append("")
    
    total_matches = sum(d.get('match_count', 0) for d in results_by_doc.values() if 'error' not in d)
    docs_with_matches = sum(1 for d in results_by_doc.values() if d.get('match_count', 0) > 0)
    
    parts.append("---")
    parts.append(f"**Summary:** {total_matches} total matches across {docs_with_matches}/{len(target_docs)} documents")
    parts.append(f"*To explore a specific result, use: get_document_section(doc_id, node_id)*")
    
    return "\n".join(parts)


@mcp.tool()
def check_company_indexed(ticker: str) -> str:
    """Check if we have any SEC filings indexed for a company.

    CALL THIS FIRST — before fetch_company_filings() — every time.
    This avoids redundant downloads and protects against SEC rate limits.

    If the company is already indexed, you can proceed directly to analysis
    using the returned doc_ids. Only fetch if filings are missing.

    Args:
        ticker: Company ticker symbol (e.g. "AAPL", "CAT", "MSFT").
                Case-insensitive.

    Returns:
        List of indexed doc_ids if found, or a message indicating no filings.

    Example workflow:
        1. check_company_indexed("AAPL") -> "AAPL has 3 filing(s) indexed"
        2. If not indexed: fetch_company_filings("AAPL", forms="10-K")
        3. Proceed with search/analysis
    """
    result = sec_fetcher.check_ticker_indexed(ticker)
    
    if result["indexed"]:
        lines = [
            f"**{ticker.upper()}** has {result['count']} filing(s) indexed:",
            ""
        ]
        for doc_id in result["doc_ids"]:
            lines.append(f"- `{doc_id}`")
        lines.append("")
        lines.append("Ready for analysis.")
        return "\n".join(lines)
    else:
        return f"**{ticker.upper()}** has no filings indexed.\n\nUse `fetch_company_filings` to pull SEC filings."


@mcp.tool()
def fetch_company_filings(
    ticker: str,
    forms: str = "",
    max_filings: int = 5,
    auto_index: bool = True
) -> str:
    """Fetch SEC filings for a company from EDGAR and index them for analysis.

    PREREQUISITE: Always call check_company_indexed() first to avoid
    redundant downloads.

    Downloads filings from SEC EDGAR. Indexing runs in the BACKGROUND to
    avoid timeouts — this tool returns immediately after downloading.

    AFTER CALLING THIS: Call check_indexing_status() to monitor progress.
    Once indexing is complete, all analysis tools work on the new filings.

    Args:
        ticker: Company ticker symbol (e.g. "AAPL", "CAT").
        forms: Comma-separated form types to fetch. Common values:
               "10-K" — Annual report (most comprehensive)
               "10-Q" — Quarterly report
               "8-K" — Current report (material events)
               "10-K,10-Q" — Both annual and quarterly
               "" (blank) — All available types (NOT recommended)
        max_filings: Maximum filings to fetch (default 5). Use 2-3 for
                     focused analysis, 5 for historical coverage.
        auto_index: Whether to start indexing automatically (default true).
                    Set false to only download without indexing.

    Returns:
        Confirmation of download with a batch_id for tracking indexing progress.

    Example workflow:
        1. fetch_company_filings("AAPL", forms="10-K", max_filings=2)
           -> "Downloaded 2 files. Indexing in background (batch_1_...)."
        2. check_indexing_status()
           -> "1/2 done, 1 indexing..."
        3. check_indexing_status()
           -> "2/2 done. Ready for analysis."
    """
    form_list = [f.strip() for f in forms.split(",") if f.strip()] if forms else None

    result = sec_fetcher.fetch_company_filings(ticker, form_list, max_filings)

    if result.get("rate_limited"):
        error_msg = "\n".join(result["errors"])
        return (f"**SEC Rate Limit Exceeded**\n\n{error_msg}\n\n"
                f"**What you can do:**\n"
                f"1. Wait 10 minutes and try again\n"
                f"2. Use already-indexed documents if available\n"
                f"3. Check `check_company_indexed('{ticker}')` for existing filings")

    if not result["success"]:
        error_msg = "\n".join(result["errors"])
        return f"Failed to fetch filings for {ticker}:\n{error_msg}"

    lines = [
        f"**Downloaded {len(result['files'])} filing(s) for {result['company']} ({ticker.upper()})**",
        ""
    ]

    for path in result["files"]:
        lines.append(f"- {path.name}")

    if result["errors"]:
        lines.append("")
        lines.append("**Warnings:**")
        for err in result["errors"]:
            lines.append(f"- {err}")

    if auto_index and result["files"]:
        file_pairs = [(p.name, p) for p in result["files"]]
        batch_id = _tracker.create_batch(file_pairs)

        _executor.submit(_run_batch_indexing, batch_id)

        lines.append("")
        lines.append(f"**Indexing {len(result['files'])} file(s) in the background.**")
        lines.append(f"Batch ID: `{batch_id}`")
        lines.append("")
        lines.append("Call `check_indexing_status()` to monitor progress.")
        lines.append("Once complete, filings are ready for search and analysis.")
    else:
        lines.append("")
        lines.append("Files downloaded to data/drop/. Use `ingest_drop_folder` to index.")

    return "\n".join(lines)


@mcp.tool()
def check_filings_available(ticker: str, forms: str = "") -> str:
    """Check available SEC filings WITHOUT downloading.
    
    This enhanced check shows you:
    - Total filings available on SEC EDGAR for this ticker
    - How many are already indexed locally
    - How many are new (available but not indexed)
    - Breakdown by form type
    
    USE THIS FIRST before fetching to understand what you're working with.
    This is the enhanced version of check_company_indexed().
    
    Args:
        ticker: Company ticker symbol (e.g., "AAPL", "CRWV")
        forms: Optional comma-separated form filter (e.g., "10-K,10-Q")
               Leave blank to see all form types.
    
    Returns:
        Summary of available vs indexed filings with form breakdown.
        
    Example:
        check_filings_available("CRWV")
        # Shows: 144 total available, 15 indexed, 129 new to fetch
    """
    return enhanced_sec_fetcher.check_filings_available(ticker, forms)


@mcp.tool()
def fetch_company_filings_enhanced(
    ticker: str,
    forms: str = "",
    batch_size: int = 10,
    auto_index: bool = True
) -> str:
    """Fetch SEC filings with pagination and smart rate limit handling.
    
    This enhanced fetcher addresses the limitations of the standard
    fetch_company_filings tool:
    
    1. **Pre-flight check**: Shows total available before downloading
    2. **Paginated fetching**: Downloads in small batches (default 10) to avoid rate limits
    3. **Resume support**: If rate limited, tells you exactly when you can resume
    4. **Progress tracking**: Shows batch progress (e.g., "Batch 3/15")
    5. **Smart deduplication**: Automatically skips already-indexed filings
    
    RECOMMENDED WORKFLOW:
        1. check_filings_available("CRWV")  # See what's available
        2. fetch_company_filings_enhanced("CRWV", batch_size=10)  # Fetch in batches
        3. If rate limited, wait the specified time and re-run (auto-resumes)
    
    Args:
        ticker: Company ticker symbol
        forms: Comma-separated form types (e.g., "10-K,10-Q,8-K")
               Leave blank for all forms.
        batch_size: Number of filings per batch (default 10). 
                    Lower = safer for rate limits. Max 20.
        auto_index: Automatically index downloaded filings (default True)
    
    Returns:
        Progress report with downloaded files and resume info if rate limited.
        
    Example:
        fetch_company_filings_enhanced("CRWV", batch_size=5)
        # Returns complete summary with all downloaded files
    """
    fetcher = enhanced_sec_fetcher.EnhancedSECFetcher()
    form_list = [f.strip() for f in forms.split(",") if f.strip()] if forms else None

    # Always download without synchronous indexing — we'll submit to
    # the background _executor/_tracker system below (same as fetch_company_filings).
    result = fetcher.fetch_all_with_resume(
        ticker=ticker,
        forms=form_list,
        batch_size=batch_size,
        auto_index=False,  # never index synchronously inside MCP call
    )

    lines = [
        f"**{result['ticker']}** Filing Fetch Results",
        "",
        f"Total available on SEC EDGAR: {result['total_available']}",
        f"Already indexed: {result['already_indexed']}",
        f"New filings to fetch: {result['new_filings']}",
        "",
    ]

    if result["rate_limited"]:
        lines.extend([
            f"**Rate Limit Hit**",
            f"Downloaded: {result['downloaded']} of {result['new_filings']} new filings",
            f"Batches: {result['batches_completed']}/{result['total_batches']}",
            "",
            f"**Resume in: {result['resume_after_minutes']} minutes**",
            "",
            f"Files downloaded so far: {len(result['files'])}",
        ])
        for f in result["files"]:
            lines.append(f"  - {f.name}")
    elif result["success"]:
        lines.extend([
            f"**Complete**",
            f"Downloaded: {result['downloaded']} new filings",
            "",
            "Files downloaded:",
        ])
        for f in result["files"]:
            lines.append(f"  - {f.name}")
    else:
        lines.extend([
            f"**Failed**",
            f"Message: {result['message']}",
        ])

    # Submit downloaded files to background indexing (same pattern as
    # fetch_company_filings) so the MCP call returns immediately.
    if auto_index and result["files"] and not result["rate_limited"]:
        file_pairs = [(p.name, p) for p in result["files"]]
        batch_id = _tracker.create_batch(file_pairs)
        _executor.submit(_run_batch_indexing, batch_id)

        lines.append("")
        lines.append(f"**Indexing {len(result['files'])} file(s) in the background.**")
        lines.append(f"Batch ID: `{batch_id}`")
        lines.append("")
        lines.append("Call `check_indexing_status()` to monitor progress.")

    return "\n".join(lines)


@mcp.tool()
def list_documents() -> str:
    """List all indexed documents in the RAG database.

    USE WHEN: You need to see what filings are currently available,
    find document IDs for other tool calls, or help the user choose
    which filing to analyze.

    Returns:
        List of all indexed documents with: document name, doc_id,
        node count, and description. Doc_ids are needed for all
        other tools that operate on specific documents.

    Example output:
        "CAT_10-K_20240216 (id: CAT_10-K_20240216_d03268e6, 45 nodes)"
    """
    docs = tree_store.list_trees()
    if not docs:
        return "No documents indexed yet. Drop files in data/drop/ and run ingest."

    lines = [f"**{len(docs)} document(s) indexed:**\n"]
    for doc in docs:
        desc = f" — {doc['doc_description']}" if doc.get("doc_description") else ""
        emb_tag = " [semantic]" if doc.get("has_embeddings") else ""
        raw_tag = " [raw]" if doc.get("index_mode") == "raw" else ""
        form_tag = f" ({doc['form_type']})" if doc.get("form_type") else ""
        lines.append(f"- **{doc['doc_name']}** (id: `{doc['doc_id']}`, {doc['node_count']} nodes{emb_tag}{raw_tag}{form_tag}){desc}")
    return "\n".join(lines)


# ── Ingestion tools ───────────────────────────────────────────────────────────


@mcp.tool()
def ingest_drop_folder() -> str:
    """Process and index any supported files in the data/drop/ folder.

    USE WHEN: Files have been manually placed in data/drop/ and need indexing.
    This is rarely needed — fetch_company_filings() auto-indexes by default.

    Supported file types: HTML, PDF, CSV, TXT, Markdown.
    Indexing runs in the BACKGROUND to avoid timeouts.

    AFTER CALLING THIS: Call check_indexing_status() to monitor progress.
    Files are moved to data/processed/ after successful indexing.

    Returns:
        Confirmation with batch_id for tracking, or message if no files found.
    """
    DROP_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    files = [f for f in sorted(DROP_DIR.iterdir())
             if f.is_file() and (f.suffix.lower() in PARSERS or f.suffix.lower() in (".md", ".markdown"))]
    if not files:
        return "No supported files found in the drop folder."

    file_pairs = [(f.name, f) for f in files]
    batch_id = _tracker.create_batch(file_pairs)

    _executor.submit(_run_batch_indexing, batch_id)

    lines = [
        f"**Indexing {len(files)} file(s) in the background.**",
        f"Batch ID: `{batch_id}`",
        "",
    ]
    for f in files:
        lines.append(f"- {f.name}")
    lines.append("")
    lines.append("Call `check_indexing_status()` to monitor progress.")

    return "\n".join(lines)


@mcp.tool()
def check_indexing_status(batch_id: str = "") -> str:
    """Check the status of background indexing jobs.

    CALL THIS AFTER: fetch_company_filings() or ingest_drop_folder() to
    monitor indexing progress. Keep calling until status shows "complete".

    Indexing a 10-K filing typically takes 30-120 seconds depending on size
    and LLM model speed. Smaller filings (Form 4, 8-K) take 5-15 seconds.

    Args:
        batch_id: Optional batch ID from a fetch/ingest call. If omitted,
                  shows status of ALL active indexing jobs.

    Returns:
        Progress report: how many files are done, in progress, or failed,
        plus doc_ids for completed files (ready for analysis).

    Example workflow:
        1. fetch_company_filings("AAPL", forms="10-K")
           -> batch_id: "batch_1_1740000000"
        2. check_indexing_status("batch_1_1740000000")
           -> "0/2 done, 1 indexing, 1 queued..."
        3. check_indexing_status("batch_1_1740000000")
           -> "2/2 done. Ready: AAPL_10-K_... , AAPL_10-K_..."
    """
    _tracker.cleanup_old()

    if batch_id:
        status = _tracker.get_status(batch_id)
        if not status:
            return f"Batch '{batch_id}' not found. It may have already completed and been cleaned up."
        if isinstance(status, dict) and "batch_id" in status:
            return _format_batch_status(status)

    all_status = _tracker.get_status()
    if not all_status:
        return "No active indexing jobs."

    parts = [f"**{len(all_status)} indexing batch(es):**\n"]
    for bid, status in all_status.items():
        parts.append(_format_batch_status(status))
        parts.append("")

    return "\n".join(parts)


def _format_batch_status(status: dict) -> str:
    """Format a single batch status into a readable string."""
    batch_id = status["batch_id"]
    total = status["total"]
    done = status["done"]
    failed = status["failed"]
    indexing = status["indexing"]
    queued = status["queued"]
    elapsed = status["elapsed_seconds"]
    complete = status["complete"]

    lines = [f"**Batch `{batch_id}`** — {elapsed:.0f}s elapsed"]

    if complete:
        lines.append(f"**Status: COMPLETE** ({done} done, {failed} failed)")
    else:
        parts = []
        if done:
            parts.append(f"{done} done")
        if indexing:
            parts.append(f"{indexing} indexing")
        if queued:
            parts.append(f"{queued} queued")
        if failed:
            parts.append(f"{failed} failed")
        lines.append(f"**Status: IN PROGRESS** — {', '.join(parts)} of {total} total")

    for job in status["jobs"]:
        if job["status"] == "done":
            lines.append(f"  - {job['filename']}: done -> `{job['doc_id']}`")
        elif job["status"] == "failed":
            lines.append(f"  - {job['filename']}: FAILED — {job['error']}")
        elif job["status"] == "indexing":
            lines.append(f"  - {job['filename']}: indexing...")
        else:
            lines.append(f"  - {job['filename']}: queued")

    if complete and done > 0:
        doc_ids = [j["doc_id"] for j in status["jobs"] if j["doc_id"]]
        lines.append("")
        lines.append("**Ready for analysis.** Use these doc_ids:")
        for did in doc_ids:
            lines.append(f"  - `{did}`")

    return "\n".join(lines)


@mcp.tool()
def remove_document(doc_id: str) -> str:
    """Remove an indexed document from the database.

    USE WHEN: The user wants to clean up, you need to re-index a filing,
    or you're done analyzing and want to free space.

    This is permanent — the document must be re-fetched and re-indexed
    to restore it.

    Args:
        doc_id: The document ID to remove (e.g. "CAT_10-K_20240216_d03268e6").

    Returns:
        Confirmation of removal, or error if document not found.
    """
    deleted = tree_store.delete_tree(doc_id)
    if deleted:
        return f"Removed document '{doc_id}'."
    return f"Document '{doc_id}' not found."


@mcp.tool()
def embed_documents(doc_ids: str = "all") -> str:
    """Generate semantic search embeddings for indexed documents.

    USE WHEN: Documents were indexed before semantic search was enabled,
    or you want to add/refresh embeddings for better search quality.
    New documents indexed with semantic_search enabled in config.json
    get embeddings automatically — this tool is for backfilling.

    Requires Ollama running with an embedding model (default: nomic-embed-text).
    Install it with: ollama pull nomic-embed-text

    Args:
        doc_ids: Comma-separated document IDs, or "all" to embed every
                 document that lacks embeddings.

    Returns:
        Status report showing which documents were embedded.

    Example:
        embed_documents("all")
        embed_documents("CAT_10-K_20240216_d03268e6,CAT_10-Q_20240807_ea39b25f")
    """
    try:
        from . import embeddings
    except ImportError:
        return "Embeddings module not available."

    if not embeddings.is_enabled():
        return ("Semantic search is disabled in config.json.\n"
                "Set \"semantic_search\": true to enable.")

    if doc_ids.lower() == "all" or not doc_ids.strip():
        all_docs = tree_store.list_trees()
        targets = [d["doc_id"] for d in all_docs if not d.get("has_embeddings")]
        if not targets:
            return "All documents already have embeddings."
    else:
        targets = [d.strip() for d in doc_ids.split(",")]

    results = []
    for doc_id in targets:
        record = tree_store.load_tree(doc_id)
        if not record:
            results.append(f"  SKIP {doc_id} — not found")
            continue

        if record.get("embeddings") and doc_ids.lower() == "all":
            results.append(f"  SKIP {doc_id} — already has embeddings")
            continue

        tree = record.get("tree", {})
        structure = tree.get("structure", [])

        try:
            node_embeddings = embeddings.generate_node_embeddings(structure)
            if node_embeddings:
                tree_store.save_embeddings(doc_id, node_embeddings)
                results.append(f"  OK {doc_id} — {len(node_embeddings)} nodes embedded")
            else:
                results.append(f"  FAIL {doc_id} — no embeddings generated")
        except Exception as e:
            results.append(f"  FAIL {doc_id} — {e}")

    embedded_count = sum(1 for r in results if r.strip().startswith("OK"))
    header = f"**Embedded {embedded_count}/{len(targets)} document(s):**\n"
    return header + "\n".join(results)


def main():
    try:
        from trading_core.transport import run_server
        run_server(mcp, "pageindex-rag")
    except ImportError:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
