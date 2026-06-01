"""Chat adapter — converts OpenAI/Anthropic message arrays into RawDoc."""

from __future__ import annotations

from typing import Any

from bobo_memory.stream.adapters.base import RawDoc, StreamAdapter


class ChatAdapter(StreamAdapter):
    """Parses a list of chat messages into a single RawDoc transcript."""

    name = "chat"

    def parse(self, payload: Any, **kwargs) -> list[RawDoc]:
        messages = payload if isinstance(payload, list) else []
        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            if isinstance(content, list):
                content = " ".join(
                    block.get("text", "") for block in content if isinstance(block, dict)
                )
            lines.append(f"**{role}**: {content}")

        body = "\n\n".join(lines)
        title = kwargs.get("title") or f"Chat transcript ({len(messages)} messages)"
        return [RawDoc(title=title, body=body, adapter=self.name)]
