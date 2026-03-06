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

# SEC section patterns: 10-K/10-Q (Item/Part), Form 3/4 (Table/Section),
# 8-K (Item 2.02), Exhibits, Financial Notes, Schedules
_SEC_HEADING_PATTERN = re.compile(
    r"^\s*("
    r"Part\s+[IVXLCDM]+|"
    r"Item\s+\d+[A-Z]?\.|"
    r"Item\s+\d+\.\d+|"
    r"Table\s+[IVXLCDM]+|"
    r"Table\s+\d+|"
    r"Section\s+[IVXLCDM]+|"
    r"Section\s+\d+|"
    r"Exhibit\s+(?:Index|\d+)|"
    r"Note\s+\d+|"
    r"Schedule\s+[IVXLCDM\d]+"
    r")\s*",
    re.IGNORECASE,
)

# SEC EDGAR sometimes serves XHTML/XML; we parse as HTML for heading/text extraction.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


# Block-level elements that contain text/content we want to emit
_BLOCK_TAGS = {"p", "div", "li", "td", "th", "blockquote", "pre", "section", "article", "header", "footer"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _bold_heading_text(tag: Tag) -> str | None:
    """Return heading text if a block tag contains only a single bold child with ALL-CAPS text.

    SEC EDGAR filings commonly use <div><span style="font-weight:bold">HEADING</span></div>
    instead of h1-h6. Detects <b>, <strong>, and bold <span> children.
    """
    # Only check block-level tags that don't contain nested blocks
    if tag.name not in ("p", "div"):
        return None

    # Collect meaningful children (skip whitespace strings)
    children = [c for c in tag.children if not (isinstance(c, NavigableString) and not c.strip())]
    if len(children) != 1:
        return None
    child = children[0]
    if not isinstance(child, Tag):
        return None

    # Check for bold tag: <b>, <strong>, or <span> with font-weight:bold
    is_bold = False
    if child.name in ("b", "strong"):
        is_bold = True
    elif child.name == "span":
        style = child.get("style", "")
        if re.search(r"font-weight\s*:\s*(bold|[7-9]00)", style, re.IGNORECASE):
            is_bold = True

    if not is_bold:
        return None

    text = child.get_text(" ", strip=True)
    if not text or len(text) > 120:
        return None

    # Must be ALL-CAPS (at least 3 alpha chars, all uppercase)
    alpha = [c for c in text if c.isalpha()]
    if len(alpha) < 3 or not all(c.isupper() for c in alpha):
        return None

    return text


def _is_allcaps_heading(line: str) -> bool:
    """True if line looks like an ALL-CAPS heading (3-120 chars, all alpha uppercase).

    Excludes data rows with pipe separators or dollar signs.
    """
    if not line or len(line) < 3 or len(line) > 120:
        return False
    if "|" in line or "$" in line:
        return False
    alpha = [c for c in line if c.isalpha()]
    if len(alpha) < 3:
        return False
    return all(c.isupper() for c in alpha)


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
    """Convert a table to markdown text.

    When <th> header cells exist, emit proper markdown table format with | --- |
    separator row. When no <th>, keep pipe-separated format. If out is provided,
    table headers matching SEC patterns are emitted as markdown headings.
    """
    rows = []
    header_emitted = False
    md_header_row: list[str] | None = None

    for tr in table.find_all("tr"):
        th_cells = tr.find_all("th")
        if th_cells and not header_emitted:
            header_text = " ".join(th.get_text(" ", strip=True) for th in th_cells)
            # Check if this looks like a SEC table header (Table I, Section 1, etc.)
            if out is not None and (_SEC_HEADING_PATTERN.match(header_text) or len(header_text) < 100):
                lower_text = header_text.lower()
                if any(keyword in lower_text for keyword in [
                    "table", "section", "non-derivative", "derivative",
                    "securities", "beneficially owned", "title of security"
                ]):
                    out.append(f"## {header_text}\n")
                    header_emitted = True
                    continue
            # Emit as proper markdown table header row
            cells = [th.get_text(" ", strip=True) for th in th_cells]
            if cells and any(c.strip() for c in cells):
                md_header_row = cells
                rows.append("| " + " | ".join(cells) + " |")
                rows.append("| " + " | ".join("---" for _ in cells) + " |")
                header_emitted = True
                continue

        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        if cells and any(c.strip() for c in cells):
            if md_header_row is not None:
                rows.append("| " + " | ".join(cells) + " |")
            else:
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
                # Check for bold ALL-CAPS heading (SEC EDGAR style)
                bold_text = _bold_heading_text(child)
                if bold_text:
                    out.append(f"## {bold_text}\n")
                    if state is not None:
                        state["has_headings"] = True
                    continue
                text = _element_text(child)
                if text:
                    # Check for SEC heading patterns in block text → ###
                    if _SEC_HEADING_PATTERN.match(text):
                        out.append(f"### {text}\n")
                        if state is not None:
                            state["has_headings"] = True
                    else:
                        # Escape leading '#' to prevent md_to_tree misinterpretation
                        # (SEC footnotes like "# Indicates a management contract")
                        if text.startswith("#"):
                            text = "\\" + text
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

    # Remove SEC EDGAR page-break TOC navigation links
    # Pattern: <h5><a href="#toc">Table of Contents</a></h5> (repeated on every page)
    for h5 in soup.find_all("h5"):
        link = h5.find("a")
        if link and "table of contents" in link.get_text(strip=True).lower():
            h5.decompose()

    body = soup.find("body") or soup
    out: list[str] = []
    state: dict = {"has_headings": False}
    _walk(body, out, state)

    # Join and normalize: ensure blank line between sections, no excessive newlines
    md = "\n".join(line.rstrip() for line in "\n".join(out).splitlines())
    md = md.strip() + "\n" if md.strip() else ""

    # Fallback: if no real h1–h6 tags were found, promote SEC patterns and ALL-CAPS lines
    if md and not state["has_headings"]:
        lines = md.splitlines()
        promoted = []
        for line in lines:
            stripped = line.strip()
            if stripped and _SEC_HEADING_PATTERN.match(stripped):
                promoted.append("## " + stripped)
            elif stripped and _is_allcaps_heading(stripped):
                promoted.append("## " + stripped)
            else:
                promoted.append(line)
        md = "\n".join(promoted).strip() + "\n" if promoted else ""

    # Merge cover page boilerplate: short ALL-CAPS heading lines near the start
    # of the document into a single heading.
    # SEC filings start with fragments like "UNITED STATES", "SECURITIES AND
    # EXCHANGE COMMISSION", "FORM S-1", etc. — each becoming a separate node.
    # Plain text lines (filing date, address) may be interspersed; skip them.
    # Limit scan to first 50 lines to avoid absorbing real content sections.
    if md:
        lines = md.splitlines()
        scan_limit = min(50, len(lines))
        cover_end = 0
        cover_lines = []
        cover_heading_indices = []
        for i in range(scan_limit):
            stripped = lines[i].strip()
            if not stripped:
                continue
            # Check if this is a short ALL-CAPS heading (## WORD or ## SHORT PHRASE)
            m = re.match(r'^##\s+(.+)$', stripped)
            if m and len(m.group(1)) <= 60 and _is_allcaps_heading(m.group(1)):
                cover_lines.append(m.group(1))
                cover_heading_indices.append(i)
                cover_end = i + 1
            elif not stripped.startswith("#"):
                # Plain text line — skip it (part of cover preamble)
                continue
            else:
                # Non-ALL-CAPS heading — cover page is over
                break

        if len(cover_lines) >= 3:
            merged = " | ".join(cover_lines)
            # Keep non-heading lines before cover_end, drop heading lines
            kept = [lines[j] for j in range(cover_end) if j not in set(cover_heading_indices)]
            remaining = lines[cover_end:]
            md = "\n".join(kept) + f"\n## {merged}\n" + "\n".join(remaining)
            md = md.strip() + "\n" if md.strip() else ""

    # Deduplicate page-break headers: SEC XBRL filings repeat company name,
    # period-end date, and "(CONTINUED)" headers on every page.  Any heading
    # that appears 3+ times is a page-break artifact — keep only the first
    # occurrence.  Also strip "(CONTINUED)" suffix from headings (page
    # continuation markers) and remove the heading if the base title already
    # appeared.
    if md:
        lines = md.splitlines()
        heading_count: dict[str, int] = {}
        for line in lines:
            m = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
            if m:
                title = m.group(2).strip()
                heading_count[title] = heading_count.get(title, 0) + 1

        # Identify titles repeated 3+ times (page-break headers)
        repeat_titles = {t for t, c in heading_count.items() if c >= 3}

        if repeat_titles:
            seen_titles: set[str] = set()
            deduped = []
            for line in lines:
                m = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
                if m:
                    title = m.group(2).strip()
                    # Strip "(CONTINUED)" suffix
                    base = re.sub(r'\s*\(CONTINUED\)\s*$', '', title, flags=re.IGNORECASE)
                    if title in repeat_titles:
                        if title not in seen_titles:
                            seen_titles.add(title)
                            deduped.append(line)
                        # else: skip duplicate
                    elif base != title and base in seen_titles:
                        # "(CONTINUED)" variant of an already-seen heading — skip
                        pass
                    else:
                        seen_titles.add(title)
                        seen_titles.add(base)
                        deduped.append(line)
                else:
                    deduped.append(line)
            md = "\n".join(deduped).strip() + "\n" if deduped else ""

    # Escape stray '#' that aren't headings (e.g. SEC footnote markers like
    # "# Indicates a management contract").  Only lines starting with '#'
    # that were NOT already promoted as headings.
    if md:
        lines = md.splitlines()
        escaped = []
        for line in lines:
            if line.startswith("#") and not re.match(r"^#{1,6}\s", line):
                escaped.append("\\" + line)
            else:
                escaped.append(line)
        md = "\n".join(escaped).strip() + "\n" if escaped else ""

    return md
