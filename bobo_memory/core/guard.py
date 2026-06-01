"""
MemoryGuard — path boundary validation + policy lookup.

Mirrors the purpose of isAgentMemoryPath() and createMemoryFileCanUseTool()
from the reference TypeScript implementation.

All tool handlers call guard.assert_within_memory(path) before any IO.
Session memory subagents use guard.canonical_can_use_tool(allowed_path)
to get a whitelist predicate that only permits writes to one exact path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from bobo_memory.core.paths import is_memory_path
from bobo_memory.core.policy import MemoryPolicy, PolicyViolation


class MemoryGuard:
    """Centralised path-boundary checker and policy gateway."""

    def __init__(
        self,
        project_root: Path,
        agent_type: str = "",
        scope: str = "",
        policy: MemoryPolicy | None = None,
    ) -> None:
        self.project_root = project_root
        self.agent_type = agent_type
        self.scope = scope
        self.policy: MemoryPolicy = policy or MemoryPolicy.default()

    # ------------------------------------------------------------------ #
    # Path boundary                                                        #
    # ------------------------------------------------------------------ #

    def is_within_memory(self, path: Path | str) -> bool:
        """Return True if *path* resolves into any known memory directory."""
        return is_memory_path(
            path,
            project_root=self.project_root,
            agent_type=self.agent_type,
            scope=self.scope,
        )

    def assert_within_memory(self, path: Path | str) -> None:
        """Raise PolicyViolation if *path* is outside all memory directories.

        This is the primary defence against path-traversal attacks in tool
        handlers (analogous to the TypeScript isAgentMemoryPath check).
        """
        if not self.is_within_memory(path):
            raise PolicyViolation(
                f"path '{path}' is outside all memory directories — "
                "possible path-traversal attempt",
                tool="__guard__",
                layer="__guard__",
            )

    # ------------------------------------------------------------------ #
    # Policy gateway                                                       #
    # ------------------------------------------------------------------ #

    def check_action(
        self,
        tool: str,
        layer: str,
        *,
        path: Path | str | None = None,
        actor: str = "agent",
        content: str = "",
        sources: list[str] | None = None,
    ) -> None:
        """Combined path-boundary + policy check.

        Raises PolicyViolation on any violation.
        """
        if path is not None:
            self.assert_within_memory(path)
        self.policy.check_action(
            tool,
            layer,
            actor=actor,
            content=content,
            sources=sources,
        )

    # ------------------------------------------------------------------ #
    # Session memory sandbox (mirrors createMemoryFileCanUseTool)          #
    # ------------------------------------------------------------------ #

    def canonical_can_use_tool(
        self,
        allowed_path: Path | str,
    ) -> Callable[[str, dict], bool]:
        """Return a predicate that only allows writes to *exactly* one path.

        Intended for the session-memory fork subagent: the agent receives this
        predicate and may only call the file-edit tool on the given session file.

        Usage::

            can_use = guard.canonical_can_use_tool(session_path)
            if can_use("memory_update", {"file": some_path}):
                ...   # allowed
        """
        resolved = Path(allowed_path).resolve()

        def _can_use(tool_name: str, arguments: dict) -> bool:
            if tool_name != "memory_update":
                return False
            candidate = arguments.get("file") or arguments.get("path") or ""
            try:
                return Path(candidate).resolve() == resolved
            except (ValueError, OSError):
                return False

        return _can_use

    # ------------------------------------------------------------------ #
    # Proposal redirect check                                              #
    # ------------------------------------------------------------------ #

    def should_use_proposal(self, layer: str) -> bool:
        """Return True if writes to *layer* should go to the proposals queue."""
        return self.policy.effective_write_mode(layer) == "proposal"
