"""
find_relevant_memories — BM25 + manifest recall.

Mirrors the design of findRelevantMemories() from the reference implementation.

Strategy:
  1. Collect ManifestEntry objects from all requested layers.
  2. Filter out already_surfaced entries.
  3. Run BM25 over (filename + summary + tags).
  4. Take top-k by score.
  5. Read file content (up to token_budget).
  6. Extract citations from frontmatter.
  7. Return a ContextPack.

Mode "manifest_only" returns the entries WITHOUT reading file content —
the caller LLM can then do its own sideQuery to select which files to read.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi

from bobo_memory.core.context_pack import (
    Citation,
    ContextPack,
    MemoryFileRef,
    build_context_pack,
)
from bobo_memory.core.manifest_cache import ManifestEntry, get_cache
from bobo_memory.core.paths import (
    agent_memory_dir,
    auto_memory_dir,
    cache_dir,
    team_memory_dir,
    wiki_dir,
)


def _layer_to_dir(
    layer: str,
    *,
    project_root: Path,
    agent_type: str,
    scope: str,
) -> Path | None:
    if layer == "agent":
        return agent_memory_dir(agent_type, scope, project_root=project_root)
    if layer == "auto":
        return auto_memory_dir(project_root)
    if layer == "wiki":
        return wiki_dir(project_root)
    if layer == "team":
        return team_memory_dir(project_root)
    return None


def _tokenise(text: str) -> list[str]:
    """Simple whitespace + punctuation tokeniser for BM25."""
    return re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", text.lower())


def _extract_citations(file_path: Path, layer: str) -> Citation:
    """Read frontmatter sources: field and return a Citation."""
    sources: list[str] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 2:
                for line in parts[1].splitlines():
                    line = line.strip()
                    if line.startswith("- ") and "raw/" in line:
                        sources.append(line[2:].strip())
    except OSError:
        pass
    return Citation(
        layer=layer,
        memory_path=str(file_path),
        raw_source_ids=sources,
    )


def find_relevant_memories(
    *,
    query: str,
    k: int = 5,
    layers: list[str],
    project_root: Path,
    agent_type: str,
    scope: str,
    already_surfaced: list[str],
    recent_tools: list[str],
    token_budget: int = 8000,
    mode: str = "bm25",
) -> ContextPack:
    """Main recall function — returns a ContextPack."""

    already_surfaced_set = set(already_surfaced)

    # Collect candidates from all layers
    candidates: list[tuple[ManifestEntry, str]] = []  # (entry, layer_name)

    persist = cache_dir(project_root) / "manifest.json"

    for layer in layers:
        mem_dir = _layer_to_dir(layer, project_root=project_root, agent_type=agent_type, scope=scope)
        if mem_dir is None or not mem_dir.exists():
            continue

        layer_persist = persist.parent / f"manifest_{layer}.json" if persist else None
        cache = get_cache(mem_dir, layer_persist)
        entries = cache.get_all()

        for entry in entries:
            if entry.path in already_surfaced_set or entry.filename in already_surfaced_set:
                continue
            candidates.append((entry, layer))

    if not candidates:
        return ContextPack(query=query, files=[], mode=mode)

    if mode == "manifest_only":
        # Return without reading file content — LLM selects
        refs = [
            MemoryFileRef(
                filename=e.filename,
                path=e.path,
                summary=e.summary,
                tags=e.tags,
                mtime=e.mtime,
                layer=layer,
                score=1.0,
            )
            for e, layer in candidates[:k]
        ]
        return build_context_pack(query, refs, token_budget=token_budget, mode=mode)

    # BM25 ranking over (filename + summary + tags)
    corpus: list[list[str]] = []
    for entry, _ in candidates:
        doc_text = " ".join([
            entry.filename.replace("-", " ").replace("_", " "),
            entry.summary,
            " ".join(entry.tags),
        ])
        corpus.append(_tokenise(doc_text))

    query_tokens = _tokenise(query)
    if not query_tokens:
        query_tokens = ["_"]

    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(query_tokens)

    # Sort by score, take top-k
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:k]

    refs: list[MemoryFileRef] = []
    for idx, score in ranked:
        entry, layer = candidates[idx]
        fpath = Path(entry.path)
        content = ""
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass

        citation = _extract_citations(fpath, layer)
        refs.append(
            MemoryFileRef(
                filename=entry.filename,
                path=entry.path,
                summary=entry.summary,
                tags=entry.tags,
                mtime=entry.mtime,
                layer=layer,
                score=float(score),
                content=content,
                citation=citation,
            )
        )

    return build_context_pack(query, refs, token_budget=token_budget, mode=mode)
