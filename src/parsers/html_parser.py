"""HTML parsing using BeautifulSoup."""

import warnings
from pathlib import Path

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# Content may be XHTML/XML; we parse as HTML for tag names and get_text().
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def parse_html(path: Path) -> tuple[str, dict]:
    raw = path.read_bytes()
    # Try UTF-8 first, fall back to latin-1
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        html = raw.decode("latin-1")

    soup = BeautifulSoup(html, "lxml")

    # Remove script and style elements
    for tag in soup(["script", "style"]):
        tag.decompose()

    # Convert tables to readable text
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            rows.append(" | ".join(cells))
        table.replace_with(soup.new_string("\n".join(rows) + "\n"))

    text = soup.get_text("\n", strip=True)
    # Collapse excessive whitespace
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)

    metadata = {
        "source_file": path.name,
        "file_type": "html",
    }
    return text, metadata
