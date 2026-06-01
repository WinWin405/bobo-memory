"""
Scope-aware path resolution for all memory layers.

Mirrors the design of src/memdir/paths.ts from the reference implementation.

Three agent memory scopes:
  user    → ~/.bobo/memory/agent/<agent_type>/user/
  project → <project_root>/.bobo/memory/agent/<agent_type>/project/
  local   → <project_root>/.bobo/memory/agent/<agent_type>/local/
            (or $BOBO_REMOTE_MEMORY_DIR/projects/<git_root>/agent-memory-local/<agent_type>/)

Override env-vars (highest → lowest priority):
  BOBO_MEMORY_PATH_OVERRIDE   — sets the entire auto-memory root
  BOBO_REMOTE_MEMORY_DIR      — remote/cowork memory mount root
  BOBO_MEMORY_BASE            — local memory base (default: ~/.bobo)
"""

from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path


# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000

VALID_SCOPES = ("user", "project", "local")


# --------------------------------------------------------------------------- #
# Git root detection                                                           #
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=64)
def _git_root(cwd: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def sanitize_git_root(root: str | Path) -> str:
    """Convert an absolute path to a safe directory name (replaces path separators)."""
    s = str(root).replace("\\", "/").strip("/")
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)


def sanitize_agent_type(agent_type: str) -> str:
    """Replace characters unsafe for directory names (e.g. ':' in plugin namespaces)."""
    return agent_type.replace(":", "-").replace("/", "-").replace("\\", "-")


# --------------------------------------------------------------------------- #
# Memory base                                                                  #
# --------------------------------------------------------------------------- #

def memory_base() -> Path:
    """Return the base directory for user-scope memories.

    Priority:
      1. $BOBO_MEMORY_BASE
      2. ~/.bobo
    """
    remote = os.environ.get("BOBO_REMOTE_MEMORY_DIR")
    if remote:
        return Path(remote)
    custom = os.environ.get("BOBO_MEMORY_BASE")
    if custom:
        return Path(custom)
    return Path.home() / ".bobo"


# --------------------------------------------------------------------------- #
# Auto Memory path                                                              #
# --------------------------------------------------------------------------- #

def auto_memory_dir(project_root: Path) -> Path:
    """Return the Auto Memory directory for a given project root.

    Priority:
      1. $BOBO_MEMORY_PATH_OVERRIDE
      2. <project_root>/.bobo/memory/auto/
    """
    override = os.environ.get("BOBO_MEMORY_PATH_OVERRIDE")
    if override:
        return Path(override)
    return project_root / ".bobo" / "memory" / "auto"


# --------------------------------------------------------------------------- #
# Agent Memory path                                                             #
# --------------------------------------------------------------------------- #

def agent_memory_dir(
    agent_type: str,
    scope: str,
    *,
    project_root: Path,
) -> Path:
    """Return the Agent Memory directory for a given agent type and scope.

    Args:
        agent_type:    Logical agent identifier (e.g. "researcher", "my-plugin:worker").
        scope:         One of "user", "project", "local".
        project_root:  Absolute path to the project root.

    Returns:
        Absolute Path to the agent memory directory for that scope.
    """
    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope '{scope}'. Must be one of {VALID_SCOPES}")

    safe_type = sanitize_agent_type(agent_type)

    if scope == "user":
        return memory_base() / "memory" / "agent" / safe_type / "user"

    if scope == "project":
        return project_root / ".bobo" / "memory" / "agent" / safe_type / "project"

    # scope == "local"
    remote = os.environ.get("BOBO_REMOTE_MEMORY_DIR")
    if remote:
        git_root = _git_root(str(project_root)) or str(project_root)
        safe_root = sanitize_git_root(git_root)
        return (
            Path(remote) / "projects" / safe_root / "agent-memory-local" / safe_type
        )
    return project_root / ".bobo" / "memory" / "agent" / safe_type / "local"


# --------------------------------------------------------------------------- #
# Session Memory path                                                           #
# --------------------------------------------------------------------------- #

def session_memory_dir(project_root: Path) -> Path:
    return project_root / ".bobo" / "memory" / "session"


def session_memory_path(session_id: str, project_root: Path) -> Path:
    return session_memory_dir(project_root) / f"{session_id}.md"


# --------------------------------------------------------------------------- #
# Wiki / Team / Snapshot paths                                                  #
# --------------------------------------------------------------------------- #

def wiki_dir(project_root: Path) -> Path:
    return project_root / ".bobo" / "memory" / "wiki"


def team_memory_dir(project_root: Path) -> Path:
    return project_root / ".bobo" / "memory" / "team"


def snapshot_dir(agent_type: str, project_root: Path) -> Path:
    safe_type = sanitize_agent_type(agent_type)
    return project_root / ".bobo" / "snapshots" / safe_type


def raw_dir(project_root: Path) -> Path:
    return project_root / ".bobo" / "raw"


def staging_path(project_root: Path) -> Path:
    return project_root / ".bobo" / "staging" / "pending.json"


def proposals_dir(project_root: Path) -> Path:
    return project_root / ".bobo" / "proposals"


def audit_dir(project_root: Path) -> Path:
    return project_root / ".bobo" / "audit"


def cache_dir(project_root: Path) -> Path:
    return project_root / ".bobo" / "cache"


# --------------------------------------------------------------------------- #
# Entrypoint helpers                                                           #
# --------------------------------------------------------------------------- #

def memory_entrypoint(memory_dir: Path) -> Path:
    """Return the MEMORY.md index path for a given memory directory."""
    return memory_dir / ENTRYPOINT_NAME


def is_memory_path(
    candidate: Path | str,
    *,
    project_root: Path,
    agent_type: str = "",
    scope: str = "",
) -> bool:
    """Return True if *candidate* is within any known memory directory.

    Normalises both paths to prevent '../' traversal attacks.
    """
    try:
        resolved = Path(candidate).resolve()
    except (ValueError, OSError):
        return False

    check_dirs: list[Path] = [
        auto_memory_dir(project_root),
        wiki_dir(project_root),
        team_memory_dir(project_root),
        session_memory_dir(project_root),
        raw_dir(project_root),
        proposals_dir(project_root),
    ]
    if agent_type:
        for s in VALID_SCOPES if not scope else [scope]:
            try:
                check_dirs.append(agent_memory_dir(agent_type, s, project_root=project_root))
            except ValueError:
                pass

    for d in check_dirs:
        try:
            resolved.relative_to(d.resolve())
            return True
        except ValueError:
            pass
    return False
