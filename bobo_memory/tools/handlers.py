"""
Tool handlers — the actual Python implementations behind each ToolSpec.

Every write handler follows the 4-rail pipeline:
  1. policy.check_action()    — citations, secret scan, write_mode, size
  2. guard.assert_within_memory() — path boundary
  3. file_lock + atomic_write — concurrent safety
  4. audit.log_event()        — structured audit trail

Proposal redirect:
  If guard.should_use_proposal(layer) → True, the write is redirected to
  .bobo/proposals/<layer>/<topic>.<uuid>.md instead.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bobo_memory.client import MemoryClient


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _layer_dir(client: "MemoryClient", layer: str) -> Path:
    """Return the memory directory for a given layer."""
    if layer == "agent":
        return client._agent_mem.memory_dir
    if layer == "auto":
        return client._auto_mem.memory_dir
    if layer == "wiki":
        from bobo_memory.core.paths import wiki_dir
        return wiki_dir(client.project_root)
    if layer == "session":
        return client._session_mem.memory_dir
    if layer == "team":
        from bobo_memory.core.paths import team_memory_dir
        return team_memory_dir(client.project_root)
    raise ValueError(f"Unknown layer: {layer!r}")


def _build_frontmatter(
    sources: list[str],
    tags: list[str],
) -> str:
    today = date.today().isoformat()
    src_block = "sources:\n" + "".join(f"  - {s}\n" for s in sources) if sources else "sources: []"
    tag_block = f"tags: [{', '.join(tags)}]" if tags else "tags: []"
    return f"---\n{src_block}\n{tag_block}\ncreated: {today}\nupdated: {today}\n---\n\n"


def _ok(data: dict) -> dict:
    return {"ok": True, **data}


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


# ------------------------------------------------------------------ #
# Dispatch                                                             #
# ------------------------------------------------------------------ #

def dispatch(name: str, arguments: dict[str, Any], *, client: "MemoryClient", actor: str = "agent") -> dict[str, Any]:
    """Dispatch a named tool call to its handler."""
    _HANDLER_MAP = {
        "memory_save": handle_memory_save,
        "memory_update": handle_memory_update,
        "memory_list": handle_memory_list,
        "memory_read": handle_memory_read,
        "memory_recall": handle_memory_recall,
        "wiki_link": handle_wiki_link,
        "wiki_log": handle_wiki_log,
        "ingest_next": handle_ingest_next,
    }
    # lifecycle tools imported lazily to avoid circular
    _LIFECYCLE_MAP = {
        "memory_forget": "handle_memory_forget",
        "memory_restore": "handle_memory_restore",
        "memory_purge": "handle_memory_purge",
    }

    if name in _HANDLER_MAP:
        return _HANDLER_MAP[name](arguments, client=client)
    if name in _LIFECYCLE_MAP:
        from bobo_memory.tools.lifecycle import handle_memory_forget, handle_memory_restore, handle_memory_purge
        lc_map = {
            "memory_forget": handle_memory_forget,
            "memory_restore": handle_memory_restore,
            "memory_purge": handle_memory_purge,
        }
        return lc_map[name](arguments, client=client)

    return _err(f"Unknown tool: '{name}'")


# ------------------------------------------------------------------ #
# memory_save                                                          #
# ------------------------------------------------------------------ #

def handle_memory_save(args: dict[str, Any], *, client: "MemoryClient") -> dict:
    layer = args.get("layer", "auto")
    topic = str(args.get("topic", "untitled")).strip()
    content = str(args.get("content", ""))
    summary = str(args.get("summary") or topic.replace("-", " ").capitalize())
    tags: list[str] = args.get("tags") or []
    sources: list[str] = args.get("sources") or []

    # Sanitise topic → filename
    filename = topic.replace(":", "-").replace("/", "-").replace("\\", "-")
    if not filename.endswith(".md"):
        filename += ".md"

    try:
        # 1. Policy check
        client.policy.check_action(
            "memory_save", layer,
            content=content,
            sources=sources,
        )

        mem_dir = _layer_dir(client, layer)
        mem_dir.mkdir(parents=True, exist_ok=True)
        file_path = mem_dir / filename

        # 1b. Per-layer file count limit (only for new files, not overwrites)
        if not file_path.exists():
            max_files = client.policy.effective_max_files(layer)
            if max_files is not None:
                current = sum(
                    1 for f in mem_dir.glob("*.md")
                    if f.name != "MEMORY.md" and not f.name.startswith(".")
                )
                if current >= max_files:
                    return _err(
                        f"layer '{layer}' has reached the maximum of {max_files} memory files. "
                        "Use memory_update to modify an existing memory, or memory_forget to remove one first."
                    )

        # Proposal redirect
        if client.guard.should_use_proposal(layer):
            return _redirect_to_proposal(
                client, layer, topic, filename, content, summary, tags, sources
            )

        # 2. Guard (path boundary)
        client.guard.assert_within_memory(file_path)

        # 3. Build final content with frontmatter
        front = _build_frontmatter(sources, tags)
        full_content = front + content

        # 4. Atomic write
        from bobo_memory.core.atomic import atomic_write, file_lock
        from bobo_memory.core.memdir import update_entrypoint_index

        with file_lock(file_path):
            atomic_write(file_path, full_content)

        # Update MEMORY.md index
        update_entrypoint_index(mem_dir, filename=filename, summary=summary)

        # 5. Audit
        client._log(
            "memory_save", layer, str(file_path.relative_to(client.project_root)),
            tool="memory_save", bytes_written=len(full_content.encode()),
        )

        return _ok({"file": str(file_path.relative_to(client.project_root)), "layer": layer})

    except Exception as exc:
        client._log("memory_save", layer, tool="memory_save", ok=False, error=str(exc))
        return _err(str(exc))


def _redirect_to_proposal(client, layer, topic, filename, content, summary, tags, sources) -> dict:
    from bobo_memory.tools.proposal import write_proposal
    return write_proposal(
        client=client,
        layer=layer,
        topic=topic,
        filename=filename,
        content=content,
        summary=summary,
        tags=tags,
        sources=sources,
    )


# ------------------------------------------------------------------ #
# memory_update                                                        #
# ------------------------------------------------------------------ #

def handle_memory_update(args: dict[str, Any], *, client: "MemoryClient") -> dict:
    file_rel = str(args.get("file", ""))
    content = str(args.get("content", ""))
    summary: str | None = args.get("summary")

    if not file_rel:
        return _err("'file' is required")

    file_path = (client.project_root / file_rel).resolve()

    try:
        layer = _infer_layer(file_path, client)

        client.policy.check_action("memory_update", layer, content=content)
        client.guard.assert_within_memory(file_path)

        from bobo_memory.core.atomic import atomic_write, file_lock
        with file_lock(file_path):
            atomic_write(file_path, content)

        if summary:
            from bobo_memory.core.memdir import update_entrypoint_index
            update_entrypoint_index(
                file_path.parent,
                filename=file_path.name,
                summary=summary,
            )

        client._log(
            "memory_update", layer, file_rel,
            tool="memory_update", bytes_written=len(content.encode()),
        )
        return _ok({"file": file_rel})

    except Exception as exc:
        client._log("memory_update", "", file_rel, tool="memory_update", ok=False, error=str(exc))
        return _err(str(exc))


# ------------------------------------------------------------------ #
# memory_list                                                          #
# ------------------------------------------------------------------ #

def handle_memory_list(args: dict[str, Any], *, client: "MemoryClient") -> dict:
    layer = args.get("layer", "agent")
    try:
        mem_dir = _layer_dir(client, layer)
        from bobo_memory.core.memdir import read_entrypoint, truncate_entrypoint_content
        raw = read_entrypoint(mem_dir)
        if raw:
            truncated = truncate_entrypoint_content(raw)
            content = truncated.content
        else:
            content = "(empty — no memories saved yet)"
        client._log("memory_list", layer, tool="memory_list")
        return _ok({"layer": layer, "index": content})
    except Exception as exc:
        return _err(str(exc))


# ------------------------------------------------------------------ #
# memory_read                                                          #
# ------------------------------------------------------------------ #

def handle_memory_read(args: dict[str, Any], *, client: "MemoryClient") -> dict:
    file_rel = str(args.get("file", ""))
    if not file_rel:
        return _err("'file' is required")

    file_path = (client.project_root / file_rel).resolve()

    try:
        client.guard.assert_within_memory(file_path)
        content = file_path.read_text(encoding="utf-8")
        client._log("memory_read", _infer_layer(file_path, client), file_rel, tool="memory_read")
        return _ok({"file": file_rel, "content": content})
    except FileNotFoundError:
        return _err(f"File not found: {file_rel}")
    except Exception as exc:
        return _err(str(exc))


# ------------------------------------------------------------------ #
# memory_recall                                                        #
# ------------------------------------------------------------------ #

def handle_memory_recall(args: dict[str, Any], *, client: "MemoryClient") -> dict:
    query = str(args.get("query", ""))
    k = int(args.get("k", 5))
    layers = args.get("layers") or client.config.enabled_layers
    token_budget = int(args.get("token_budget", 8000))

    try:
        pack = client.recall(query=query, k=k, layers=layers, token_budget=token_budget)
        client._log("memory_recall", tool="memory_recall")
        if hasattr(pack, "model_dump"):
            return _ok({"pack": pack.model_dump()})
        return _ok({"pack": pack})
    except Exception as exc:
        return _err(str(exc))


# ------------------------------------------------------------------ #
# wiki_link                                                            #
# ------------------------------------------------------------------ #

def handle_wiki_link(args: dict[str, Any], *, client: "MemoryClient") -> dict:
    from_topic = str(args.get("from_topic", ""))
    to_topic = str(args.get("to_topic", ""))
    kind = str(args.get("kind", "related"))

    if not from_topic or not to_topic:
        return _err("'from_topic' and 'to_topic' are required")

    try:
        from bobo_memory.core.paths import wiki_dir
        from bobo_memory.core.atomic import atomic_write, file_lock
        wiki = wiki_dir(client.project_root)

        def _add_xref(source_file: Path, target_file: Path, rel_kind: str) -> None:
            if not source_file.exists():
                return
            content = source_file.read_text(encoding="utf-8")
            xref_line = f"- [{target_file.stem}]({target_file.name}) ({rel_kind})"
            if str(target_file.stem) in content:
                return
            if "## See also" in content:
                content = content + f"\n{xref_line}\n"
            else:
                content = content + f"\n\n## See also\n\n{xref_line}\n"
            with file_lock(source_file):
                atomic_write(source_file, content)

        from_file = wiki / f"{from_topic}.md"
        to_file = wiki / f"{to_topic}.md"
        _add_xref(from_file, to_file, kind)
        _add_xref(to_file, from_file, "referenced-by")

        client._log("wiki_link", "wiki", tool="wiki_link")
        return _ok({"from": from_topic, "to": to_topic, "kind": kind})
    except Exception as exc:
        return _err(str(exc))


# ------------------------------------------------------------------ #
# wiki_log                                                             #
# ------------------------------------------------------------------ #

def handle_wiki_log(args: dict[str, Any], *, client: "MemoryClient") -> dict:
    kind = str(args.get("kind", "event"))
    title = str(args.get("title", ""))
    summary = str(args.get("summary", ""))
    today = date.today().isoformat()

    try:
        from bobo_memory.core.paths import wiki_dir
        from bobo_memory.core.atomic import atomic_write, file_lock
        wiki = wiki_dir(client.project_root)
        wiki.mkdir(parents=True, exist_ok=True)
        log_file = wiki / "log.md"

        entry = f"\n## [{today}] {kind} | {title}\n\n{summary}\n"

        with file_lock(log_file):
            existing = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
            atomic_write(log_file, existing + entry)

        client._log("wiki_log", "wiki", tool="wiki_log")
        return _ok({"appended": entry.strip()})
    except Exception as exc:
        return _err(str(exc))


# ------------------------------------------------------------------ #
# ingest_next                                                          #
# ------------------------------------------------------------------ #

def handle_ingest_next(args: dict[str, Any], *, client: "MemoryClient") -> dict:
    from bobo_memory.stream.inbox import pop_next_task

    task = pop_next_task(client.project_root)
    if task is None:
        return _ok({"task": None, "message": "No pending ingest tasks."})

    raw_path = client.project_root / task.get("raw_path", "")
    try:
        raw_content = raw_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        raw_content = "(file not found)"

    client._log("ingest_next", tool="ingest_next")
    return _ok({
        "task": task,
        "raw_content": raw_content,
        "instruction": (
            f"Please integrate this source into memory/wiki. "
            f"Source: '{task.get('title')}' (id={task.get('source_id')}). "
            "Write memory files using memory_save, update wiki with wiki_link, "
            "and log the event with wiki_log."
        ),
    })


# ------------------------------------------------------------------ #
# Helper: infer layer from path                                        #
# ------------------------------------------------------------------ #

def _infer_layer(file_path: Path, client: "MemoryClient") -> str:
    p = str(file_path)
    if "memory/agent" in p or "memory\\agent" in p:
        return "agent"
    if "memory/auto" in p or "memory\\auto" in p:
        return "auto"
    if "memory/wiki" in p or "memory\\wiki" in p:
        return "wiki"
    if "memory/session" in p or "memory\\session" in p:
        return "session"
    if "memory/team" in p or "memory\\team" in p:
        return "team"
    return "unknown"
