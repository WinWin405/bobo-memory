"""
ManifestCache — mtime-aware in-memory cache of memory file headers.

Avoids full disk scans on every recall() call. The cache stores:
  - filename
  - one-line summary (from MEMORY.md index line or frontmatter description)
  - mtime (for freshness check)
  - tags (from frontmatter)

Design principles:
  - Cache is NOT the source of truth — only markdown files are.
  - Deleting cache (cache/manifest.json) has no effect on memory.
  - Per-directory caches are keyed by (memory_dir, filename).
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class ManifestEntry:
    filename: str
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    mtime: float = 0.0
    path: str = ""      # absolute path string


class ManifestCache:
    """Thread-safe, mtime-aware manifest cache for a memory directory."""

    def __init__(self, memory_dir: Path, persist_path: Path | None = None) -> None:
        self.memory_dir = Path(memory_dir)
        self.persist_path = persist_path
        self._entries: dict[str, ManifestEntry] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def get_all(self) -> list[ManifestEntry]:
        """Return all (refreshed) entries for this memory directory."""
        with self._lock:
            self._refresh()
            return list(self._entries.values())

    def get(self, filename: str) -> ManifestEntry | None:
        with self._lock:
            self._refresh_file(filename)
            return self._entries.get(filename)

    def invalidate(self, filename: str | None = None) -> None:
        """Remove one or all entries from the in-memory cache."""
        with self._lock:
            if filename:
                self._entries.pop(filename, None)
            else:
                self._entries.clear()

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _refresh(self) -> None:
        """Scan the directory and refresh stale entries."""
        if not self.memory_dir.exists():
            return
        seen: set[str] = set()
        for f in self.memory_dir.glob("*.md"):
            if f.name.startswith(".") or f.name == "MEMORY.md":
                continue
            seen.add(f.name)
            mtime = f.stat().st_mtime
            existing = self._entries.get(f.name)
            if existing and existing.mtime == mtime:
                continue
            self._entries[f.name] = self._read_entry(f)

        # Remove entries for deleted files
        for key in list(self._entries.keys()):
            if key not in seen:
                del self._entries[key]

    def _refresh_file(self, filename: str) -> None:
        f = self.memory_dir / filename
        if not f.exists():
            self._entries.pop(filename, None)
            return
        mtime = f.stat().st_mtime
        existing = self._entries.get(filename)
        if existing and existing.mtime == mtime:
            return
        self._entries[filename] = self._read_entry(f)

    def _read_entry(self, f: Path) -> ManifestEntry:
        """Extract summary and tags from file's YAML frontmatter."""
        summary = ""
        tags: list[str] = []
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 2:
                    front = parts[1]
                    # Extract tags
                    for line in front.splitlines():
                        if line.startswith("tags:"):
                            raw = line[5:].strip().strip("[]")
                            tags = [t.strip() for t in raw.split(",") if t.strip()]
                    # Try to get description from frontmatter
                    for line in front.splitlines():
                        if line.startswith("description:"):
                            summary = line[12:].strip().strip('"').strip("'")
                            break
            # Fallback: use first non-empty non-heading line
            if not summary:
                body = content.split("---", 2)[-1] if "---" in content else content
                for line in body.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        summary = line[:100]
                        break
        except OSError:
            pass

        return ManifestEntry(
            filename=f.name,
            summary=summary,
            tags=tags,
            mtime=f.stat().st_mtime if f.exists() else 0.0,
            path=str(f),
        )

    # ------------------------------------------------------------------ #
    # Persistence (optional)                                               #
    # ------------------------------------------------------------------ #

    def load_persistent(self) -> None:
        """Load entries from disk cache (if present)."""
        if not self.persist_path or not self.persist_path.exists():
            return
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            with self._lock:
                for key, val in data.items():
                    e = ManifestEntry(**val)
                    # Only use cached entry if file hasn't changed
                    f = self.memory_dir / key
                    if f.exists() and f.stat().st_mtime == e.mtime:
                        self._entries[key] = e
        except Exception:
            pass

    def save_persistent(self) -> None:
        """Save current entries to disk cache."""
        if not self.persist_path:
            return
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = {k: asdict(v) for k, v in self._entries.items()}
            self.persist_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Module-level registry                                                        #
# --------------------------------------------------------------------------- #

_caches: dict[str, ManifestCache] = {}
_registry_lock = threading.Lock()


def get_cache(memory_dir: Path, persist_path: Path | None = None) -> ManifestCache:
    """Return (creating if needed) the ManifestCache for a directory."""
    key = str(memory_dir.resolve())
    with _registry_lock:
        if key not in _caches:
            cache = ManifestCache(memory_dir, persist_path)
            cache.load_persistent()
            _caches[key] = cache
        return _caches[key]


def build_manifest_text(entries: list[ManifestEntry]) -> str:
    """Format manifest entries as a text block for LLM sideQuery."""
    lines = [f"- {e.filename}: {e.summary}" + (f" [tags: {', '.join(e.tags)}]" if e.tags else "")
             for e in entries]
    return "\n".join(lines)
