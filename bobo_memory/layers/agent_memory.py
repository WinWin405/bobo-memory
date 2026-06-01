"""
AgentMemory layer — per-agent-type persistent memory with three scopes.

Mirrors src/tools/AgentTool/agentMemory.ts from the reference implementation.

Scope semantics:
  user    — cross-project, lives in ~/.bobo/memory/agent/<type>/user/
  project — project-local shared, lives in .bobo/memory/agent/<type>/project/
  local   — machine-local, lives in .bobo/memory/agent/<type>/local/

At build_prompt() time the layer:
  1. Ensures the memory directory exists (fire-and-forget, non-blocking).
  2. Reads MEMORY.md synchronously.
  3. Applies hard truncation (200 lines / 25 KB).
  4. Returns the full prompt fragment including scope note + index content.
"""

from __future__ import annotations

import os
from pathlib import Path

from bobo_memory.core.memdir import build_memory_prompt
from bobo_memory.core.paths import agent_memory_dir
from bobo_memory.core.prompts import citation_note, scope_note
from bobo_memory.layers.base import MemoryLayer


class AgentMemory(MemoryLayer):
    """Persistent memory tied to a specific agent type and scope."""

    name = "agent"

    def __init__(
        self,
        agent_type: str,
        scope: str,
        project_root: Path,
    ) -> None:
        """
        Args:
            agent_type:   Logical agent identifier (e.g. "researcher").
            scope:        One of "user", "project", "local".
            project_root: Absolute project root path.
        """
        if scope not in ("user", "project", "local"):
            raise ValueError(f"Invalid scope '{scope}'. Must be user/project/local.")
        self.agent_type = agent_type
        self.scope = scope
        self.project_root = project_root
        self._require_citation: bool = False

    def configure(self, *, require_citation: bool = False) -> None:
        self._require_citation = require_citation

    @property
    def memory_dir(self) -> Path:
        return agent_memory_dir(self.agent_type, self.scope, project_root=self.project_root)

    def is_enabled(self) -> bool:
        disabled = os.environ.get("BOBO_DISABLE_AGENT_MEMORY", "").lower()
        return disabled not in ("1", "true", "yes")

    def ensure_dirs(self) -> None:
        from bobo_memory.core.atomic import ensure_dir, secure_dir
        d = self.memory_dir
        if self.scope == "local":
            secure_dir(d)
        else:
            ensure_dir(d)
        trash = d / ".trash"
        trash.mkdir(parents=True, exist_ok=True)

    def build_prompt(self) -> str:
        if not self.is_enabled():
            return ""

        # fire-and-forget directory creation
        try:
            self.ensure_dirs()
        except OSError:
            pass

        extra: list[str] = [scope_note(self.scope)]
        extra.append(citation_note(required=self._require_citation))

        return build_memory_prompt(
            "Persistent Agent Memory",
            self.memory_dir,
            extra_guidelines=extra,
        )
