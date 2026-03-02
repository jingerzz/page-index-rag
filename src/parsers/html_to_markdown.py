"""Hierarchy-faithful HTML → Markdown converter for indexing (e.g. SEC EDGAR filings).

Maps h1–h6 to #–###### and preserves document order so md_to_tree() can build
the tree from heading levels without LLM structure calls.

When the HTML has no h1–h6 (common in some SEC filings), a fallback detects
SEC-style section labels and turns them into ## headings so we still get a
usable tree instead of 0 nodes:
- 10-K/10-Q: "Item 1.", "Item 1A.", "Part I", etc.
- Form 3/4: "Table I", "Table II", "Table 1", "Section 1", etc.
"""

import re
import warnings
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning

# SEC section patterns: 10-K/10-Q (Item/Part) and Form 3/4 (Table/Section)
_SEC_HEADING_PATTERN = re.compile(
    r"^\s*("
    r"Part\s+[IVXLCDM]+|"
    r"Item\s+\d+[A-Z]?\.|"
    r"Table\s+[IVXLCDM]+|"
    r"Table\s+\d+|"
    r"Section\s+[IVXLCDM]+|"
    r"Section\s+\d+"
    r")\s*",
    re.IGNORECASE,
)

# SEC EDGAR sometimes serves XHTML/XML; we parse as HTML for heading/text extraction.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


# Block-level elements that contain text/content we want to emit
_BLOCK_TAGS = {"p", "div", "li", "td", "th", "blockquote", "pre", "section", "article", "header", "footer"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _has_block_or_heading_children(tag: Tag) -> bool:
    """True if any direct child is a block or heading tag."""
    for child in tag.children:
        if isinstance(child, Tag) and child.name in (_BLOCK_TAGS | _HEADING_TAGS):
            return True
    return False


def _element_text(tag: Tag) -> str:
    """Get text content of element, stripped."""
    return tag.get_text(" ", strip=True)


def _table_to_text(table: Tag, out: list[str] | None = None) -> str:
    """Convert a table to simple line-based text (one row per line, cells separated).
    
    If out is provided, table headers matching SEC patterns are emitted as markdown
    headings before the table content.
    """
    rows = []
    header_emitted = False
    
    for tr in table.find_all("tr"):
        # Check for header row with SEC-style patterns
        th_cells = tr.find_all("th")
        if th_cells and out is not None and not header_emitted:
            header_text = " ".join(th.get_text(" ", strip=True) for th in th_cells)
            # Check if this looks like a SEC table header (Table I, Section 1, etc.)
            if _SEC_HEADING_PATTERN.match(header_text) or len(header_text) < 100:
                # Also check for common SEC table headers
                lower_text = header_text.lower()
                if any(keyword in lower_text for keyword in [
                    "table", "section", "non-derivative", "derivative", 
                    "securities", "beneficially owned", "title of security"
                ]):
                    out.append(f"## {header_text}\n")
                    header_emitted = True
                    continue  # Skip emitting this row in table body
        
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        if cells and any(c.strip() for c in cells):  # Skip empty rows
            rows.append(" | ".join(cells))
    
    return "\n".join(rows) if rows else ""


def _walk(soup: Tag, out: list[str], state: dict | None = None) -> None:
    """Recursively walk the DOM and append markdown lines to out (preserves order).

    state["has_headings"] is set True when a real h1–h6 tag is found.
    """
    for child in soup.children:
        if isinstance(child, NavigableString):
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name
        if name in _HEADING_TAGS:
            level = int(name[1])
            prefix = "#" * level
            text = _element_text(child)
            if text:
                out.append(f"{prefix} {text}\n")
                if state is not None:
                    state["has_headings"] = True
        elif name == "table":
            text = _table_to_text(child, out)
            if text:
                out.append(text + "\n")
        elif name in _BLOCK_TAGS:
            if _has_block_or_heading_children(child):
                _walk(child, out, state)
            else:
                text = _element_text(child)
                if text:
                    out.append(text + "\n")
        else:
            _walk(child, out, state)


def html_to_markdown(html_input: str | Path) -> str:
    """Convert HTML to hierarchy-faithful Markdown.

    - h1 → # Title, h2 → ## Title, … h6 → ###### Title
    - Block content (p, div, li, table, etc.) is emitted in document order.
    - Script and style elements are removed. Encoding: UTF-8 with latin-1 fallback for Path input.

    Args:
        html_input: Either an HTML string or a Path to an .html/.htm file.

    Returns:
        Markdown string suitable for md_to_tree().
    """
    if isinstance(html_input, Path):
        raw = html_input.read_bytes()
        try:
            html = raw.decode("utf-8")
        except UnicodeDecodeError:
            html = raw.decode("latin-1")
    else:
        html = html_input

    soup = BeautifulSoup(html, "lxml")

    # Remove script, style, and hidden elements (iXBRL metadata uses display:none)
    for tag in soup(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display\s*:\s*none", re.IGNORECASE)):
        tag.decompose()

    body = soup.find("body") or soup
    out: list[str] = []
    state: dict = {"has_headings": False}
    _walk(body, out, state)

    # Join and normalize: ensure blank line between sections, no excessive newlines
    md = "\n".join(line.rstrip() for line in "\n".join(out).splitlines())
    md = md.strip() + "\n" if md.strip() else ""

    # Fallback: if no real h1–h6 tags were found, promote SEC Item/Part lines to ##
    if md and not state["has_headings"]:
        lines = md.splitlines()
        promoted = []
        for line in lines:
            stripped = line.strip()
            if stripped and _SEC_HEADING_PATTERN.match(line):
                promoted.append("## " + stripped)
            else:
                promoted.append(line)
        md = "\n".join(promoted).strip() + "\n" if promoted else ""

    return md
