"""
Compact boundary messages — SystemCompactBoundaryMessage + post-compact attachments.

Mirrors createCompactBoundaryMessage() and createPostCompactFileAttachments()
from the reference implementation.

After compaction the reconstructed message list looks like:
  [system_msg, compact_boundary_msg, *tail_messages, *post_compact_attachments]
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any


def create_compact_boundary(summary: str | None) -> dict[str, Any]:
    """Create the SystemCompactBoundaryMessage that replaces the dropped messages.

    This is a synthetic system-role message that tells the LLM:
      "Context was compacted. Here is the summary of what happened before."
    """
    ts = datetime.now(tz=timezone.utc).isoformat()
    summary_text = summary or "(no summary available)"

    return {
        "role": "system",
        "content": (
            f"[Context compacted at {ts}]\n\n"
            "The conversation history above this point has been summarised "
            "to save context space. Here is the summary:\n\n"
            f"{summary_text}\n\n"
            "--- End of compacted context ---"
        ),
        "_bobo_compact_boundary": True,
    }


def build_post_compact_attachments(
    *,
    tool_specs: list[Any] | None = None,
    active_files: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build synthetic messages that re-declare tools and active files.

    After compaction the LLM may have lost sight of available tools (their
    schemas were in the dropped messages). This function re-injects them as
    a user message so the model "wakes up" with full capabilities.

    Returns:
        A list of 0–1 synthetic messages to append after the tail messages.
    """
    if not tool_specs and not active_files:
        return []

    parts: list[str] = ["[Post-compact capability re-declaration]"]

    if tool_specs:
        try:
            from bobo_memory.tools.adapters import to_openai_tools
            tools_json = to_openai_tools(tool_specs)
            import json
            parts.append("\nAvailable memory tools:")
            for t in tools_json:
                fn = t.get("function", {})
                parts.append(f"  - {fn.get('name', '?')}: {fn.get('description', '')}")
        except Exception:
            pass

    if active_files:
        parts.append("\nCurrently active files:")
        for f in active_files:
            parts.append(f"  - {f}")

    return [
        {
            "role": "user",
            "content": "\n".join(parts),
            "_bobo_post_compact": True,
        }
    ]
