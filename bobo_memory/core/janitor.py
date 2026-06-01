"""
Janitor — idempotent storage governance primitives.

Three functions, each callable independently or via MemoryClient.run_janitor():

  purge_expired_trash(project_root, policy)
      Deletes .trash/*.md files older than policy.trash.retention_days across
      all known memory layer directories.  Updates each layer's .trash/INDEX.md.

  cleanup_sessions(project_root, policy)
      Deletes session/*.md files whose mtime is older than
      policy.session.max_age_days.

  rotate_audit(project_root, policy)
      Deletes audit-YYYY-MM-DD.jsonl files older than policy.audit.retention_days.
      Never deletes today's file regardless of the setting.

Each function returns a dict:
  {
    "deleted_files": int,
    "freed_bytes":   int,
    "errors":        list[str],   # non-fatal; path + exception message
  }
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bobo_memory.core.policy import MemoryPolicy


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _file_age_days(path: Path) -> float:
    """Return age of *path* in fractional days based on mtime (UTC)."""
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (_utc_now() - mtime).total_seconds() / 86400


def _safe_unlink(path: Path, errors: list[str]) -> int:
    """Delete *path* and return its size in bytes; append to *errors* on failure."""
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except OSError as exc:
        errors.append(f"{path}: {exc}")
        return 0


def _empty_report() -> dict:
    return {"deleted_files": 0, "freed_bytes": 0, "errors": []}


# --------------------------------------------------------------------------- #
# Trash cleanup                                                                #
# --------------------------------------------------------------------------- #

def _collect_memory_dirs(project_root: Path) -> list[Path]:
    """Return all memory-layer directories that may contain a .trash/ sub-dir."""
    bobo = project_root / ".bobo" / "memory"
    if not bobo.exists():
        return []
    dirs: list[Path] = []
    # auto, wiki, team, session
    for name in ("auto", "wiki", "team", "session"):
        d = bobo / name
        if d.is_dir():
            dirs.append(d)
    # agent/<type>/<scope>
    agent_root = bobo / "agent"
    if agent_root.is_dir():
        for type_dir in agent_root.iterdir():
            if type_dir.is_dir():
                for scope_dir in type_dir.iterdir():
                    if scope_dir.is_dir():
                        dirs.append(scope_dir)
    return dirs


def _rebuild_trash_index(trash_dir: Path) -> None:
    """Rewrite .trash/INDEX.md to reflect only the files still present."""
    index_file = trash_dir / "INDEX.md"
    remaining = sorted(
        f for f in trash_dir.iterdir()
        if f.is_file() and f.name != "INDEX.md" and f.suffix == ".md"
    )
    if not remaining:
        if index_file.exists():
            index_file.unlink(missing_ok=True)
        return
    lines = ["# Trash Index\n\n"]
    for f in remaining:
        ts_match = re.search(r"\.(\d{14})\.md$", f.name)
        ts = ts_match.group(1) if ts_match else "unknown"
        lines.append(f"- [{f.name}](.trash/{f.name}) — deleted {ts}\n")
    index_file.write_text("".join(lines), encoding="utf-8")


def purge_expired_trash(project_root: Path, policy: "MemoryPolicy") -> dict:
    """Delete .trash files older than policy.trash.retention_days.

    Idempotent — safe to call repeatedly.
    """
    report = _empty_report()
    retention = policy.trash.retention_days  # int, always set (default 30)

    for mem_dir in _collect_memory_dirs(project_root):
        trash_dir = mem_dir / ".trash"
        if not trash_dir.is_dir():
            continue
        changed = False
        for f in list(trash_dir.iterdir()):
            if f.name == "INDEX.md" or not f.is_file() or f.suffix != ".md":
                continue
            try:
                if _file_age_days(f) >= retention:
                    freed = _safe_unlink(f, report["errors"])
                    report["deleted_files"] += 1
                    report["freed_bytes"] += freed
                    changed = True
            except OSError as exc:
                report["errors"].append(f"{f}: {exc}")
        if changed:
            try:
                _rebuild_trash_index(trash_dir)
            except OSError as exc:
                report["errors"].append(f"INDEX rebuild {trash_dir}: {exc}")

    return report


# --------------------------------------------------------------------------- #
# Session cleanup                                                              #
# --------------------------------------------------------------------------- #

def cleanup_sessions(project_root: Path, policy: "MemoryPolicy") -> dict:
    """Delete session/*.md files older than policy.session.max_age_days.

    If max_age_days is None, does nothing.
    """
    report = _empty_report()
    max_age = policy.session.max_age_days
    if max_age is None:
        return report

    session_dir = project_root / ".bobo" / "memory" / "session"
    if not session_dir.is_dir():
        return report

    for f in list(session_dir.iterdir()):
        if not f.is_file() or f.suffix != ".md":
            continue
        try:
            if _file_age_days(f) >= max_age:
                freed = _safe_unlink(f, report["errors"])
                report["deleted_files"] += 1
                report["freed_bytes"] += freed
        except OSError as exc:
            report["errors"].append(f"{f}: {exc}")

    return report


# --------------------------------------------------------------------------- #
# Audit rotation                                                               #
# --------------------------------------------------------------------------- #

def rotate_audit(project_root: Path, policy: "MemoryPolicy") -> dict:
    """Delete audit-YYYY-MM-DD.jsonl files older than policy.audit.retention_days.

    Never deletes today's audit file regardless of the setting.
    If retention_days is None, does nothing.
    """
    report = _empty_report()
    retention = policy.audit.retention_days
    if retention is None:
        return report

    audit_dir = project_root / ".bobo" / "audit"
    if not audit_dir.is_dir():
        return report

    today_str = _utc_now().strftime("%Y-%m-%d")
    cutoff = _utc_now() - timedelta(days=retention)

    for f in list(audit_dir.iterdir()):
        if not f.is_file() or not f.name.startswith("audit-") or not f.name.endswith(".jsonl"):
            continue
        # extract date from filename: audit-YYYY-MM-DD.jsonl
        date_str = f.stem[len("audit-"):]
        if date_str == today_str:
            continue  # always keep today
        try:
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue  # unknown format, skip
        if file_date < cutoff:
            freed = _safe_unlink(f, report["errors"])
            report["deleted_files"] += 1
            report["freed_bytes"] += freed

    return report
