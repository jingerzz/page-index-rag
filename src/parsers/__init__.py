from .pdf_parser import parse_pdf
from .html_parser import parse_html
from .html_to_markdown import html_to_markdown
from .csv_parser import parse_csv
from .text_parser import parse_text

PARSERS = {
    ".pdf": parse_pdf,
    ".htm": parse_html,
    ".html": parse_html,
    ".csv": parse_csv,
    ".xlsx": parse_csv,
    ".xls": parse_csv,
    ".txt": parse_text,
    ".md": parse_text,
}


def parse_file(path):
    """Route a file to the appropriate parser. Returns (text, metadata)."""
    suffix = path.suffix.lower()
    parser = PARSERS.get(suffix)
    if parser is None:
        raise ValueError(f"Unsupported file type: {suffix}")
    return parser(path)
