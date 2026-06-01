"""
ContextPack — standardised recall output contract.

A ContextPack is what client.recall() returns. It bundles:
  - the matched memory files
  - inline citations traced to raw/ sources
  - a formatted markdown string ready to inject into an LLM prompt
  - token count and truncation metadata

This is the Python equivalent of the proposed "Context Pack" improvement.
It makes the recall output portable across OpenAI/Anthropic/LangChain/CrewAI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class Citation(BaseModel):
    """A single source citation for a memory file."""

    layer: str = ""
    memory_path: str = ""
    raw_source_ids: list[str] = []
    source_urls: list[str] = []


class MemoryFileRef(BaseModel):
    """Reference to a recalled memory file."""

    filename: str
    path: str
    summary: str = ""
    tags: list[str] = []
    score: float = 0.0
    mtime: float = 0.0
    content: str = ""  # filled when token_budget allows full read
    layer: str = ""
    citation: Citation | None = None


class ContextPack(BaseModel):
    """Standardised output of a recall() call."""

    query: str
    files: list[MemoryFileRef] = []
    formatted_markdown: str = ""
    citations: list[Citation] = []
    total_tokens: int = 0
    truncated: bool = False
    mode: str = "bm25"
    meta: dict[str, Any] = {}

    def format_for_prompt(self, *, include_citations: bool = True) -> str:
        """Return a string suitable for direct inclusion in a system/user prompt."""
        if self.formatted_markdown:
            return self.formatted_markdown
        return self._build_markdown(include_citations=include_citations)

    def _build_markdown(self, *, include_citations: bool = True) -> str:
        parts: list[str] = [f"## Recalled memories for: {self.query}\n"]
        for ref in self.files:
            parts.append(f"### [{ref.filename}]({ref.path})")
            if ref.summary:
                parts.append(f"*{ref.summary}*\n")
            if ref.content:
                parts.append(ref.content)
            parts.append("")

        if include_citations and self.citations:
            parts.append("---\n**Sources**\n")
            for c in self.citations:
                for sid in c.raw_source_ids:
                    parts.append(f"- `{sid}`")
        return "\n".join(parts)


def build_context_pack(
    query: str,
    refs: list[MemoryFileRef],
    *,
    token_budget: int = 8000,
    include_citations: bool = True,
    mode: str = "bm25",
) -> ContextPack:
    """Assemble a ContextPack from a list of refs, respecting token_budget."""
    files_in_budget: list[MemoryFileRef] = []
    tokens_used = 0
    truncated = False
    citations: list[Citation] = []

    for ref in refs:
        est_tokens = len(ref.content.encode("utf-8")) // 4 if ref.content else 50
        if tokens_used + est_tokens > token_budget and files_in_budget:
            truncated = True
            break
        files_in_budget.append(ref)
        tokens_used += est_tokens
        if ref.citation:
            citations.append(ref.citation)

    pack = ContextPack(
        query=query,
        files=files_in_budget,
        citations=citations,
        total_tokens=tokens_used,
        truncated=truncated,
        mode=mode,
    )
    pack.formatted_markdown = pack._build_markdown(include_citations=include_citations)
    return pack
