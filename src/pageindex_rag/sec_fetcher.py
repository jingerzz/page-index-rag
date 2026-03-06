"""Non-interactive SEC filing fetcher for MCP/agent use.

Extracts core fetching logic from fetch_sec.py without Rich console dependencies
for programmatic/agent use.

Rate limiting: SEC allows max 10 requests/second. We use 0.12s delay (~5.5-7 req/s)
plus jitter, with exponential backoff on 503 errors. This provides a safety
margin to avoid hitting SEC's rate limits, especially during high-traffic periods.

Connection pooling: Uses urllib3.PoolManager for HTTP connection reuse, avoiding
the ~50-200ms TCP+TLS overhead of opening a new connection per request.
"""

import json
import random
import time
from pathlib import Path

import urllib3
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config.json"
DROP_DIR = ROOT / "data" / "drop"
PROCESSED_DIR = ROOT / "data" / "processed"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# SEC rate limit: max 10 req/sec. Use 0.12s (~5.5-7 req/s) + jitter for safety
SEC_DELAY = 0.12
JITTER_RANGE = (0.02, 0.06)  # Add 20-60ms random jitter
MAX_RETRIES = 3
BACKOFF_BASE = 2.0  # Exponential backoff base seconds

# Cache for company ticker lookups (avoid repeated requests)
_company_cache: dict[str, dict] = {}
_cache_timestamp: float = 0
CACHE_TTL = 300  # Cache company list for 5 minutes

DEFAULT_USER_AGENT = "pageindex-rag/1.0 (SEC filings indexing)"

# Connection pool for HTTP reuse (lazy-initialized)
_http_pool: urllib3.PoolManager | None = None

# External rate control: when True, _rate_limited_sleep() is a no-op
# (the caller manages rate limiting instead of double-sleeping)
_external_rate_control: bool = False


class RateLimitError(Exception):
    """Raised when SEC rate limit is exceeded."""
    pass


class SECLookupError(Exception):
    """Raised when SEC lookup fails (ticker not found, etc.)."""
    pass


def set_external_rate_control(enabled: bool) -> None:
    """Enable/disable external rate control.

    When enabled, ``_rate_limited_sleep()`` becomes a no-op so that the
    caller (e.g. enhanced_sec_fetcher) can manage rate limiting without
    double-sleeping.
    """
    global _external_rate_control
    _external_rate_control = enabled


def _get_pool() -> urllib3.PoolManager:
    """Lazy-init and return the module-level HTTP connection pool."""
    global _http_pool
    if _http_pool is None:
        _http_pool = urllib3.PoolManager(
            num_pools=4,
            maxsize=10,
            retries=urllib3.Retry(
                connect=0, read=0, status=0,  # no auto-retries (we handle those)
                redirect=5,                    # follow redirects (SEC 301s strip CIK leading zeros)
            ),
        )
    return _http_pool


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {}


def _get_user_agent() -> str:
    cfg = _load_config()
    ua = (cfg.get("sec_user_agent") or "").strip()
    if ua:
        return ua
    return DEFAULT_USER_AGENT


def _read_json(url: str, user_agent: str | None = None, retry: bool = True) -> dict:
    """Read JSON with rate limit handling and exponential backoff."""
    ua = user_agent or _get_user_agent()
    pool = _get_pool()
    last_error = None
    for attempt in range(MAX_RETRIES if retry else 1):
        try:
            resp = pool.request("GET", url, headers={"User-Agent": ua}, timeout=30.0)
            if resp.status == 503:
                last_error = urllib3.exceptions.HTTPError(f"503 Service Unavailable: {url}")
                wait = BACKOFF_BASE ** (attempt + 1) + random.uniform(0, 1)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(wait)
                    continue
                raise RateLimitError(f"SEC rate limit (503) after {MAX_RETRIES} retries: {url}")
            if resp.status >= 400:
                raise urllib3.exceptions.HTTPError(f"HTTP {resp.status}: {url}")
            return json.loads(resp.data.decode())
        except (urllib3.exceptions.HTTPError, RateLimitError):
            raise
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_BASE ** (attempt + 1))
                continue
            raise
    raise last_error


def _read_bytes(url: str, user_agent: str | None = None, retry: bool = True) -> bytes:
    """Read bytes with rate limit handling and exponential backoff."""
    ua = user_agent or _get_user_agent()
    pool = _get_pool()
    last_error = None
    for attempt in range(MAX_RETRIES if retry else 1):
        try:
            resp = pool.request("GET", url, headers={"User-Agent": ua}, timeout=60.0)
            if resp.status == 503:
                last_error = urllib3.exceptions.HTTPError(f"503 Service Unavailable: {url}")
                wait = BACKOFF_BASE ** (attempt + 1) + random.uniform(0, 1)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(wait)
                    continue
                raise RateLimitError(f"SEC rate limit (503) after {MAX_RETRIES} retries: {url}")
            if resp.status >= 400:
                raise urllib3.exceptions.HTTPError(f"HTTP {resp.status}: {url}")
            return resp.data
        except (urllib3.exceptions.HTTPError, RateLimitError):
            raise
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_BASE ** (attempt + 1))
                continue
            raise
    raise last_error


def _rate_limited_sleep():
    """Sleep with jitter to avoid thundering herd.

    No-op when external rate control is enabled (caller manages timing).
    """
    if _external_rate_control:
        return
    jitter = random.uniform(*JITTER_RANGE)
    time.sleep(SEC_DELAY + jitter)


def get_company_info(ticker: str) -> dict | None:
    """Return {cik, name} for ticker, or None if not found.
    
    Uses caching to avoid repeated requests to the tickers list.
    """
    global _company_cache, _cache_timestamp
    
    ticker_upper = ticker.upper().strip()
    
    # Check cache first
    now = time.time()
    if _company_cache and (now - _cache_timestamp) < CACHE_TTL:
        return _company_cache.get(ticker_upper)
    
    # Fetch fresh data
    try:
        data = _read_json(TICKERS_URL)
        _company_cache = {}
        for entry in data.values():
            if isinstance(entry, dict):
                tk = entry.get("ticker", "")
                if tk:
                    _company_cache[tk] = {
                        "cik": str(entry["cik_str"]).zfill(10),
                        "name": entry.get("title", tk),
                    }
        _cache_timestamp = now
        return _company_cache.get(ticker_upper)
    except Exception:
        # If fetch fails but we have cached data, use that
        return _company_cache.get(ticker_upper) if _company_cache else None


def get_filings(
    cik: str,
    form_filters: set[str] | None = None,
    limit: int = 10,
    user_agent: str | None = None,
) -> list[dict]:
    """Return list of recent filings."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    _rate_limited_sleep()
    try:
        data = _read_json(url, user_agent)
    except RateLimitError:
        raise RateLimitError("SEC rate limit exceeded. Please wait 10 minutes and try again.")
    except Exception:
        raise
    
    recent = data.get("filings", {}).get("recent") or {}
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary_docs = recent.get("primary_document") or recent.get("primaryDocument") or []
    
    out = []
    for i in range(len(forms)):
        form = forms[i] if i < len(forms) else ""
        if not form:
            continue
        if form_filters and form.upper() not in form_filters:
            continue
        out.append({
            "accessionNumber": accessions[i] if i < len(accessions) else "",
            "form": form,
            "filingDate": dates[i] if i < len(dates) else "",
            "primaryDocument": primary_docs[i] if i < len(primary_docs) else None,
        })
        if len(out) >= limit:
            break
    return out


def get_primary_html_url(
    cik: str,
    accession: str,
    primary_doc: str | None,
    user_agent: str | None = None,
) -> str:
    """Get the primary HTML URL for a filing."""
    path_part = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{path_part}"
    if primary_doc:
        return f"{base}/{primary_doc}"
    index_url = f"{base}/{accession}-index.htm"
    _rate_limited_sleep()
    try:
        raw = _read_bytes(index_url, user_agent)
    except RateLimitError:
        raise RateLimitError("SEC rate limit exceeded during URL lookup.")
    except Exception:
        return f"{base}/{accession}.htm"
    soup = BeautifulSoup(raw, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        name = href.split("/")[-1]
        if name.endswith(".htm") or name.endswith(".html"):
            if "index" not in name.lower():
                return href if href.startswith("http") else f"https://www.sec.gov{href}"
    return f"{base}/{accession}.htm"


def download_filing(
    ticker: str,
    cik: str,
    filing: dict,
    user_agent: str | None = None,
) -> Path | None:
    """Download a single filing to data/drop/."""
    import re
    
    acc = filing["accessionNumber"]
    form = filing["form"]
    date_str = filing.get("filingDate") or "unknown"
    primary_doc = filing.get("primaryDocument")
    
    try:
        url = get_primary_html_url(cik, acc, primary_doc, user_agent)
    except RateLimitError:
        raise
    
    _rate_limited_sleep()
    
    try:
        html = _read_bytes(url, user_agent)
    except RateLimitError:
        raise RateLimitError("SEC rate limit exceeded during download.")
    except Exception:
        return None
    
    date_part = date_str.replace("-", "")
    acc_clean = acc.replace("-", "")
    safe_ticker = re.sub(r"[^\w\-]", "_", ticker.upper())[:20]
    filename = f"{safe_ticker}_{form}_{date_part}_{acc_clean}.html"
    
    DROP_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DROP_DIR / filename
    out_path.write_bytes(html)
    return out_path


def fetch_company_filings(
    ticker: str,
    forms: list[str] | None = None,
    max_filings: int = 5,
) -> dict:
    """Fetch SEC filings for a company. Returns status and list of downloaded files.
    
    Handles rate limiting with exponential backoff. If rate limit is hit, returns
    specific error message advising wait time.
    
    Args:
        ticker: Company ticker symbol
        forms: List of form types (e.g., ["10-K", "10-Q"]), or None for all
        max_filings: Maximum number of filings to fetch
    
    Returns:
        {
            "success": bool,
            "company": str,
            "cik": str,
            "files": [Path, ...],
            "errors": [str, ...],
            "rate_limited": bool  # True if SEC rate limit was hit
        }
    """
    user_agent = _get_user_agent()
    
    # Lookup company
    try:
        company = get_company_info(ticker)
    except RateLimitError:
        return {
            "success": False,
            "company": ticker,
            "cik": "",
            "files": [],
            "errors": ["SEC rate limit exceeded. Please wait 10 minutes before fetching more filings."],
            "rate_limited": True
        }
    
    if not company:
        return {
            "success": False,
            "company": ticker,
            "cik": "",
            "files": [],
            "errors": [f"Ticker not found: {ticker}"],
            "rate_limited": False
        }
    
    cik = company["cik"]
    company_name = company["name"]
    
    # Get filings
    try:
        form_filters = set(forms) if forms else None
        filings = get_filings(cik, form_filters, max_filings, user_agent)
    except RateLimitError:
        return {
            "success": False,
            "company": company_name,
            "cik": cik,
            "files": [],
            "errors": ["SEC rate limit exceeded. Please wait 10 minutes before fetching more filings."],
            "rate_limited": True
        }
    
    if not filings:
        return {
            "success": False,
            "company": company_name,
            "cik": cik,
            "files": [],
            "errors": [f"No filings found for {ticker}"],
            "rate_limited": False
        }
    
    # Download each filing
    downloaded = []
    errors = []
    rate_limited = False
    
    for filing in filings:
        try:
            path = download_filing(ticker, cik, filing, user_agent)
            if path:
                downloaded.append(path)
            else:
                errors.append(f"Failed to download {filing['form']} from {filing['filingDate']}")
        except RateLimitError:
            rate_limited = True
            errors.append(f"Rate limit hit while downloading {filing['form']}")
            break  # Stop trying more filings
    
    return {
        "success": len(downloaded) > 0 and not rate_limited,
        "company": company_name,
        "cik": cik,
        "files": downloaded,
        "errors": errors,
        "rate_limited": rate_limited
    }


def check_ticker_indexed(ticker: str) -> dict:
    """Check if we have any filings indexed for this ticker.
    
    Returns:
        {
            "indexed": bool,
            "count": int,
            "doc_ids": [str, ...]
        }
    """
    from . import tree_store
    
    ticker_upper = ticker.upper()
    all_docs = tree_store.list_trees()
    
    matching = []
    for doc in all_docs:
        doc_name = doc.get("doc_name", "")
        # Check if doc name starts with ticker
        if doc_name.startswith(ticker_upper + "_"):
            matching.append(doc["doc_id"])
    
    return {
        "indexed": len(matching) > 0,
        "count": len(matching),
        "doc_ids": matching
    }
