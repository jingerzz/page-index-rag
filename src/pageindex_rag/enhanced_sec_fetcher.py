"""Enhanced SEC filing fetcher with pagination, total count awareness, and proactive rate limiting.

This wrapper around sec_fetcher.py adds:
1. Pre-flight checks to show total available filings before downloading
2. Pagination support (offset-based fetching)
3. Proactive rate limit tracking to avoid SEC blocks
4. Resume capability for interrupted fetches
5. Smart deduplication based on accession numbers

Usage:
    from enhanced_sec_fetcher import EnhancedSECFetcher
    
    fetcher = EnhancedSECFetcher()
    
    # First, check what's available without downloading
    available = fetcher.get_available_filings("CRWV")
    print(f"Total available: {available['total_count']}")
    
    # Fetch with pagination
    for batch in fetcher.fetch_paginated("CRWV", batch_size=10):
        print(f"Downloaded batch: {batch['files']}")
"""

import json
import time
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import Iterator
from dataclasses import dataclass, field

from . import sec_fetcher
from .sec_fetcher import RateLimitError, SECLookupError

# Rate limit configuration
SEC_REQUESTS_PER_SECOND = 10
MIN_REQUEST_INTERVAL = 1.0 / SEC_REQUESTS_PER_SECOND  # 0.1s minimum
SAFE_REQUEST_INTERVAL = 0.14  # 0.14s for safety margin (~7 req/s)
RATE_LIMIT_COOLDOWN_MINUTES = 10


@dataclass
class RateLimitTracker:
    """Tracks requests to proactively avoid rate limiting."""
    requests: list[datetime] = field(default_factory=list)
    last_request_time: datetime | None = None
    rate_limited_until: datetime | None = None
    
    def record_request(self):
        """Record that a request was made."""
        now = datetime.now()
        self.requests.append(now)
        self.last_request_time = now
        # Keep only last 60 seconds of history
        cutoff = now - timedelta(seconds=60)
        self.requests = [r for r in self.requests if r > cutoff]
    
    def get_requests_in_last_minute(self) -> int:
        """Count requests made in the last 60 seconds."""
        cutoff = datetime.now() - timedelta(seconds=60)
        return len([r for r in self.requests if r > cutoff])
    
    def is_rate_limited(self) -> bool:
        """Check if we're currently in a rate limit cooldown."""
        if self.rate_limited_until is None:
            return False
        return datetime.now() < self.rate_limited_until
    
    def set_rate_limited(self):
        """Mark that we hit a rate limit."""
        self.rate_limited_until = datetime.now() + timedelta(minutes=RATE_LIMIT_COOLDOWN_MINUTES)
    
    def get_wait_time(self) -> float:
        """Get recommended wait time before next request (in seconds)."""
        if self.is_rate_limited():
            remaining = (self.rate_limited_until - datetime.now()).total_seconds()
            return max(remaining, 0)
        
        # Proactive throttling — SEC allows 10 req/s = 600 req/min.
        # Throttle at sustained ~6.67 req/s (400 in 60s) for safety.
        recent_count = self.get_requests_in_last_minute()
        if recent_count >= 400:
            return 2.0  # Wait 2 seconds to cool down
        
        # Standard rate limiting
        if self.last_request_time:
            elapsed = (datetime.now() - self.last_request_time).total_seconds()
            wait = SAFE_REQUEST_INTERVAL - elapsed
            return max(wait, 0)
        
        return 0
    
    def wait_if_needed(self):
        """Wait if necessary to respect rate limits."""
        wait_time = self.get_wait_time()
        if wait_time > 0:
            time.sleep(wait_time)


class EnhancedSECFetcher:
    """Enhanced SEC fetcher with pagination and rate limit management."""
    
    def __init__(self):
        self.rate_tracker = RateLimitTracker()
        self.user_agent = sec_fetcher._get_user_agent()
    
    def get_available_filings(
        self, 
        ticker: str, 
        forms: list[str] | None = None
    ) -> dict:
        """Get list of ALL available filings WITHOUT downloading.
        
        Returns:
            {
                "ticker": str,
                "company": str,
                "cik": str,
                "total_count": int,
                "form_breakdown": {"10-K": 5, "10-Q": 8, ...},
                "filings": [{"form": "10-K", "date": "2024-01-15", "accession": "..."}, ...],
                "already_indexed": ["CRWV_10-K_20240115_...", ...],
                "new_filings": [...]  # Not yet indexed
            }
        """
        # Check rate limit first
        if self.rate_tracker.is_rate_limited():
            wait_mins = int(self.rate_tracker.get_wait_time() / 60) + 1
            raise RateLimitError(f"Rate limited. Wait {wait_mins} minutes.")
        
        self.rate_tracker.wait_if_needed()
        
        # Get company info
        company = sec_fetcher.get_company_info(ticker)
        if not company:
            raise SECLookupError(f"Ticker not found: {ticker}")
        
        cik = company["cik"]
        
        self.rate_tracker.record_request()
        self.rate_tracker.wait_if_needed()
        
        # Fetch ALL filings (no limit) - we'll get everything from SEC API
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        try:
            data = sec_fetcher._read_json(url, self.user_agent)
        except RateLimitError:
            self.rate_tracker.set_rate_limited()
            raise
        
        recent = data.get("filings", {}).get("recent") or {}
        forms_list = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        dates = recent.get("filingDate", [])
        primary_docs = recent.get("primary_document") or recent.get("primaryDocument") or []
        
        # Build complete list
        all_filings = []
        form_breakdown = {}
        
        form_filters = set(forms) if forms else None
        
        for i in range(len(forms_list)):
            form = forms_list[i] if i < len(forms_list) else ""
            if not form:
                continue
            if form_filters and form.upper() not in form_filters:
                continue
            
            filing_info = {
                "form": form,
                "date": dates[i] if i < len(dates) else "",
                "accession": accessions[i] if i < len(accessions) else "",
                "primary_doc": primary_docs[i] if i < len(primary_docs) else None,
            }
            all_filings.append(filing_info)
            form_breakdown[form] = form_breakdown.get(form, 0) + 1
        
        # Check which are already indexed
        indexed = sec_fetcher.check_ticker_indexed(ticker)
        indexed_accessions = set()
        for doc_id in indexed.get("doc_ids", []):
            # Extract accession from doc_id (e.g., "CRWV_10-K_20240115_000123456724000123_abc123")
            parts = doc_id.split("_")
            if len(parts) >= 4:
                indexed_accessions.add(parts[3])  # The accession number part
        
        new_filings = [
            f for f in all_filings 
            if f["accession"].replace("-", "") not in indexed_accessions
        ]
        
        return {
            "ticker": ticker.upper(),
            "company": company["name"],
            "cik": cik,
            "total_count": len(all_filings),
            "form_breakdown": form_breakdown,
            "filings": all_filings,
            "already_indexed_count": len(all_filings) - len(new_filings),
            "new_filings": new_filings,
            "new_count": len(new_filings),
        }
    
    def fetch_paginated(
        self,
        ticker: str,
        forms: list[str] | None = None,
        batch_size: int = 10,
        max_total: int | None = None,
        skip_indexed: bool = True,
        _available_data: dict | None = None,
    ) -> Iterator[dict]:
        """Fetch filings in paginated batches with rate limit protection.

        Args:
            ticker: Company ticker
            forms: Form type filter (e.g., ["10-K", "10-Q"])
            batch_size: Number of filings per batch (default 10)
            max_total: Maximum total filings to fetch (None for all)
            skip_indexed: If True, skip filings already in the index
            _available_data: Pre-fetched data from get_available_filings() to
                             avoid a redundant API call.

        Yields:
            Batch results: {
                "batch_num": int,
                "total_batches": int,
                "files": [Path, ...],
                "filings": [{form, date, accession}, ...],
                "rate_limited": bool,
                "completed": bool
            }
        """
        # Use pre-fetched data if available, otherwise fetch
        available = _available_data or self.get_available_filings(ticker, forms)

        filings_to_fetch = available["new_filings"] if skip_indexed else available["filings"]

        if max_total:
            filings_to_fetch = filings_to_fetch[:max_total]

        total = len(filings_to_fetch)
        if total == 0:
            yield {
                "batch_num": 0,
                "total_batches": 0,
                "files": [],
                "filings": [],
                "rate_limited": False,
                "completed": True,
                "message": "No new filings to fetch" if skip_indexed else "No filings found"
            }
            return

        total_batches = (total + batch_size - 1) // batch_size
        # Use CIK from available data instead of an extra API call
        cik = available["cik"]
        
        for batch_num in range(total_batches):
            # Check if rate limited before starting batch
            if self.rate_tracker.is_rate_limited():
                yield {
                    "batch_num": batch_num + 1,
                    "total_batches": total_batches,
                    "files": [],
                    "filings": [],
                    "rate_limited": True,
                    "completed": False,
                    "message": f"Rate limited. Wait {RATE_LIMIT_COOLDOWN_MINUTES} minutes."
                }
                return
            
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, total)
            batch_filings = filings_to_fetch[start_idx:end_idx]
            
            downloaded = []
            errors = []
            
            for filing in batch_filings:
                # Check rate limit before each download
                if self.rate_tracker.is_rate_limited():
                    yield {
                        "batch_num": batch_num + 1,
                        "total_batches": total_batches,
                        "files": downloaded,
                        "filings": batch_filings[:len(downloaded)],
                        "rate_limited": True,
                        "completed": False,
                        "message": f"Rate limited after {len(downloaded)} downloads"
                    }
                    return
                
                self.rate_tracker.wait_if_needed()
                
                try:
                    path = sec_fetcher.download_filing(
                        ticker, cik, 
                        {
                            "accessionNumber": filing["accession"],
                            "form": filing["form"],
                            "filingDate": filing["date"],
                            "primaryDocument": filing["primary_doc"]
                        },
                        self.user_agent
                    )
                    if path:
                        downloaded.append(path)
                    self.rate_tracker.record_request()
                except RateLimitError:
                    self.rate_tracker.set_rate_limited()
                    yield {
                        "batch_num": batch_num + 1,
                        "total_batches": total_batches,
                        "files": downloaded,
                        "filings": batch_filings[:len(downloaded)],
                        "rate_limited": True,
                        "completed": False,
                        "message": f"Rate limited after {len(downloaded)} downloads"
                    }
                    return
                except Exception as e:
                    errors.append(f"{filing['form']} {filing['date']}: {str(e)}")
            
            yield {
                "batch_num": batch_num + 1,
                "total_batches": total_batches,
                "files": downloaded,
                "filings": batch_filings,
                "errors": errors,
                "rate_limited": False,
                "completed": (batch_num + 1) >= total_batches,
                "progress": f"{end_idx}/{total}"
            }
    
    def fetch_all_with_resume(
        self,
        ticker: str,
        forms: list[str] | None = None,
        batch_size: int = 10,
        auto_index: bool = True,
    ) -> dict:
        """Fetch all filings with resume capability and progress tracking.
        
        This is the main entry point for comprehensive fetching.
        
        Returns:
            {
                "success": bool,
                "ticker": str,
                "total_available": int,
                "already_indexed": int,
                "new_filings": int,
                "downloaded": int,
                "files": [Path, ...],
                "batches_completed": int,
                "total_batches": int,
                "rate_limited": bool,
                "resume_after_minutes": int | None,
                "message": str
            }
        """
        result = {
            "success": False,
            "ticker": ticker.upper(),
            "total_available": 0,
            "already_indexed": 0,
            "new_filings": 0,
            "downloaded": 0,
            "files": [],
            "batches_completed": 0,
            "total_batches": 0,
            "rate_limited": False,
            "resume_after_minutes": None,
            "message": ""
        }
        
        # Enable external rate control so sec_fetcher._rate_limited_sleep()
        # is a no-op — we manage timing via self.rate_tracker instead.
        sec_fetcher.set_external_rate_control(True)
        try:
            # Get available filings
            available = self.get_available_filings(ticker, forms)
            result["total_available"] = available["total_count"]
            result["already_indexed"] = available["already_indexed_count"]
            result["new_filings"] = available["new_count"]

            if available["new_count"] == 0:
                result["success"] = True
                result["message"] = f"All {available['total_count']} filings already indexed"
                return result

            # Fetch in batches — pass available data to avoid redundant API call
            all_files = []
            batches_completed = 0
            total_batches = None

            for batch in self.fetch_paginated(
                ticker, forms, batch_size, skip_indexed=True,
                _available_data=available,
            ):
                batches_completed = batch["batch_num"]
                total_batches = batch["total_batches"]
                all_files.extend(batch["files"])

                if batch["rate_limited"]:
                    result["rate_limited"] = True
                    result["resume_after_minutes"] = RATE_LIMIT_COOLDOWN_MINUTES
                    result["message"] = batch.get("message", "Rate limited")
                    break

                if batch["completed"]:
                    result["success"] = True
                    result["message"] = f"Downloaded all {len(all_files)} new filings"

            result["downloaded"] = len(all_files)
            result["files"] = all_files
            result["batches_completed"] = batches_completed
            result["total_batches"] = total_batches or 0

            # Auto-index if requested and we have files
            if auto_index and all_files and not result["rate_limited"]:
                from . import indexer
                for path in all_files:
                    try:
                        indexer.index_document(path)
                    except Exception as e:
                        print(f"Warning: Failed to index {path}: {e}")

            return result

        except RateLimitError as e:
            result["rate_limited"] = True
            result["resume_after_minutes"] = RATE_LIMIT_COOLDOWN_MINUTES
            result["message"] = str(e)
            return result
        except Exception as e:
            result["message"] = f"Error: {str(e)}"
            return result
        finally:
            sec_fetcher.set_external_rate_control(False)


def check_filings_available(ticker: str, forms: str = "") -> str:
    """Check how many filings are available WITHOUT downloading.
    
    This is the enhanced version of check_company_indexed that shows
    the full picture of available vs indexed filings.
    """
    fetcher = EnhancedSECFetcher()
    
    form_list = [f.strip() for f in forms.split(",") if f.strip()] if forms else None
    
    try:
        info = fetcher.get_available_filings(ticker, form_list)
        
        lines = [
            f"**{info['ticker']}** - {info['company']}",
            f"",
            f"**Available on SEC EDGAR:** {info['total_count']} filings",
            f"**Already indexed:** {info['already_indexed_count']} filings",
            f"**New filings to fetch:** {info['new_count']} filings",
            f"",
            "**Form type breakdown:**",
        ]
        
        for form, count in sorted(info['form_breakdown'].items(), key=lambda x: -x[1]):
            lines.append(f"  - {form}: {count}")
        
        if info['new_count'] > 0:
            lines.extend([
                f"",
                f"**Next step:** Use `fetch_company_filings_enhanced('{ticker}')` to download new filings.",
            ])
        
        return "\n".join(lines)
        
    except RateLimitError as e:
        return f"**Rate Limit Active**\n\n{str(e)}\n\nWait 10 minutes and try again."
    except Exception as e:
        return f"**Error:** {str(e)}"


def fetch_company_filings_enhanced(
    ticker: str,
    forms: str = "",
    batch_size: int = 10,
    auto_index: bool = True
) -> str:
    """Enhanced fetch that handles pagination and rate limits intelligently.
    
    This is the enhanced version of fetch_company_filings that:
    1. Shows total available before downloading
    2. Fetches in smaller batches to avoid rate limits
    3. Provides progress updates
    4. Handles resume after rate limits
    """
    fetcher = EnhancedSECFetcher()
    
    form_list = [f.strip() for f in forms.split(",") if f.strip()] if forms else None
    
    result = fetcher.fetch_all_with_resume(
        ticker=ticker,
        forms=form_list,
        batch_size=batch_size,
        auto_index=auto_index
    )
    
    lines = [
        f"**{result['ticker']}** Filing Fetch Results",
        f"",
        f"Total available on SEC EDGAR: {result['total_available']}",
        f"Already indexed: {result['already_indexed']}",
        f"New filings to fetch: {result['new_filings']}",
        f"",
    ]
    
    if result['rate_limited']:
        lines.extend([
            f"⚠️ **Rate Limit Hit**",
            f"Downloaded: {result['downloaded']} of {result['new_filings']} new filings",
            f"Batches: {result['batches_completed']}/{result['total_batches']}",
            f"",
            f"**Resume in: {result['resume_after_minutes']} minutes**",
            f"",
            f"Files downloaded so far: {len(result['files'])}",
        ])
        for f in result['files']:
            lines.append(f"  - {f.name}")
    elif result['success']:
        lines.extend([
            f"✅ **Complete**",
            f"Downloaded: {result['downloaded']} new filings",
            f"",
            f"Files downloaded:",
        ])
        for f in result['files']:
            lines.append(f"  - {f.name}")
        
        if auto_index:
            lines.append(f"")
            lines.append(f"Indexing completed in background.")
    else:
        lines.extend([
            f"❌ **Failed**",
            f"Message: {result['message']}",
        ])
    
    return "\n".join(lines)
