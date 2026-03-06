"""CSV/Excel parser — converts tabular data to natural language sentences."""

from pathlib import Path
import pandas as pd


def parse_csv(path: Path) -> tuple[str, dict]:
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)

    # Convert each row to a natural language sentence
    sentences = []
    columns = list(df.columns)
    for _, row in df.iterrows():
        parts = []
        for col in columns:
            val = row[col]
            if pd.notna(val):
                parts.append(f"{col}: {val}")
        if parts:
            sentences.append(", ".join(parts))

    text = "\n".join(sentences)
    metadata = {
        "source_file": path.name,
        "file_type": "csv" if suffix == ".csv" else "excel",
        "rows": len(df),
        "columns": ", ".join(columns),
    }
    return text, metadata
