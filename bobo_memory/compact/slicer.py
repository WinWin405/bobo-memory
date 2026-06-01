"""
calculate_keep_index — find the cut point that preserves N tail tokens.

Mirrors calculateMessagesToKeepIndex() from sessionMemoryCompact.ts:
  - Scan messages from the END.
  - Keep accumulating until min_tail_tokens is exceeded.
  - Return the index of the first message to keep.
"""

from __future__ import annotations
from typing import Any


def _msg_tokens(msg: dict[str, Any]) -> int:
    content = msg.get("content") or ""
    if isinstance(content, str):
        return max(1, len(content) // 4)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                total += max(1, len(str(block.get("text") or "")) // 4)
        return max(1, total)
    return 1


def calculate_keep_index(
    messages: list[dict[str, Any]],
    *,
    min_tail_tokens: int = 10_000,
) -> int:
    """Return the index of the first message to keep in the compacted list.

    Messages at index 0 (system prompt) are always excluded from this scan —
    the caller is responsible for preserving messages[:1] separately.

    Args:
        messages:        Full message list.
        min_tail_tokens: Minimum tokens to preserve from the tail.

    Returns:
        Index into *messages* such that messages[idx:] contains at least
        min_tail_tokens tokens. Returns 1 if the entire tail fits.
    """
    if not messages:
        return 0

    accumulated = 0
    for i in range(len(messages) - 1, 0, -1):
        accumulated += _msg_tokens(messages[i])
        if accumulated >= min_tail_tokens:
            return i
    return 1  # keep everything from index 1 onwards


def truncate_head_for_ptl_retry(
    messages: list[dict[str, Any]],
    *,
    rounds_to_drop: int = 2,
) -> list[dict[str, Any]]:
    """Drop the oldest *rounds_to_drop* API round-trips from the head.

    A "round trip" = one user message + one assistant message (2 messages).
    System message at index 0 is always preserved.

    Used as a last-resort PTL (PromptTooLong) retry strategy.
    """
    system = messages[:1]
    tail = messages[1:]
    drop = min(rounds_to_drop * 2, len(tail))
    return system + tail[drop:]
