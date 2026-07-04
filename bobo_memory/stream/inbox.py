"""
Inbox — lands RawDoc objects into raw/ and registers them in staging/pending.json.

Responsibilities (ONLY):
  1. Parse the payload via the named adapter.
  2. Assign a stable source_id (SHA-256 of content, first 12 chars).
  3. Write raw/<YYYY-MM-DD>/<source_id>.md (immutable source of truth).
  4. Append an entry to staging/pending.json.
  5. Return a summary dict.

Does NOT call any LLM or modify memory/wiki directories.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bobo_memory.core.atomic import atomic_write, file_lock
from bobo_memory.core.paths import raw_dir, staging_path
from bobo_memory.stream.pipeline import StreamPipeline


def _source_id(content: str) -> str:
    """Return a stable 12-char hex id based on content hash."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def _frontmatter(doc_title: str, source_id: str, adapter: str, tags: list[str], metadata: dict) -> str:
    today = date.today().isoformat()
    tags_str = ", ".join(tags) if tags else ""
    tags_block = f"[{tags_str}]" if tags_str else "[]"
    meta_lines = "\n".join(f"{k}: {v}" for k, v in metadata.items() if k not in ("title",))
    extra = f"\n{meta_lines}" if meta_lines else ""
    return (
        "---\n"
        f"title: \"{doc_title}\"\n"
        f"source_id: {source_id}\n"
        f"adapter: {adapter}\n"
        f"tags: {tags_block}\n"
        f"ingested: {today}\n"
        f"{extra}"
        "---\n\n"
    )


def land_raw(
    *,
    adapter_name: str,
    payload: Any = None,
    path: str | Path | None = None,
    project_root: Path,
    max_raw_bytes: int | None = None,
) -> dict[str, Any]:
    """Parse payload, write to raw/, add to staging/pending.json.

    Args:
        adapter_name:  Name of the stream adapter to use.
        payload:       Raw payload passed directly to the adapter.
        path:          Path to a file parsed by the adapter.
        project_root:  Absolute project root containing .bobo/.
        max_raw_bytes: If set, documents whose raw content (body) exceeds this
                       byte limit are skipped and recorded in ``skipped``.

    Returns a dict:
        {
          "ok": True,
          "docs":    [...],   # successfully ingested docs
          "skipped": [...],   # docs that exceeded max_raw_bytes
        }
    """
    # Parse
    if path is not None:
        from bobo_memory.stream.pipeline import StreamPipeline as SP
        adapter = SP.get_adapter(adapter_name)
        from pathlib import Path as _Path
        docs = adapter.parse_path(_Path(path))
    else:
        docs = StreamPipeline.parse(adapter_name, payload)

    results: list[dict] = []
    skipped: list[dict] = []
    today_str = date.today().isoformat()
    raw_base = raw_dir(project_root) / today_str
    raw_base.mkdir(parents=True, exist_ok=True)

    staging = staging_path(project_root)
    staging.parent.mkdir(parents=True, exist_ok=True)

    for doc in docs:
        sid = doc.source_id or _source_id(doc.body)
        doc.source_id = sid

        # Build raw file content
        front = _frontmatter(doc.title, sid, doc.adapter or adapter_name, doc.tags, doc.metadata)
        raw_content = front + doc.body

        # Size limit check
        if max_raw_bytes is not None and len(raw_content.encode("utf-8")) > max_raw_bytes:
            skipped.append({
                "source_id": sid,
                "title": doc.title,
                "reason": f"content size exceeds max_raw_bytes={max_raw_bytes}",
            })
            continue

        raw_file = raw_base / f"{sid}.md"
        if not raw_file.exists():
            atomic_write(raw_file, raw_content)

        # Append to staging
        task = {
            "source_id": sid,
            "raw_path": str(raw_file.relative_to(project_root)),
            "title": doc.title,
            "adapter": doc.adapter or adapter_name,
            "tags": doc.tags,
            "ingested": today_str,
        }
        _append_staging(staging, task)

        results.append({
            "source_id": sid,
            "raw_path": str(raw_file),
            "title": doc.title,
        })

    return {"ok": True, "docs": results, "skipped": skipped}


def _append_staging(staging_file: Path, task: dict) -> None:
    """Atomically append a task to pending.json."""
    with file_lock(staging_file):
        tasks: list[dict] = []
        if staging_file.exists():
            try:
                tasks = json.loads(staging_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                tasks = []
        # Avoid duplicate source_ids
        existing_ids = {t.get("source_id") for t in tasks}
        if task.get("source_id") not in existing_ids:
            tasks.append(task)
        atomic_write(staging_file, json.dumps(tasks, indent=2, ensure_ascii=False))


def _load_tasks(staging_file: Path) -> list[dict] | None:
    try:
        return json.loads(staging_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_tasks(staging_file: Path, tasks: list[dict]) -> None:
    atomic_write(staging_file, json.dumps(tasks, indent=2, ensure_ascii=False))


def pop_next_task(
    project_root: Path,
    *,
    lease_minutes: int = 30,
    max_attempts: int = 3,
) -> dict | None:
    """Lease and return the next available task from staging/pending.json.

    The task is NOT removed — it is marked ``in_progress`` with a lease
    timestamp so a crashed agent cannot lose it:

      - Call complete_task() (tool: ingest_done) to remove it after processing.
      - If the lease expires before confirmation, the task becomes available
        again, up to *max_attempts* leases in total.
      - After *max_attempts* expired leases the task is marked ``failed`` and
        kept in the file for human inspection.
    """
    staging = staging_path(project_root)
    if not staging.exists():
        return None

    now = datetime.now(tz=timezone.utc)
    lease = timedelta(minutes=lease_minutes)

    with file_lock(staging):
        tasks = _load_tasks(staging)
        if not tasks:
            return None

        leased: dict | None = None
        for task in tasks:
            status = task.get("status", "pending")
            if status == "pending":
                pass  # available
            elif status == "in_progress":
                try:
                    leased_at = datetime.fromisoformat(task.get("leased_at", ""))
                except ValueError:
                    leased_at = now - lease  # unreadable lease → treat as expired
                if now - leased_at < lease:
                    continue  # still being processed elsewhere
                if int(task.get("attempts", 1)) >= max_attempts:
                    task["status"] = "failed"
                    continue
            else:  # failed / unknown
                continue

            task["status"] = "in_progress"
            task["leased_at"] = now.isoformat()
            task["attempts"] = int(task.get("attempts", 0)) + 1
            leased = task
            break

        _save_tasks(staging, tasks)

    return leased


def complete_task(project_root: Path, source_id: str) -> bool:
    """Remove a finished task from staging (the ingest_done acknowledgement).

    Returns True if a task with *source_id* was found and removed.
    """
    staging = staging_path(project_root)
    if not staging.exists():
        return False
    with file_lock(staging):
        tasks = _load_tasks(staging)
        if tasks is None:
            return False
        remaining = [t for t in tasks if t.get("source_id") != source_id]
        if len(remaining) == len(tasks):
            return False
        _save_tasks(staging, remaining)
    return True


def list_tasks(project_root: Path) -> list[dict]:
    """Return all staging tasks (pending / in_progress / failed) without mutating."""
    staging = staging_path(project_root)
    if not staging.exists():
        return []
    return _load_tasks(staging) or []
