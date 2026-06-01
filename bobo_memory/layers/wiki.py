"""
WikiLayer — LLM-wiki style knowledge base layer.

Directory layout:
  .bobo/memory/wiki/
    index.md          — content-oriented catalog (links + one-line summaries)
    log.md            — chronological append-only event log
    entities/         — named entities (people, projects, products, ...)
    concepts/         — abstract concepts and ideas
    sources/          — source summary pages (one per ingested raw doc)

The LLM owns this layer entirely. The module provides:
  - build_prompt(): ingest + query + lint workflow instructions
  - ensure_dirs(): creates the subdirectory structure
"""

from __future__ import annotations

import os
from pathlib import Path

from bobo_memory.core.paths import wiki_dir
from bobo_memory.core.prompts import WIKI_INGEST_PROMPT, WIKI_QUERY_PROMPT, WIKI_LINT_PROMPT
from bobo_memory.layers.base import MemoryLayer


_WIKI_INDEX_BOOTSTRAP = """\
# Wiki Index

This is your wiki knowledge base. Each page covers one entity, concept, or source.

## Sources
(source pages will appear here as you ingest documents)

## Entities
(entity pages will appear here)

## Concepts
(concept pages will appear here)
"""

_WIKI_LOG_BOOTSTRAP = """\
# Wiki Log

Chronological record of ingests, queries, and maintenance passes.

"""


class WikiLayer(MemoryLayer):
    """LLM-maintained wiki knowledge base."""

    name = "wiki"

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    @property
    def memory_dir(self) -> Path:
        return wiki_dir(self.project_root)

    def is_enabled(self) -> bool:
        return os.environ.get("BOBO_DISABLE_WIKI", "").lower() not in ("1", "true", "yes")

    def ensure_dirs(self) -> None:
        from bobo_memory.core.atomic import ensure_dir, atomic_write
        base = self.memory_dir
        ensure_dir(base)
        ensure_dir(base / "entities")
        ensure_dir(base / "concepts")
        ensure_dir(base / "sources")

        index = base / "index.md"
        if not index.exists():
            atomic_write(index, _WIKI_INDEX_BOOTSTRAP)

        log = base / "log.md"
        if not log.exists():
            atomic_write(log, _WIKI_LOG_BOOTSTRAP)

    def build_prompt(self) -> str:
        if not self.is_enabled():
            return ""

        try:
            self.ensure_dirs()
        except OSError:
            pass

        index_path = self.memory_dir / "index.md"
        index_content = ""
        if index_path.exists():
            from bobo_memory.core.memdir import truncate_entrypoint_content
            raw = index_path.read_text(encoding="utf-8")
            t = truncate_entrypoint_content(raw)
            index_content = t.content

        sections = [
            "# Wiki Knowledge Base",
            "",
            f"Wiki directory: `{self.memory_dir}`",
            "",
            WIKI_INGEST_PROMPT,
            "",
            WIKI_QUERY_PROMPT,
            "",
            WIKI_LINT_PROMPT,
            "",
            "## wiki/index.md",
            "",
            index_content if index_content else "(empty — add sources to build the wiki)",
        ]
        return "\n".join(sections)
