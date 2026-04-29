"""Cheap token counter shared by the tier handlers (codegen/10).

Uses ``tiktoken`` cl100k_base when available; otherwise approximates with
``len(text) // 4``. Never raises — at worst it returns 1 for an empty
string so caller routing logic always has a positive int.
"""
from __future__ import annotations


def count_tokens(text: str) -> int:
    """Return an integer token count >= 1 for ``text``."""
    if not text:
        return 1
    try:
        import tiktoken  # type: ignore
        enc = tiktoken.get_encoding("cl100k_base")
        return max(1, len(enc.encode(text)))
    except Exception:
        return max(1, len(text) // 4)
