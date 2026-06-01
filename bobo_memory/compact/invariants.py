"""
adjust_index_to_preserve_invariants — prevent cutting mid tool_use/tool_result chain.

Mirrors adjustIndexToPreserveAPIInvariants() from sessionMemoryCompact.ts.

Anthropic's API requires:
  - Every tool_use block in an assistant message MUST be followed by a
    tool_result block in the next user message.
  - No orphaned tool_result without a preceding tool_use.

If the proposed keep_idx would split a tool_use/tool_result pair,
we move keep_idx backwards (toward the head) until we find a clean cut point.
"""

from __future__ import annotations
from typing import Any


def _has_tool_use(msg: dict[str, Any]) -> bool:
    """Return True if this assistant message contains a tool_use block."""
    content = msg.get("content") or []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_calls"):
                return True
    if msg.get("tool_calls"):
        return True
    return False


def _has_tool_result(msg: dict[str, Any]) -> bool:
    """Return True if this user/tool message contains tool_result content."""
    content = msg.get("content") or []
    if msg.get("role") == "tool":
        return True
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return True
    return False


def _is_thinking(msg: dict[str, Any]) -> bool:
    """Return True if this message contains a thinking block."""
    content = msg.get("content") or []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                return True
    return False


def adjust_index_to_preserve_invariants(
    messages: list[dict[str, Any]],
    keep_idx: int,
) -> int:
    """Return an adjusted keep_idx that does not split any tool chain.

    Scans backwards from keep_idx until:
      - messages[keep_idx - 1] is NOT an assistant message with tool_use,
      - messages[keep_idx] is NOT a tool_result,
      - messages[keep_idx] is NOT a thinking continuation.

    Always returns at least 1 (never touches messages[0] system prompt).
    """
    idx = keep_idx

    while idx > 1:
        # Check: would keeping from idx split a tool_use/result pair?
        prev = messages[idx - 1] if idx > 0 else None
        curr = messages[idx] if idx < len(messages) else None

        split_tool_chain = (
            prev is not None
            and prev.get("role") == "assistant"
            and _has_tool_use(prev)
            and curr is not None
            and _has_tool_result(curr)
        )

        orphan_tool_result = (
            curr is not None
            and _has_tool_result(curr)
            and (prev is None or not _has_tool_use(prev))
        )

        thinking_continuation = curr is not None and _is_thinking(curr)

        if split_tool_chain or orphan_tool_result or thinking_continuation:
            idx -= 1
        else:
            break

    return max(1, idx)
