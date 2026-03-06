"""Enhanced MCP tool definitions for SEC filing fetching.

This module provides improved MCP tools that address the limitations
of the current fetch_company_filings tool:

1. check_filings_available() - Shows total available vs indexed BEFORE downloading
2. fetch_company_filings_enhanced() - Paginated fetching with progress and resume support
3. get_fetch_resume_status() - Check if a previous fetch can be resumed

To integrate into server.py, add:
    from mcp_enhanced_fetch import check_filings_available, fetch_company_filings_enhanced

Then add the @mcp.tool() decorated functions from below.
"""

from .enhanced_sec_fetcher import (
    EnhancedSECFetcher, 
    check_filings_available as _check_available,
    fetch_company_filings_enhanced as _fetch_enhanced,
)
from .sec_fetcher import RateLimitError


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
        # Returns:
        # **CRWV** - CoreWeave, Inc.
        # 
        # **Available on SEC EDGAR:** 144 filings
        # **Already indexed:** 15 filings  
        # **New filings to fetch:** 129 filings
        # 
        # **Form type breakdown:**
        #   - 4: 87
        #   - 8-K: 12
        #   - 10-Q: 8
        #   ...
    """
    return _check_available(ticker, forms)


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
        3. If rate limited, wait the specified time and re-run (automatically resumes)
    
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
        # Returns:
        # **CRWV** Filing Fetch Results
        # 
        # Total available on SEC EDGAR: 144
        # Already indexed: 15
        # New filings to fetch: 129
        # 
        # ✅ **Complete**
        # Downloaded: 129 new filings
    """
    return _fetch_enhanced(ticker, forms, batch_size, auto_index)


def get_fetch_resume_status() -> str:
    """Check if there are any incomplete fetches that can be resumed.
    
    This is a placeholder for future resume tracking functionality.
    
    Returns:
        Status of any incomplete fetch operations.
    """
    return "Resume tracking not yet implemented. Simply re-run fetch_company_filings_enhanced() - it will automatically skip already-downloaded files."


# Tool metadata for MCP server registration
ENHANCED_TOOLS = [
    {
        "name": "check_filings_available",
        "description": """Check available SEC filings WITHOUT downloading.

USE THIS FIRST to see:
- Total filings available on SEC EDGAR
- How many are already indexed locally  
- How many are new
- Breakdown by form type

This is the enhanced version of check_company_indexed().""",
    },
    {
        "name": "fetch_company_filings_enhanced", 
        "description": """Fetch SEC filings with pagination and smart rate limiting.

KEY IMPROVEMENTS over fetch_company_filings:
1. Shows total available BEFORE downloading
2. Fetches in small batches (default 10) to avoid rate limits
3. Auto-resumes - skips already-indexed filings
4. Provides progress updates (Batch 3/15)
5. Clear resume instructions if rate limited

RECOMMENDED for fetching >20 filings.""",
    },
]
