"""Snapshot packager — convenience wrapper for creating distributable snapshots."""

from __future__ import annotations
from pathlib import Path


def pack_snapshot(
    agent_type: str,
    project_root: Path,
    *,
    scope: str = "user",
    out_dir: Path | str | None = None,
) -> dict:
    """Export agent memory as a snapshot directory.

    If out_dir is None, writes to .bobo/snapshots/<agent_type>/ in the project.
    """
    from bobo_memory.snapshot.manager import SnapshotManager
    from bobo_memory.core.paths import snapshot_dir

    mgr = SnapshotManager(agent_type, project_root)
    target = Path(out_dir) if out_dir else snapshot_dir(agent_type, project_root)
    return mgr.export(target, scope=scope)
