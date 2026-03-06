"""Plain text / transcript parser."""

import re
from pathlib import Path

# Try to detect ticker from filename patterns like AAPL_10K.txt or MSFT-Q3-2024.txt
TICKER_RE = re.compile(r"^([A-Z]{1,5})[\s_\-]", re.IGNORECASE)
QUARTER_RE = re.compile(r"Q([1-4])\s*(\d{4})", re.IGNORECASE)


def parse_text(path: Path) -> tuple[str, dict]:
    text = path.read_text(encoding="utf-8", errors="replace")

    metadata = {
        "source_file": path.name,
        "file_type": "text",
    }

    # Try to detect ticker from filename
    m = TICKER_RE.match(path.stem)
    if m:
        metadata["ticker"] = m.group(1).upper()

    # Try to detect quarter from filename
    m = QUARTER_RE.search(path.stem)
    if m:
        metadata["quarter"] = int(m.group(1))
        metadata["year"] = int(m.group(2))

    return text, metadata
