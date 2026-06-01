"""Web clipper adapter — compatible with Obsidian Web Clipper markdown output."""

from __future__ import annotations

import re
from typing import Any

from bobo_memory.stream.adapters.base import RawDoc, StreamAdapter
from bobo_memory.stream.adapters.markdown import _extract_title


class WebClipperAdapter(StreamAdapter):
    """Parses Obsidian Web Clipper markdown (with YAML frontmatter)."""

    name = "web_clipper"

    def parse(self, payload: Any, **kwargs) -> list[RawDoc]:
        text = str(payload)
        tags: list[str] = []
        metadata: dict = {}

        # Strip YAML frontmatter
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                front_raw = parts[1]
                text = parts[2].lstrip()
                for line in front_raw.splitlines():
                    if line.startswith("tags:"):
                        raw_tags = line[5:].strip().strip("[]").split(",")
                        tags = [t.strip().strip('"').strip("'") for t in raw_tags if t.strip()]
                    elif ":" in line:
                        k, _, v = line.partition(":")
                        metadata[k.strip()] = v.strip()

        title = (
            metadata.get("title")
            or _extract_title(text)
            or "web-clip"
        )
        source_url = metadata.get("url") or metadata.get("source") or ""
        metadata["source_url"] = source_url

        return [
            RawDoc(
                title=title,
                body=text,
                tags=tags,
                metadata=metadata,
                adapter=self.name,
            )
        ]
