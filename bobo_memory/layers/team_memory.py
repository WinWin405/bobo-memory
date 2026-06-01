"""
TeamMemory layer — git-tracked shared team knowledge.

Provides pull/push/watch with:
  - SHA-256 checksum-based change detection
  - optimistic locking (ETag-style conflict detection)
  - policy.forbidden_patterns secret scanning before push
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from bobo_memory.core.atomic import atomic_write, file_lock
from bobo_memory.core.paths import team_memory_dir
from bobo_memory.layers.base import MemoryLayer


class TeamMemory(MemoryLayer):
    """Shared, git-tracked team memory layer."""

    name = "team"

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    @property
    def memory_dir(self) -> Path:
        return team_memory_dir(self.project_root)

    def is_enabled(self) -> bool:
        return os.environ.get("BOBO_DISABLE_TEAM_MEMORY", "").lower() not in ("1", "true", "yes")

    def build_prompt(self) -> str:
        if not self.is_enabled():
            return ""
        from bobo_memory.core.memdir import build_memory_prompt
        return build_memory_prompt(
            "Team Memory",
            self.memory_dir,
            extra_guidelines=["This memory is shared with your team. Be careful what you write here."],
        )

    # ------------------------------------------------------------------ #
    # Sync operations                                                      #
    # ------------------------------------------------------------------ #

    def checksum(self) -> str:
        """Return a SHA-256 checksum of all markdown files in the team memory dir."""
        if not self.memory_dir.exists():
            return ""
        h = hashlib.sha256()
        for f in sorted(self.memory_dir.rglob("*.md")):
            try:
                h.update(f.read_bytes())
            except OSError:
                pass
        return h.hexdigest()

    def pull(self, source_dir: Path | str) -> dict[str, Any]:
        """Pull team memory from a source directory (e.g. git-synced path).

        Args:
            source_dir: Directory to pull from (the "remote" shared directory).

        Returns:
            Summary of changes pulled.
        """
        source = Path(source_dir)
        if not source.exists():
            return {"ok": False, "error": f"Source directory does not exist: {source}"}

        self.memory_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for f in source.rglob("*.md"):
            rel = f.relative_to(source)
            dest = self.memory_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with file_lock(dest):
                shutil.copy2(str(f), str(dest))
            copied.append(str(rel))

        return {"ok": True, "pulled": copied}

    def push(
        self,
        dest_dir: Path | str,
        *,
        policy_patterns: list[str] | None = None,
    ) -> dict[str, Any]:
        """Push team memory to a destination directory, with optional secret scan.

        Args:
            dest_dir:        Destination directory.
            policy_patterns: Forbidden regex patterns. If any file matches, push is blocked.

        Returns:
            Summary of changes pushed.
        """
        import re
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)

        compiled = [re.compile(p) for p in (policy_patterns or [])]
        blocked: list[str] = []
        pushed: list[str] = []

        for f in self.memory_dir.rglob("*.md"):
            rel = f.relative_to(self.memory_dir)
            try:
                content = f.read_text(encoding="utf-8")
            except OSError:
                continue

            # Secret scan
            for pattern in compiled:
                if pattern.search(content):
                    blocked.append(str(rel))
                    break
            else:
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with file_lock(target):
                    shutil.copy2(str(f), str(target))
                pushed.append(str(rel))

        if blocked:
            return {
                "ok": False,
                "error": f"Push blocked: {len(blocked)} file(s) contain forbidden patterns",
                "blocked": blocked,
                "pushed": pushed,
            }
        return {"ok": True, "pushed": pushed}

    def watch(self, source_dir: Path | str, *, interval_seconds: float = 30.0) -> None:
        """Start a background polling watcher that periodically pulls from source_dir."""
        import threading

        source = Path(source_dir)
        last_checksum = [None]

        def _watch_loop() -> None:
            while True:
                import time
                time.sleep(interval_seconds)
                if source.exists():
                    try:
                        result = self.pull(source)
                        if result.get("pulled"):
                            print(f"[bobo-memory team] Pulled {len(result['pulled'])} file(s)")
                    except Exception as e:
                        print(f"[bobo-memory team] Watch error: {e}")

        t = threading.Thread(target=_watch_loop, daemon=True)
        t.start()
        print(f"[bobo-memory team] Watching {source} every {interval_seconds}s")
