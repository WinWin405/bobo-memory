"""should_compact — token budget threshold check."""

from __future__ import annotations
from typing import Any


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(str(block.get("text") or "")) // 4
    return total


def should_compact(
    messages: list[dict[str, Any]],
    *,
    token_budget: int = 180_000,
) -> bool:
    """Return True when the message list has grown past *token_budget* tokens.

    This is intentionally conservative — we estimate 1 token ≈ 4 chars.
    Callers can also set token_budget=0 to force compaction (manual /compact).
    """
    if token_budget == 0:
        return True
    return _estimate_tokens(messages) >= token_budget
