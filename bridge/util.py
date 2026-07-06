"""Small pure helpers shared across modules (leaf — no bridge imports)."""
from __future__ import annotations


def chunk_message(text: str, size: int = 1900) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + size] for i in range(0, len(text), size)]
