"""Interactive SEC EDGAR filing browser and downloader (HTML only).

Prompts for ticker (or use argv), form filter, then shows a table of recent
filings; you select which to download. Saves HTML to data/drop/ for indexing.
SEC requires a descriptive User-Agent (stored in config.json).
"""

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm

import warnings
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# SEC filing index pages may be XML; we parse as HTML to find primary .htm link.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DROP_DIR = ROOT / "data" / "drop"
PROCESSED_DIR = ROOT / "data" / "processed"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_DELAY = 0.11

# Fallback if config has no sec_user_agent (SEC requires real name/email)
DEFAULT_USER_AGENT = "pageindex-rag/1.0 (SEC filings indexing; contact via your-email@example.com)"

console = Console()


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def _get_user_agent() -> str:
    cfg = _load_config()
    ua = (cfg.get("sec_user_agent") or "").strip()
    if ua:
        return ua
    return DEFAULT_USER_AGENT


def _is_placeholder_identity(ua: str) -> bool:
    """SEC rejects placeholder identities; require real name and email."""
    if not ua or not ua.strip():
        return True
    ua_lower = ua.lower()
    return (
        "example.com" in ua_lower
        or "your.email" in ua_lower
        or "your-email@" in ua_lower
        or "research@local" in ua_lower
        or "@local" in ua_lower
    )


def _ensure_identity() -> str:
    """Prompt for SEC User-Agent if missing or placeholder; save to config. Return UA."""
    cfg = _load_config()
    ua = (cfg.get("sec_user_agent") or "").strip()
    if not ua or _is_placeholder_identity(ua):
        console.print("\n[bold]SEC EDGAR requires your real name and email.[/bold]")
        console.print("Format: Your Name (your.email@domain.com)")
        console.print("SEC rejects placeholders like example.com or your-email@example.com.\n")
        ua = Prompt.ask("Enter your SEC User-Agent")
        cfg["sec_user_agent"] = ua.strip()
        _save_config(cfg)
    return _get_user_agent()


def _request(url: str, user_agent: str | None = None) -> urllib.request.Request:
    ua = user_agent or _get_user_agent()
    return urllib.request.Request(url, headers={"User-Agent": ua})


def _read_json(url: str, user_agent: str | None = None) -> dict:
    with urllib.request.urlopen(_request(url, user_agent), timeout=30) as r:
        return json.loads(r.read().decode())


def _read_bytes(url: str, user_agent: str | None = None) -> bytes:
    with urllib.request.urlopen(_request(url, user_agent), timeout=60) as r:
        return r.read()


def get_company_info(ticker: str) -> dict | None:
    """Return {cik, name} for ticker, or None if not found."""
    data = _read_json(TICKERS_URL)
    ticker_upper = ticker.upper().strip()
    for entry in data.values():
        if isinstance(entry, dict) and entry.get("ticker") == ticker_upper:
            return {
                "cik": str(entry["cik_str"]).zfill(10),
                "name": entry.get("title", ticker_upper),
            }
    return None


def get_filings_all_forms(
    cik: str,
    form_filters: set[str] | None = None,
    limit: int = 20,
    user_agent: str | None = None,
) -> list[dict]:
    """Return list of recent filings. If form_filters is set (e.g. {'10-K','10-Q'}), only those forms."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    time.sleep(SEC_DELAY)
    data = _read_json(url, user_agent)
    recent = data.get("filings", {}).get("recent") or {}
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    # Some APIs use primary_document (snake) or primaryDocument (camel)
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


def accession_to_path_part(accession: str) -> str:
    return accession.replace("-", "")


def get_primary_html_url(
    cik: str,
    accession: str,
    primary_doc: str | None,
    user_agent: str | None = None,
) -> str:
    path_part = accession_to_path_part(accession)
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{path_part}"
    if primary_doc:
        return f"{base}/{primary_doc}"
    index_url = f"{base}/{accession}-index.htm"
    time.sleep(SEC_DELAY)
    try:
        raw = _read_bytes(index_url, user_agent)
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


def _parse_selection(selection: str, max_val: int) -> list[int]:
    """Parse user input like '1,3,5' or '1-5' or 'all' -> list of 1-based indices."""
    if selection.strip().lower() == "all":
        return list(range(1, max_val + 1))
    result = []
    for part in selection.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                lo, hi = int(a.strip()), int(b.strip())
                result.extend(range(lo, hi + 1))
            except ValueError:
                pass
        else:
            try:
                result.append(int(part))
            except ValueError:
                pass
    return [i for i in result if 1 <= i <= max_val]


def _download_one(
    cik: str,
    filing: dict,
    ticker: str,
    user_agent: str,
) -> Path | None:
    """Download one filing's HTML to data/drop/. Returns path or None on failure."""
    acc = filing["accessionNumber"]
    form = filing["form"]
    date_str = filing.get("filingDate") or "unknown"
    primary_doc = filing.get("primaryDocument")
    url = get_primary_html_url(cik, acc, primary_doc, user_agent)
    time.sleep(SEC_DELAY)
    try:
        html = _read_bytes(url, user_agent)
    except Exception as e:
        console.print(f"  [red]✗[/red] {form}  {date_str} — {e}")
        return None
    date_part = date_str.replace("-", "")
    acc_clean = accession_to_path_part(acc)
    safe_ticker = re.sub(r"[^\w\-]", "_", ticker.upper())[:20]
    # Include accession so same form+date (e.g. multiple Form 4s) get unique filenames
    filename = f"{safe_ticker}_{form}_{date_part}_{acc_clean}.html"
    DROP_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DROP_DIR / filename
    out_path.write_bytes(html)
    return out_path


def main() -> None:
    user_agent = _ensure_identity()

    # Ticker: from argv or prompt
    if len(sys.argv) > 1:
        ticker = sys.argv[1].upper().strip()
    else:
        ticker = Prompt.ask("\nEnter ticker symbol").upper().strip()

    company = get_company_info(ticker)
    if not company:
        console.print(f"[red]Ticker not found: {ticker}[/red]")
        return
    cik = company["cik"]
    company_name = company["name"]

    # Form filter
    form_filter_raw = Prompt.ask(
        "\nFilter by form type(s)? (e.g. 10-K,10-Q) [leave blank for all]",
        default="",
    ).strip()
    form_filters: set[str] | None = None
    if form_filter_raw:
        form_filters = {
            p.strip().upper()
            for p in form_filter_raw.split(",")
            if p.strip()
        }

    console.print(f"\nFetching filings for [bold]{ticker}[/bold] ({company_name})...")
    filing_list = get_filings_all_forms(cik, form_filters=form_filters, limit=20, user_agent=user_agent)

    if not filing_list:
        if form_filters:
            console.print(
                f"[red]No filings found matching form type(s): {', '.join(sorted(form_filters))}.[/red]"
            )
        else:
            console.print("[red]No filings found.[/red]")
        return

    # Table
    table = Table(title=f"Recent SEC filings for {company_name} ({ticker})")
    table.add_column("#", style="bold white", justify="right", width=4)
    table.add_column("Date", width=12)
    table.add_column("Type", width=10)
    table.add_column("Accession", width=24)
    for i, f in enumerate(filing_list, 1):
        table.add_row(
            str(i),
            f.get("filingDate", ""),
            f.get("form", ""),
            f.get("accessionNumber", "")[:24],
        )
    console.print(table)

    # Selection
    selection = Prompt.ask(
        '\nSelect filings (e.g. 1,3,6 or 1-5 or "all")',
        default="1",
    )
    indices = _parse_selection(selection, len(filing_list))
    if not indices:
        console.print("[red]No valid selection.[/red]")
        return

    # Download
    console.print(f"\nDownloading {len(indices)} filing(s) (HTML only)...")
    downloaded: list[Path] = []
    for idx in indices:
        f = filing_list[idx - 1]
        path = _download_one(cik, f, ticker, user_agent)
        if path:
            downloaded.append(path)
            console.print(f"  [green]✓[/green] {f['form']}  {f.get('filingDate', '')}")

    if not downloaded:
        console.print("[red]No filings downloaded.[/red]")
        return

    # Index now?
    if Confirm.ask("\nIndex these into the tree now?", default=True):
        from . import indexer
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        for path in downloaded:
            try:
                indexer.index_document(path)
                dest = PROCESSED_DIR / path.name
                if dest.exists():
                    dest = PROCESSED_DIR / f"{path.stem}_{path.suffix}"
                path.rename(dest)
            except Exception as e:
                console.print(f"  [red]Index failed for {path.name}: {e}[/red]")
        console.print(f"\n[green]Done.[/green] {len(downloaded)} filing(s) indexed and moved to data/processed/")
    else:
        console.print(f"\n[dim]Skipped indexing. Run [bold]uv run ingest[/bold] to index files in data/drop/.[/dim]")


if __name__ == "__main__":
    main()
