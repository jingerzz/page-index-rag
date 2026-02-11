"""PDF text extraction using pypdf."""

from pathlib import Path
from pypdf import PdfReader


def parse_pdf(path: Path) -> tuple[str, dict]:
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    full_text = "\n\n".join(pages)
    metadata = {
        "source_file": path.name,
        "file_type": "pdf",
        "pages": len(reader.pages),
    }
    return full_text, metadata
