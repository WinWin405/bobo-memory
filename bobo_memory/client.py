"""
MemoryClient — the main facade for bobo-memory.

Usage::

    from bobo_memory import MemoryClient

    client = MemoryClient(
        project_root=".",
        agent_type="researcher",
        scope="project",
    )

    system_prompt = client.build_system_prompt("You are a research agent.")
    tools = client.to_openai_tools()
    result = client.dispatch_tool_call("memory_save", {...})
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bobo_memory.config import BoboConfig
from bobo_memory.core.audit import log_event
from bobo_memory.core.guard import MemoryGuard
from bobo_memory.core.paths import audit_dir
from bobo_memory.core.policy import MemoryPolicy
from bobo_memory.layers.agent_memory import AgentMemory
from bobo_memory.layers.auto_memory import AutoMemory
from bobo_memory.layers.session_memory import SessionMemory


class MemoryClient:
    """Universal memory middleware facade.

    All public methods are safe to call from synchronous code paths.
    """

    def __init__(
        self,
        project_root: str | Path | None = None,
        agent_type: str | None = None,
        scope: str = "project",
        enabled_layers: list[str] | None = None,
        config_path: str | Path | None = None,
    ) -> None:
        self.config = BoboConfig.load(
            project_root=project_root,
            config_path=config_path,
            agent_type=agent_type,
            scope=scope,
            enabled_layers=enabled_layers,
        )

        self.project_root: Path = self.config.project_root
        self.agent_type: str = self.config.agent_type
        self.scope: str = self.config.scope
        self.policy: MemoryPolicy = self.config.policy

        self.guard = MemoryGuard(
            project_root=self.project_root,
            agent_type=self.agent_type,
            scope=self.scope,
            policy=self.policy,
        )
        self._audit_dir = audit_dir(self.project_root)

        # --- Layer instances ---
        self._agent_mem = AgentMemory(self.agent_type, self.scope, self.project_root)
        lp = self.policy.layer_policy("agent")
        self._agent_mem.configure(require_citation=lp.require_citation)

        self._auto_mem = AutoMemory(self.project_root)
        self._session_mem = SessionMemory(self.project_root)
        self._last_nudge_msg_count: int | None = None  # None → no nudge sent yet

        # Init directories for enabled layers
        self._init_dirs()

    # ------------------------------------------------------------------ #
    # Init                                                                 #
    # ------------------------------------------------------------------ #

    def _init_dirs(self) -> None:
        layers = self.config.enabled_layers
        try:
            if "agent" in layers:
                self._agent_mem.ensure_dirs()
            if "auto" in layers:
                self._auto_mem.ensure_dirs()
            if "session" in layers:
                self._session_mem.ensure_dirs()
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    # System prompt injection                                              #
    # ------------------------------------------------------------------ #

    def build_system_prompt(
        self,
        base_prompt: str = "",
        *,
        include: list[str] | None = None,
        extra_guidelines: list[str] | None = None,
    ) -> str:
        """Build a system prompt with memory context injected.

        Args:
            base_prompt:       The agent's own system prompt.
            include:           Which memory layers to inject. Defaults to all enabled.
                               Options: "agent_memory", "auto_memory", "wiki_index"
            extra_guidelines:  Additional instructions appended after memory sections.

        Returns:
            Complete system prompt string ready to use.
        """
        include = include or ["agent_memory", "auto_memory"]
        layers = self.config.enabled_layers

        sections: list[str] = []
        if base_prompt.strip():
            sections.append(base_prompt.strip())

        # Per-layer fragments carry only their directory, scope note and index;
        # the shared how-to instructions are injected once below.
        layer_fragments: list[str] = []

        if "agent_memory" in include and "agent" in layers:
            fragment = self._agent_mem.build_prompt(include_instructions=False)
            if fragment:
                layer_fragments.append(fragment)

        if "auto_memory" in include and "auto" in layers:
            fragment = self._auto_mem.build_prompt(include_instructions=False)
            if fragment:
                layer_fragments.append(fragment)

        if "wiki_index" in include and "wiki" in layers:
            from bobo_memory.layers.wiki import WikiLayer
            wiki = WikiLayer(self.project_root)
            fragment = wiki.build_prompt()
            if fragment:
                layer_fragments.append(fragment)

        if layer_fragments:
            from bobo_memory.core.memdir import build_shared_instructions
            sections.append(build_shared_instructions())
            if len(layer_fragments) > 1:
                from bobo_memory.core.prompts import LAYER_ROUTING_NOTE
                sections.append(LAYER_ROUTING_NOTE)
            sections.extend(layer_fragments)

        if extra_guidelines:
            sections.extend(extra_guidelines)

        return "\n\n".join(sections)

    # ------------------------------------------------------------------ #
    # Recall                                                               #
    # ------------------------------------------------------------------ #

    def recall(
        self,
        query: str,
        *,
        k: int = 5,
        layers: list[str] | None = None,
        already_surfaced: list[str] | None = None,
        recent_tools: list[str] | None = None,
        token_budget: int = 8000,
        mode: str = "bm25",
    ) -> "Any":
        """Recall relevant memory files for *query*. Returns a ContextPack."""
        from bobo_memory.core.recall import find_relevant_memories
        return find_relevant_memories(
            query=query,
            k=k,
            layers=layers or self.config.enabled_layers,
            project_root=self.project_root,
            agent_type=self.agent_type,
            scope=self.scope,
            already_surfaced=already_surfaced or [],
            recent_tools=recent_tools or [],
            token_budget=token_budget,
            mode=mode,
        )

    # ------------------------------------------------------------------ #
    # Auto-capture (piggyback mode — zero extra LLM calls)                 #
    # ------------------------------------------------------------------ #

    def memory_nudge(
        self,
        messages: list[dict],
        *,
        lookback: int = 4,
        cooldown_messages: int = 6,
    ) -> str:
        """Return a one-line system-prompt nudge when recent messages look memory-worthy.

        Rule-based scan only — never calls an LLM. Append the returned string
        to the NEXT request's system prompt; the main model then decides and
        calls memory_save within its normal turn.

        Returns "" when nothing fired or when fewer than *cooldown_messages*
        messages have been added since the last nudge (prevents nagging).
        """
        from bobo_memory.core.triggers import build_nudge, detect_memory_signal

        if (
            self._last_nudge_msg_count is not None
            and len(messages) - self._last_nudge_msg_count < cooldown_messages
        ):
            return ""
        signal = detect_memory_signal(messages, lookback=lookback)
        if not signal.triggered:
            return ""
        self._last_nudge_msg_count = len(messages)
        return build_nudge(signal)

    def find_similar(
        self,
        content: str,
        *,
        k: int = 3,
        layers: list[str] | None = None,
    ) -> list[Any]:
        """Return up to *k* existing memories similar to *content* (BM25, no LLM).

        Use before saving to decide between memory_save and memory_update.
        """
        pack = self.recall(query=content, k=k, layers=layers)
        return list(pack.files)

    # ------------------------------------------------------------------ #
    # Tools                                                                #
    # ------------------------------------------------------------------ #

    def tool_specs(self) -> list[Any]:
        """Return all tool specs for this client."""
        from bobo_memory.tools.specs import get_tool_specs
        return get_tool_specs(self)

    def to_openai_tools(self) -> list[dict]:
        """Return tools formatted for the OpenAI chat completions API."""
        from bobo_memory.tools.adapters import to_openai_tools
        return to_openai_tools(self.tool_specs())

    def to_anthropic_tools(self) -> list[dict]:
        """Return tools formatted for the Anthropic messages API."""
        from bobo_memory.tools.adapters import to_anthropic_tools
        return to_anthropic_tools(self.tool_specs())

    def to_langchain_tools(self) -> list[Any]:
        """Return tools as LangChain StructuredTool objects."""
        from bobo_memory.tools.adapters import to_langchain_tools
        return to_langchain_tools(self.tool_specs())

    def dispatch_tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        actor: str = "agent",
    ) -> dict[str, Any]:
        """Execute a tool call by name and return the result dict.

        This is the single entry-point for all tool invocations.
        Internally runs the policy→guard→atomic→audit pipeline.
        """
        from bobo_memory.tools.handlers import dispatch
        return dispatch(name, arguments, client=self, actor=actor)

    # ------------------------------------------------------------------ #
    # Ingest                                                               #
    # ------------------------------------------------------------------ #

    def ingest(
        self,
        *,
        adapter: str,
        payload: Any = None,
        path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Receive an external document and land it in raw/ + staging/.

        No LLM is called. The agent picks up the task via ingest_next().
        The raw document size is bounded by policy.raw.max_file_size_kb when set.
        """
        from bobo_memory.stream.inbox import land_raw
        return land_raw(
            adapter_name=adapter,
            payload=payload,
            path=path,
            project_root=self.project_root,
            max_raw_bytes=self.policy.raw.max_bytes(),
        )

    def register_adapter(self, adapter: Any) -> None:
        """Register a custom StreamAdapter."""
        from bobo_memory.stream.pipeline import StreamPipeline
        StreamPipeline.global_registry[adapter.name] = adapter

    def watch_directory(self, directory: str | Path, *, adapter: str = "markdown") -> None:
        """Start watching a directory for new files (non-blocking background thread)."""
        from bobo_memory.stream.adapters.file_watch import start_watch
        start_watch(Path(directory), adapter_name=adapter, client=self)

    # ------------------------------------------------------------------ #
    # Session memory                                                       #
    # ------------------------------------------------------------------ #

    @property
    def session(self) -> SessionMemory:
        return self._session_mem

    # ------------------------------------------------------------------ #
    # Compaction                                                           #
    # ------------------------------------------------------------------ #

    @property
    def compact(self) -> "Any":
        """Lazy accessor for the context-compaction helper."""
        from bobo_memory.compact import CompactHelper
        return CompactHelper(self)

    # ------------------------------------------------------------------ #
    # Snapshot                                                             #
    # ------------------------------------------------------------------ #

    @property
    def snapshot(self) -> "Any":
        """Lazy accessor for the snapshot manager."""
        from bobo_memory.snapshot.manager import SnapshotManager
        return SnapshotManager(self.agent_type, self.project_root)

    # ------------------------------------------------------------------ #
    # Team memory                                                          #
    # ------------------------------------------------------------------ #

    @property
    def team(self) -> "Any":
        """Lazy accessor for the team memory layer."""
        from bobo_memory.layers.team_memory import TeamMemory
        return TeamMemory(self.project_root)

    # ------------------------------------------------------------------ #
    # Lint                                                                 #
    # ------------------------------------------------------------------ #

    def lint(self) -> "Any":
        """Run the wiki health checker and return a LintReport."""
        from bobo_memory.lint.checker import run_lint
        return run_lint(self.project_root)

    # ------------------------------------------------------------------ #
    # Audit                                                                #
    # ------------------------------------------------------------------ #

    def audit_log(self, *, date: str | None = None, limit: int = 50) -> list[dict]:
        """Read recent audit events."""
        from bobo_memory.core.audit import read_events
        return read_events(self._audit_dir, date=date, limit=limit)

    def _log(
        self,
        op: str,
        layer: str = "",
        path: str = "",
        *,
        actor: str = "agent",
        tool: str = "",
        ok: bool = True,
        error: str | None = None,
        bytes_written: int = 0,
    ) -> None:
        """Internal convenience wrapper for audit logging."""
        log_event(
            self._audit_dir,
            op=op,
            layer=layer,
            path=path,
            actor=actor,
            tool=tool,
            ok=ok,
            error=error,
            bytes_written=bytes_written,
        )

    # ------------------------------------------------------------------ #
    # Janitor                                                              #
    # ------------------------------------------------------------------ #

    def run_janitor(self) -> dict[str, Any]:
        """Run all storage governance cleanup tasks according to policy.

        Executes in order:
          1. purge_expired_trash  — removes .trash files older than policy.trash.retention_days
          2. cleanup_sessions     — removes session files older than policy.session.max_age_days
          3. rotate_audit         — removes old audit JSONL files per policy.audit.retention_days
          4. cleanup_locks        — removes stale *.lock files under .bobo/ (older than 24h)

        Returns a report dict::

            {
              "trash":   {"deleted_files": int, "freed_bytes": int, "errors": [...]},
              "session": {"deleted_files": int, "freed_bytes": int, "errors": [...]},
              "audit":   {"deleted_files": int, "freed_bytes": int, "errors": [...]},
              "locks":   {"deleted_files": int, "freed_bytes": int, "errors": [...]},
              "total_freed_bytes": int,
            }
        """
        from bobo_memory.core.janitor import (
            cleanup_locks,
            cleanup_sessions,
            purge_expired_trash,
            rotate_audit,
        )

        trash_report = purge_expired_trash(self.project_root, self.policy)
        session_report = cleanup_sessions(self.project_root, self.policy)
        audit_report = rotate_audit(self.project_root, self.policy)
        locks_report = cleanup_locks(self.project_root)

        return {
            "trash": trash_report,
            "session": session_report,
            "audit": audit_report,
            "locks": locks_report,
            "total_freed_bytes": (
                trash_report["freed_bytes"]
                + session_report["freed_bytes"]
                + audit_report["freed_bytes"]
                + locks_report["freed_bytes"]
            ),
        }

    # ------------------------------------------------------------------ #
    # Storage stats                                                        #
    # ------------------------------------------------------------------ #

    def storage_stats(self) -> dict[str, Any]:
        """Return a storage usage summary for all known memory directories.

        Each layer entry contains:
          - ``files``:       number of .md files (excluding MEMORY.md)
          - ``bytes``:       total size of those files
          - ``trash_files``: number of files in .trash/
          - ``trash_bytes``: total size of .trash/ files

        Top-level keys also include ``audit_bytes`` for the .bobo/audit/ directory.

        All counts default to 0 for non-existent directories.
        """
        def _dir_stats(d: Path, *, exclude_name: str = "") -> tuple[int, int]:
            """Return (file_count, total_bytes) for *.md files in *d*."""
            count = 0
            total = 0
            if not d.is_dir():
                return count, total
            for f in d.iterdir():
                if f.is_file() and f.suffix == ".md" and f.name != exclude_name:
                    count += 1
                    try:
                        total += f.stat().st_size
                    except OSError:
                        pass
            return count, total

        def _dir_bytes(d: Path) -> int:
            """Return total bytes of all files directly inside *d*."""
            if not d.is_dir():
                return 0
            total = 0
            for f in d.iterdir():
                if f.is_file():
                    try:
                        total += f.stat().st_size
                    except OSError:
                        pass
            return total

        layers_stats: dict[str, Any] = {}

        # agent layer
        if "agent" in self.config.enabled_layers:
            d = self._agent_mem.memory_dir
            fc, fb = _dir_stats(d, exclude_name="MEMORY.md")
            tc, tb = _dir_stats(d / ".trash")
            layers_stats["agent"] = {"files": fc, "bytes": fb, "trash_files": tc, "trash_bytes": tb}

        # auto layer
        if "auto" in self.config.enabled_layers:
            d = self._auto_mem.memory_dir
            fc, fb = _dir_stats(d, exclude_name="MEMORY.md")
            tc, tb = _dir_stats(d / ".trash")
            layers_stats["auto"] = {"files": fc, "bytes": fb, "trash_files": tc, "trash_bytes": tb}

        # session layer
        if "session" in self.config.enabled_layers:
            d = self._session_mem.memory_dir
            fc, fb = _dir_stats(d)
            layers_stats["session"] = {"files": fc, "bytes": fb, "trash_files": 0, "trash_bytes": 0}

        # wiki / team (if present on disk)
        from bobo_memory.core.paths import team_memory_dir, wiki_dir
        for layer_name, layer_dir in [
            ("wiki", wiki_dir(self.project_root)),
            ("team", team_memory_dir(self.project_root)),
        ]:
            if layer_dir.is_dir():
                fc, fb = _dir_stats(layer_dir, exclude_name="MEMORY.md")
                tc, tb = _dir_stats(layer_dir / ".trash")
                layers_stats[layer_name] = {"files": fc, "bytes": fb, "trash_files": tc, "trash_bytes": tb}

        audit_bytes = _dir_bytes(self._audit_dir)

        return {
            "project_root": str(self.project_root),
            "layers": layers_stats,
            "audit_bytes": audit_bytes,
        }

    # ------------------------------------------------------------------ #
    # Status                                                               #
    # ------------------------------------------------------------------ #

    def status(self) -> dict[str, Any]:
        """Return a status summary of the memory system."""
        from bobo_memory.core.paths import ENTRYPOINT_NAME

        result: dict[str, Any] = {
            "project_root": str(self.project_root),
            "agent_type": self.agent_type,
            "scope": self.scope,
            "enabled_layers": self.config.enabled_layers,
            "layers": {},
        }

        layers_info: dict[str, Any] = {}

        if "agent" in self.config.enabled_layers:
            d = self._agent_mem.memory_dir
            idx = d / ENTRYPOINT_NAME
            layers_info["agent"] = {
                "dir": str(d),
                "exists": d.exists(),
                "index_exists": idx.exists(),
                "index_lines": _count_lines(idx),
            }

        if "auto" in self.config.enabled_layers:
            d = self._auto_mem.memory_dir
            idx = d / ENTRYPOINT_NAME
            layers_info["auto"] = {
                "dir": str(d),
                "exists": d.exists(),
                "index_exists": idx.exists(),
                "index_lines": _count_lines(idx),
            }

        if "session" in self.config.enabled_layers:
            d = self._session_mem.memory_dir
            layers_info["session"] = {
                "dir": str(d),
                "exists": d.exists(),
                "session_id": self._session_mem._session_id,
            }

        result["layers"] = layers_info
        result["policy"] = {
            "write_mode": self.policy.write_mode,
            "max_file_size_kb": self.policy.max_file_size_kb,
        }
        return result


def _count_lines(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except (FileNotFoundError, OSError):
        return 0
