"""
SnapshotManager — agent memory as a distributable asset.

Mirrors src/tools/AgentTool/agentMemorySnapshot.ts.

Files:
  .bobo/snapshots/<agent_type>/snapshot.json        — {"updatedAt": "ISO8601"}
  .bobo/snapshots/<agent_type>/.snapshot-synced.json — {"syncedFrom": "ISO8601"}
  .bobo/snapshots/<agent_type>/*.md                 — snapshot memory pages

States returned by check():
  "none"          — no snapshot exists for this agent
  "initialize"    — snapshot exists but local memory has no .md files
  "prompt_update" — local memory exists but is behind the snapshot version
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from bobo_memory.core.atomic import atomic_write
from bobo_memory.core.paths import agent_memory_dir, snapshot_dir


SnapshotAction = Literal["none", "initialize", "prompt_update"]

_SNAPSHOT_META = "snapshot.json"
_SYNCED_META = ".snapshot-synced.json"


class SnapshotManager:
    """Manages the lifecycle of agent memory snapshots."""

    def __init__(self, agent_type: str, project_root: Path) -> None:
        self.agent_type = agent_type
        self.project_root = project_root

    def _snapshot_dir(self) -> Path:
        return snapshot_dir(self.agent_type, self.project_root)

    def _snapshot_meta_path(self) -> Path:
        return self._snapshot_dir() / _SNAPSHOT_META

    def _synced_meta_path(self, mem_dir: Path) -> Path:
        return mem_dir / _SYNCED_META

    def _read_snapshot_meta(self) -> dict | None:
        p = self._snapshot_meta_path()
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _read_synced_meta(self, mem_dir: Path) -> dict | None:
        p = self._synced_meta_path(mem_dir)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def check(
        self,
        scope: str = "user",
    ) -> SnapshotAction:
        """Return the appropriate action for the current snapshot state."""
        meta = self._read_snapshot_meta()
        if meta is None:
            return "none"

        mem_dir = agent_memory_dir(self.agent_type, scope, project_root=self.project_root)
        has_md = mem_dir.exists() and any(
            f for f in mem_dir.glob("*.md") if not f.name.startswith(".")
        )

        if not has_md:
            return "initialize"

        synced = self._read_synced_meta(mem_dir)
        if synced is None:
            return "prompt_update"

        snapshot_updated = meta.get("updatedAt", "")
        synced_from = synced.get("syncedFrom", "")
        if snapshot_updated > synced_from:
            return "prompt_update"

        return "none"

    def initialize(self, scope: str = "user") -> dict:
        """Copy snapshot pages into the local agent memory directory."""
        snap_dir = self._snapshot_dir()
        if not snap_dir.exists():
            return {"ok": False, "error": "No snapshot directory found"}

        mem_dir = agent_memory_dir(self.agent_type, scope, project_root=self.project_root)
        mem_dir.mkdir(parents=True, exist_ok=True)

        copied = []
        for f in snap_dir.glob("*.md"):
            dest = mem_dir / f.name
            shutil.copy2(str(f), str(dest))
            copied.append(f.name)

        self._write_synced_meta(mem_dir)
        return {"ok": True, "initialized": copied}

    def replace(self, scope: str = "user") -> dict:
        """Replace local memory with snapshot (destructive)."""
        snap_dir = self._snapshot_dir()
        if not snap_dir.exists():
            return {"ok": False, "error": "No snapshot directory found"}

        mem_dir = agent_memory_dir(self.agent_type, scope, project_root=self.project_root)
        mem_dir.mkdir(parents=True, exist_ok=True)

        # Delete existing .md files
        for f in mem_dir.glob("*.md"):
            if not f.name.startswith("."):
                f.unlink(missing_ok=True)

        # Copy from snapshot
        copied = []
        for f in snap_dir.glob("*.md"):
            dest = mem_dir / f.name
            shutil.copy2(str(f), str(dest))
            copied.append(f.name)

        self._write_synced_meta(mem_dir)
        return {"ok": True, "replaced": copied}

    def mark_synced(self, scope: str = "user") -> dict:
        """Update .snapshot-synced.json without changing memory content."""
        mem_dir = agent_memory_dir(self.agent_type, scope, project_root=self.project_root)
        self._write_synced_meta(mem_dir)
        return {"ok": True}

    def export(self, out_dir: Path | str, scope: str = "user") -> dict:
        """Package current agent memory as a snapshot for distribution."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

        mem_dir = agent_memory_dir(self.agent_type, scope, project_root=self.project_root)
        if not mem_dir.exists():
            return {"ok": False, "error": "No agent memory to export"}

        copied = []
        for f in mem_dir.glob("*.md"):
            if not f.name.startswith("."):
                shutil.copy2(str(f), str(out / f.name))
                copied.append(f.name)

        ts = datetime.now(tz=timezone.utc).isoformat()
        meta = {"updatedAt": ts, "agent_type": self.agent_type, "scope": scope}
        atomic_write(out / _SNAPSHOT_META, json.dumps(meta, indent=2))

        return {"ok": True, "exported": copied, "updatedAt": ts}

    def _write_synced_meta(self, mem_dir: Path) -> None:
        meta = self._read_snapshot_meta() or {}
        ts = meta.get("updatedAt") or datetime.now(tz=timezone.utc).isoformat()
        synced = {"syncedFrom": ts}
        atomic_write(self._synced_meta_path(mem_dir), json.dumps(synced, indent=2))
