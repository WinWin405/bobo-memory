"""Markdown file adapter — parses .md files into RawDoc objects."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bobo_memory.stream.adapters.base import RawDoc, StreamAdapter

# Linux NAME_MAX is 255; avoid treating long inline text as a path.
_MAX_PATH_CANDIDATE_LEN = 4096


def _existing_path(payload: str | Path) -> Path | None:
    """Return payload if it points to an existing file, else None."""
    if isinstance(payload, str):
        if "\n" in payload or len(payload) > _MAX_PATH_CANDIDATE_LEN:
            return None
    try:
        path = Path(str(payload))
        if path.exists():
            return path
    except OSError:
        return None
    return None


class MarkdownAdapter(StreamAdapter):
    """Accepts raw markdown text or a file path."""

    name = "markdown"

    def parse(self, payload: Any, **kwargs) -> list[RawDoc]:
        path = _existing_path(payload) if isinstance(payload, (str, Path)) else None
        if path is not None:
            text = path.read_text(encoding="utf-8")
            title = _extract_title(text) or path.stem
            return [RawDoc(title=title, body=text, adapter=self.name, original_path=str(path))]

        text = str(payload)
        title = _extract_title(text) or "untitled"
        return [RawDoc(title=title, body=text, adapter=self.name)]

    def parse_path(self, path: Path) -> list[RawDoc]:
        text = path.read_text(encoding="utf-8")
        title = _extract_title(text) or path.stem
        return [RawDoc(title=title, body=text, adapter=self.name, original_path=str(path))]


def _extract_title(text: str) -> str:
    """Extract H1 heading from markdown, or first non-empty line."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line:
            return line[:80]
    return ""
