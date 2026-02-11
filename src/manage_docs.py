"""CLI tool to inspect and delete documents from the tree store."""

from rich.console import Console
from rich.prompt import Prompt

from . import tree_store

console = Console()


def _parse_selection(selection: str, max_val: int) -> list[int]:
    """Parse user selection like '1,3,5' or '1-5' or 'all'."""
    selection = selection.strip().lower()
    if selection == "all":
        return list(range(1, max_val + 1))
    result: list[int] = []
    for part in selection.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            result.extend(range(int(a), int(b) + 1))
        else:
            result.append(int(part))
    return [i for i in result if 1 <= i <= max_val]


def main():
    docs = tree_store.list_trees()
    if not docs:
        console.print("[yellow]No documents indexed yet.[/yellow]")
        console.print("Drop files in data/drop/ and run: [bold]uv run ingest[/bold]")
        return

    # Numbered list so the selection number is always visible (no table column collapse)
    console.print(f"\n[bold]Indexed Documents ({len(docs)})[/bold]\n")
    for i, doc in enumerate(docs, 1):
        name = doc.get("doc_name") or doc.get("doc_id") or "?"
        console.print(f"  [cyan]{i:>3}[/cyan]  {name}")
    console.print()

    # Ask for deletion
    selection = Prompt.ask(
        "Enter number(s) to delete (e.g. 1, 3-5, or all), or 'none' to cancel",
        default="none",
    ).strip()
    if selection.lower() in ("none", "n", ""):
        console.print("[green]No documents deleted.[/green]")
        return

    indices = _parse_selection(selection, len(docs))
    if not indices:
        console.print("[red]No valid selection.[/red]")
        return

    console.print(
        f"\nYou are about to delete [bold]{len(indices)}[/bold] document(s). "
        "This removes their tree indexes permanently."
    )
    confirm = Prompt.ask("Type 'yes' to confirm", default="no")
    if confirm.lower() != "yes":
        console.print("[green]Aborted. No documents deleted.[/green]")
        return

    for idx in indices:
        doc = docs[idx - 1]
        deleted = tree_store.delete_tree(doc["doc_id"])
        if deleted:
            console.print(f"  Removed [bold]{doc['doc_name']}[/bold] ({doc['doc_id']})")
        else:
            console.print(f"  [red]Failed to remove {doc['doc_id']}[/red]")

    console.print(f"\n[green]Done.[/green] Removed {len(indices)} document(s).")


if __name__ == "__main__":
    main()
