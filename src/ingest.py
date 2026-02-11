"""Interactive drop-folder ingestion script with Rich UI."""

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm

from . import indexer
from .parsers import PARSERS

ROOT = Path(__file__).resolve().parent.parent
DROP_DIR = ROOT / "data" / "drop"
PROCESSED_DIR = ROOT / "data" / "processed"

SUPPORTED_EXTENSIONS = set(PARSERS.keys()) | {".md", ".markdown"}

console = Console()

# Thread pool for indexing (PageIndex calls asyncio.run() internally)
_executor = ThreadPoolExecutor(max_workers=1)


def _get_files() -> list[Path]:
    """Get all supported files in the drop folder."""
    files = []
    for path in sorted(DROP_DIR.iterdir()):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(path)
    return files


def main():
    DROP_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    files = _get_files()
    if not files:
        console.print(f"\nNo supported files found in [bold]{DROP_DIR}[/bold]")
        console.print(f"Supported types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        return

    # Show file table
    table = Table(title=f"Found {len(files)} file(s) in data/drop/")
    table.add_column("#", style="cyan", width=4)
    table.add_column("File", width=50)
    table.add_column("Type", width=10)
    table.add_column("Size", width=12, justify="right")

    type_names = {
        ".pdf": "PDF", ".htm": "HTML", ".html": "HTML",
        ".csv": "CSV", ".xlsx": "Excel", ".xls": "Excel",
        ".txt": "Text", ".md": "Markdown", ".markdown": "Markdown",
    }
    for i, f in enumerate(files, 1):
        size = f.stat().st_size
        if size > 1_000_000:
            size_str = f"{size / 1_000_000:.1f} MB"
        elif size > 1_000:
            size_str = f"{size / 1_000:.1f} KB"
        else:
            size_str = f"{size} B"
        table.add_row(str(i), f.name, type_names.get(f.suffix.lower(), f.suffix), size_str)

    console.print(table)
    console.print()

    if not Confirm.ask("Proceed with indexing?", default=True):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    processed = 0
    for i, filepath in enumerate(files, 1):
        console.print(f"\n[bold]Processing file {i} of {len(files)}:[/bold] {filepath.name}")

        try:
            with console.status(f"Indexing {filepath.name}...", spinner="dots"):
                # Run indexing in thread pool to avoid nested asyncio.run() issues
                future = _executor.submit(indexer.index_document, filepath)
                doc_id = future.result(timeout=600)

            console.print(f"  [green]OK[/green] Indexed as doc_id: [bold]{doc_id}[/bold]")

            # Move to processed
            dest = PROCESSED_DIR / filepath.name
            if dest.exists():
                dest = PROCESSED_DIR / f"{filepath.stem}_{i}{filepath.suffix}"
            shutil.move(str(filepath), str(dest))
            processed += 1
        except Exception as e:
            console.print(f"  [red]FAIL[/red] Error: {e}")

    console.print(
        f"\n[green]Done.[/green] {processed} file(s) processed, moved to data/processed/"
    )


if __name__ == "__main__":
    main()
