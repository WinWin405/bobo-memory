"""
Structured audit log — append-only, per-day JSONL files.

Each line is a JSON object:
  {
    "ts":    "2026-05-28T00:01:02.345678",
    "op":    "memory_save",
    "layer": "agent",
    "path":  ".bobo/memory/agent/researcher/project/budget.md",
    "actor": "agent",
    "tool":  "memory_save",
    "ok":    true,
    "error": null,
    "bytes": 512
  }

Design principles (from the plan's 决策7):
- Metadata only — no memory content is logged (PII defence).
- Append-only per-day files: audit-YYYY-MM-DD.jsonl.
- Deleting audit files does NOT affect memory truth.
- Thread/process-safe via the atomic append helper.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.Lock()


def _audit_path(audit_dir: Path) -> Path:
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    return audit_dir / f"audit-{today}.jsonl"


def log_event(
    audit_dir: Path | str,
    *,
    op: str,
    layer: str = "",
    path: str = "",
    actor: str = "agent",
    tool: str = "",
    ok: bool = True,
    error: str | None = None,
    bytes_written: int = 0,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one audit record to today's JSONL file.

    This is intentionally synchronous and lightweight — it must not block
    the hot path for longer than a filesystem append.
    """
    audit_dir = Path(audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)

    record: dict[str, Any] = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "op": op,
        "layer": layer,
        "path": path,
        "actor": actor,
        "tool": tool,
        "ok": ok,
        "error": error,
        "bytes": bytes_written,
    }
    if extra:
        record.update(extra)

    line = json.dumps(record, ensure_ascii=False) + "\n"

    audit_file = _audit_path(audit_dir)
    with _LOCK:
        with open(audit_file, "a", encoding="utf-8") as fh:
            fh.write(line)


def read_events(
    audit_dir: Path | str,
    *,
    date: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Read audit records from a specific date (YYYY-MM-DD) or today.

    Args:
        audit_dir: Base audit directory.
        date:      Date string YYYY-MM-DD; defaults to today.
        limit:     Return only the last N records.
    """
    audit_dir = Path(audit_dir)
    if date is None:
        date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    audit_file = audit_dir / f"audit-{date}.jsonl"
    if not audit_file.exists():
        return []

    records: list[dict[str, Any]] = []
    with open(audit_file, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if limit is not None:
        records = records[-limit:]
    return records


def list_audit_dates(audit_dir: Path | str) -> list[str]:
    """Return sorted list of dates that have audit files."""
    audit_dir = Path(audit_dir)
    if not audit_dir.exists():
        return []
    dates = []
    for f in audit_dir.glob("audit-*.jsonl"):
        stem = f.stem  # "audit-2026-05-28"
        if stem.startswith("audit-"):
            dates.append(stem[6:])
    return sorted(dates)
