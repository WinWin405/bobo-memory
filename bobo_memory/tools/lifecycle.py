"""
Memory lifecycle — soft delete (forget), restore, and permanent purge.

.trash/ layout:
  <memory_dir>/.trash/
    INDEX.md              — index of trashed files (for restore)
    <topic>.<timestamp>.md — trashed content
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bobo_memory.client import MemoryClient

from bobo_memory.tools.handlers import _err, _infer_layer, _layer_dir, _ok


def _trash_dir(mem_dir: Path) -> Path:
    return mem_dir / ".trash"


def _trash_index(mem_dir: Path) -> Path:
    return _trash_dir(mem_dir) / "INDEX.md"


def _ts() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")


# ------------------------------------------------------------------ #
# memory_forget                                                        #
# ------------------------------------------------------------------ #

def handle_memory_forget(args: dict[str, Any], *, client: "MemoryClient") -> dict:
    file_rel = str(args.get("file", ""))
    reason = str(args.get("reason") or "no reason given")

    if not file_rel:
        return _err("'file' is required")

    file_path = (client.project_root / file_rel).resolve()

    try:
        client.guard.assert_within_memory(file_path)

        if not file_path.exists():
            return _err(f"File not found: {file_rel}")

        layer = _infer_layer(file_path, client)
        mem_dir = file_path.parent

        # Build trash destination
        trash = _trash_dir(mem_dir)
        trash.mkdir(parents=True, exist_ok=True)

        ts = _ts()
        stem = file_path.stem
        trash_name = f"{stem}.{ts}.md"
        trash_path = trash / trash_name

        # Move to trash
        shutil.move(str(file_path), str(trash_path))

        # Update MEMORY.md index — remove line
        from bobo_memory.core.memdir import remove_from_entrypoint_index
        remove_from_entrypoint_index(mem_dir, filename=file_path.name)

        # Update trash INDEX.md
        from bobo_memory.core.atomic import atomic_write, file_lock
        idx = _trash_index(mem_dir)
        with file_lock(idx):
            existing = idx.read_text(encoding="utf-8") if idx.exists() else "# Trash Index\n\n"
            line = f"- [{trash_name}](.trash/{trash_name}) — deleted {ts} | reason: {reason}\n"
            atomic_write(idx, existing + line)

        client._log("memory_forget", layer, file_rel, tool="memory_forget")
        return _ok({
            "file": file_rel,
            "trash_file": str(trash_path.relative_to(client.project_root)),
            "reason": reason,
        })

    except Exception as exc:
        client._log("memory_forget", "", file_rel, tool="memory_forget", ok=False, error=str(exc))
        return _err(str(exc))


# ------------------------------------------------------------------ #
# memory_restore                                                       #
# ------------------------------------------------------------------ #

def handle_memory_restore(args: dict[str, Any], *, client: "MemoryClient") -> dict:
    trash_rel = str(args.get("trash_file", ""))
    if not trash_rel:
        return _err("'trash_file' is required")

    trash_path = (client.project_root / trash_rel).resolve()

    try:
        client.guard.assert_within_memory(trash_path)

        if not trash_path.exists():
            return _err(f"Trash file not found: {trash_rel}")

        # Destination: parent of .trash/
        # e.g. .bobo/memory/auto/.trash/topic.20260101.md → .bobo/memory/auto/topic.md
        trash_dir = trash_path.parent
        mem_dir = trash_dir.parent  # the layer memory dir

        # Recover original name: strip the timestamp suffix
        # Format: <stem>.<timestamp>.md → <stem>.md
        name_parts = trash_path.stem.rsplit(".", 1)
        original_name = (name_parts[0] if len(name_parts) == 2 else trash_path.stem) + ".md"
        restore_path = mem_dir / original_name

        shutil.move(str(trash_path), str(restore_path))

        # Re-add to MEMORY.md index
        from bobo_memory.core.memdir import update_entrypoint_index
        update_entrypoint_index(mem_dir, filename=original_name, summary=f"Restored from trash")

        # Remove from trash INDEX
        idx = _trash_index(mem_dir)
        if idx.exists():
            from bobo_memory.core.atomic import atomic_write, file_lock
            with file_lock(idx):
                lines = idx.read_text(encoding="utf-8").splitlines(keepends=True)
                new_lines = [l for l in lines if trash_path.name not in l]
                atomic_write(idx, "".join(new_lines))

        layer = _infer_layer(restore_path, client)
        client._log("memory_restore", layer, str(restore_path.relative_to(client.project_root)), tool="memory_restore")
        return _ok({"restored_to": str(restore_path.relative_to(client.project_root))})

    except Exception as exc:
        client._log("memory_restore", "", trash_rel, tool="memory_restore", ok=False, error=str(exc))
        return _err(str(exc))


# ------------------------------------------------------------------ #
# memory_purge                                                         #
# ------------------------------------------------------------------ #

def handle_memory_purge(args: dict[str, Any], *, client: "MemoryClient") -> dict:
    file_rel = str(args.get("file", ""))
    confirm = bool(args.get("confirm", False))

    if not file_rel:
        return _err("'file' is required")
    if not confirm:
        return _err("'confirm' must be true to permanently delete a memory file")

    file_path = (client.project_root / file_rel).resolve()
    layer = _infer_layer(file_path, client)

    try:
        client.policy.check_purge_allowed(layer)
        client.guard.assert_within_memory(file_path)

        if not file_path.exists():
            return _err(f"File not found: {file_rel}")

        file_path.unlink()

        from bobo_memory.core.memdir import remove_from_entrypoint_index
        remove_from_entrypoint_index(file_path.parent, filename=file_path.name)

        # Audit record is permanent and cannot be deleted
        client._log("memory_purge", layer, file_rel, tool="memory_purge")
        return _ok({"purged": file_rel})

    except Exception as exc:
        client._log("memory_purge", layer, file_rel, tool="memory_purge", ok=False, error=str(exc))
        return _err(str(exc))
