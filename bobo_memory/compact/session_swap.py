"""
compact_with_session_memory — use existing Session Memory as the compact summary.

When Session Memory has already been extracted (by the background subagent),
we can use it directly as the SummaryMessage without calling the LLM again.
This is the key cost-saving insight from the reference implementation.
"""

from __future__ import annotations
from pathlib import Path
from typing import Any


def strip_images_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove image content blocks to prevent PTL on the summary request."""
    result = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_content = [
                block for block in content
                if not (isinstance(block, dict) and block.get("type") in ("image", "image_url"))
            ]
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)
    return result


def strip_reinjected_attachments(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove repeated tool/attachment reinjections to slim the message list."""
    result = []
    seen_tool_schemas: set[str] = set()
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_content = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_schema":
                    key = block.get("name", "")
                    if key in seen_tool_schemas:
                        continue
                    seen_tool_schemas.add(key)
                new_content.append(block)
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)
    return result


def compact_with_session_memory(
    messages: list[dict[str, Any]],
    *,
    session_path: Path | str | None = None,
) -> str | None:
    """Return session memory content to use as compaction summary.

    Returns None if no session memory file exists (caller should fall back
    to requesting a live summary from the LLM).

    Also applies strip_images and strip_reinjected_attachments as a side effect
    to help avoid PTL if the caller later uses the pruned messages list.
    """
    if session_path is None:
        return None

    sp = Path(session_path)
    if not sp.exists():
        return None

    try:
        content = sp.read_text(encoding="utf-8")
    except OSError:
        return None

    if not content.strip():
        return None

    return content
