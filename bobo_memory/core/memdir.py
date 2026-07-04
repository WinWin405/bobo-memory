"""
MEMORY.md file protocol — the core contract of the bobo-memory system.

Mirrors the design of src/memdir/memdir.ts from the reference implementation.

Key constants:
  ENTRYPOINT_NAME     = "MEMORY.md"
  MAX_ENTRYPOINT_LINES = 200
  MAX_ENTRYPOINT_BYTES = 25_000

Key functions:
  truncate_entrypoint_content()  — hard cap on MEMORY.md before prompt injection
  build_memory_lines()           — returns the instruction block lines for a memory dir
  build_memory_prompt()          — full system-prompt fragment including current index
  read_entrypoint()              — synchronous read of MEMORY.md (safe, never raises)
  update_entrypoint_index()      — atomic append/update of a single index line
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from bobo_memory.core.atomic import atomic_write, file_lock
from bobo_memory.core.paths import (
    ENTRYPOINT_NAME,
    MAX_ENTRYPOINT_BYTES,
    MAX_ENTRYPOINT_LINES,
)


# --------------------------------------------------------------------------- #
# Truncation                                                                   #
# --------------------------------------------------------------------------- #

class TruncationResult(NamedTuple):
    content: str
    line_count: int
    byte_count: int
    was_line_truncated: bool
    was_byte_truncated: bool


def truncate_entrypoint_content(raw: str) -> TruncationResult:
    """Apply hard line + byte caps to MEMORY.md content before prompt injection.

    Returns a TruncationResult with the (possibly truncated) content and metadata.
    """
    lines = raw.splitlines(keepends=True)

    was_line_truncated = False
    if len(lines) > MAX_ENTRYPOINT_LINES:
        lines = lines[:MAX_ENTRYPOINT_LINES]
        was_line_truncated = True

    content = "".join(lines)

    was_byte_truncated = False
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_ENTRYPOINT_BYTES:
        # Truncate to byte limit, preserving whole characters
        encoded = encoded[:MAX_ENTRYPOINT_BYTES]
        content = encoded.decode("utf-8", errors="ignore")
        was_byte_truncated = True

    return TruncationResult(
        content=content,
        line_count=len(content.splitlines()),
        byte_count=len(content.encode("utf-8")),
        was_line_truncated=was_line_truncated,
        was_byte_truncated=was_byte_truncated,
    )


# --------------------------------------------------------------------------- #
# Prompt construction                                                           #
# --------------------------------------------------------------------------- #

_TYPES_SECTION = """
## Memory types

- **Preference** — long-term user or project preferences that should persist across sessions.
- **Fact** — objective facts about the project, domain, or world relevant to this agent.
- **Procedure** — repeatable steps or workflows the agent should follow.
- **Relationship** — connections between entities, concepts, or people.
- **Event** — time-stamped events, decisions, or milestones.
- **Reference** — pointers to raw sources, documents, or external resources.
""".strip()

_WHAT_NOT_TO_SAVE = """
## What NOT to save

- Content that can be derived from the codebase or documents without loss.
- Duplicate information already captured in another memory file.
- Temporary state that won't matter next session.
- Raw conversation text — extract only the durable insight.
- Secrets, credentials, or personal data (enforced by policy).
""".strip()

_WHEN_TO_SAVE = """
## When to save

Save a memory when:
- The user corrects you, or states a preference that should outlast this session.
- A decision is made that future sessions must respect.
- You discover a non-obvious fact or constraint that is not written down anywhere else.
- The user explicitly asks you to remember something.

Before saving, check the index (or use `memory_recall`) for an existing memory on
the same topic — prefer `memory_update` over creating a near-duplicate file.
""".strip()

_HOW_TO_SAVE = """
## How to save memories

Call the `memory_save` tool:
- `layer`:   which memory layer to write to
- `topic`:   short descriptive slug (e.g. `budget-constraints`)
- `content`: self-contained markdown — one topic per memory, small files over large ones
- `summary`: one line shown in the MEMORY.md index
- `tags` / `sources`: optional metadata

The memory file and its MEMORY.md index line are both written automatically —
never edit MEMORY.md yourself. Use `memory_update` to revise an existing memory
and `memory_forget` to retire one.
""".strip()

_SEARCHING_PAST = """
## Recalling past memories

- The MEMORY.md index below shows all saved memories at a glance.
- Use `memory_recall(query=..., k=5)` to find the most relevant files for your current task.
- Use `memory_read(file=...)` when you need the full content of one memory.
- Avoid re-reading files you have already seen in this session.
""".strip()

_DIR_EXISTS_GUIDANCE = (
    "This directory already exists — write to it directly without checking "
    "whether it needs to be created first."
)


def build_shared_instructions() -> str:
    """Return the layer-independent memory instructions as one block.

    Inject this ONCE per system prompt, then render each layer with
    ``include_instructions=False`` so the how-to text is not duplicated.
    """
    return "\n\n".join([
        "# Memory system",
        "You have a persistent, file-based memory system.",
        _TYPES_SECTION,
        _WHEN_TO_SAVE,
        _WHAT_NOT_TO_SAVE,
        _HOW_TO_SAVE,
        _SEARCHING_PAST,
    ])


def build_memory_lines(
    display_name: str,
    memory_dir: Path | str,
    *,
    extra_guidelines: list[str] | None = None,
    skip_index: bool = False,
    include_instructions: bool = True,
) -> list[str]:
    """Return the instruction lines that teach an LLM how to use a memory directory.

    This is the Python equivalent of buildMemoryLines() in memdir.ts.

    Args:
        include_instructions: When False, omit the shared how-to sections
                              (types / when / how / recall) — use together with
                              build_shared_instructions() to avoid injecting
                              the same text once per layer.
    """
    memory_dir = Path(memory_dir)
    lines: list[str] = [
        f"# {display_name}",
        "",
        f"You have a persistent, file-based memory system at `{memory_dir}`. "
        f"{_DIR_EXISTS_GUIDANCE}",
        "",
    ]

    if include_instructions:
        lines.extend([
            _TYPES_SECTION,
            "",
            _WHEN_TO_SAVE,
            "",
            _WHAT_NOT_TO_SAVE,
            "",
            _HOW_TO_SAVE,
            "",
        ])

    if extra_guidelines:
        for guideline in extra_guidelines:
            lines.append(guideline)
        lines.append("")

    if include_instructions and not skip_index:
        lines.append(_SEARCHING_PAST)
        lines.append("")

    return lines


def read_entrypoint(memory_dir: Path | str) -> str:
    """Synchronously read MEMORY.md; returns empty string if it does not exist."""
    p = Path(memory_dir) / ENTRYPOINT_NAME
    try:
        return p.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""


def build_memory_prompt(
    display_name: str,
    memory_dir: Path | str,
    *,
    extra_guidelines: list[str] | None = None,
    include_instructions: bool = True,
) -> str:
    """Return the full system-prompt fragment for a memory directory.

    Includes instructions + current MEMORY.md content (with hard truncation).
    This is the Python equivalent of buildMemoryPrompt() in memdir.ts.

    IMPORTANT: This function is intentionally synchronous — it may be called
    from a synchronous prompt-construction path.
    """
    memory_dir = Path(memory_dir)
    entrypoint_raw = read_entrypoint(memory_dir)
    lines = build_memory_lines(
        display_name, memory_dir,
        extra_guidelines=extra_guidelines,
        include_instructions=include_instructions,
    )

    lines.append(f"## {ENTRYPOINT_NAME}")
    lines.append("")

    if entrypoint_raw.strip():
        truncated = truncate_entrypoint_content(entrypoint_raw)
        lines.append(truncated.content)
        if truncated.was_line_truncated or truncated.was_byte_truncated:
            lines.append(
                f"\n*[MEMORY.md truncated: {truncated.line_count} lines / "
                f"{truncated.byte_count} bytes shown]*"
            )
    else:
        lines.append(
            f"Your {ENTRYPOINT_NAME} is currently empty. "
            "When you save new memories, they will appear here."
        )

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Index maintenance                                                             #
# --------------------------------------------------------------------------- #

def update_entrypoint_index(
    memory_dir: Path | str,
    *,
    filename: str,
    summary: str,
) -> None:
    """Atomically add or update one index line in MEMORY.md.

    Line format:  - [filename](filename) — summary

    If a line for *filename* already exists it is replaced; otherwise appended.
    Uses file_lock + atomic_write to prevent concurrent corruption.
    """
    memory_dir = Path(memory_dir)
    entry_path = memory_dir / ENTRYPOINT_NAME
    new_line = f"- [{filename}]({filename}) — {summary}"

    with file_lock(entry_path):
        existing = read_entrypoint(memory_dir)
        lines = existing.splitlines(keepends=True) if existing else []

        updated = False
        new_lines: list[str] = []
        for line in lines:
            if f"[{filename}]" in line:
                new_lines.append(new_line + "\n")
                updated = True
            else:
                new_lines.append(line)

        if not updated:
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines.append("\n")
            new_lines.append(new_line + "\n")

        atomic_write(entry_path, "".join(new_lines))


def remove_from_entrypoint_index(
    memory_dir: Path | str,
    *,
    filename: str,
) -> None:
    """Atomically remove the index line for *filename* from MEMORY.md."""
    memory_dir = Path(memory_dir)
    entry_path = memory_dir / ENTRYPOINT_NAME

    with file_lock(entry_path):
        existing = read_entrypoint(memory_dir)
        if not existing:
            return
        lines = existing.splitlines(keepends=True)
        new_lines = [l for l in lines if f"[{filename}]" not in l]
        atomic_write(entry_path, "".join(new_lines))
