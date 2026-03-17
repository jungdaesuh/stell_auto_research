"""Append-only results.tsv logger — autoresearch style."""

from __future__ import annotations

from pathlib import Path

HEADER = "candidate_id\tcombined_score\tfield_error\tstatus\tdescription\n"


def ensure_results_tsv(path: Path) -> None:
    """Create results.tsv with header if it doesn't exist."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HEADER)


def append_result(
    path: Path,
    candidate_id: str,
    combined_score: float,
    field_error: float | None,
    status: str,
    description: str,
) -> None:
    """Append one line to results.tsv."""
    fe_str = f"{field_error:.6f}" if field_error is not None else "-"
    safe_desc = description.replace("\t", " ").replace("\n", " ")
    line = f"{candidate_id}\t{combined_score:.6f}\t{fe_str}\t{status}\t{safe_desc}\n"
    with open(path, "a") as f:
        f.write(line)
