"""
Declarative MemoryPolicy — loaded from config.yaml's policy: section.

Provides:
  - MemoryPolicy       : the root policy object
  - LayerPolicy        : per-layer overrides
  - TrashPolicy        : trash retention settings
  - SessionPolicy      : session file expiry settings
  - AuditPolicy        : audit log rotation settings
  - RawPolicy          : raw ingest size limit
  - PolicyViolation    : exception raised on denied actions
  - check_action()     : the single entry-point that all handlers call

Default config.yaml policy section:

  policy:
    write_mode: direct          # direct | proposal
    layers:
      session:
        writable_by: [system]
        readable_by: [agent, human]
      team:
        require_secret_scan: true
      wiki:
        require_citation: true
        write_mode: proposal
      agent:
        require_citation: false
    forbidden_patterns:
      - "api[_-]?key\\s*=\\s*['\"]?[A-Za-z0-9]{20,}"
      - "-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----"
    max_file_size_kb: 100
    max_files_per_layer: {}     # e.g. {auto: 200, agent: 500}; empty = unlimited
    trash:
      retention_days: 30
      allow_purge: true
    session:
      max_age_days: null        # null = no expiry
    audit:
      retention_days: null      # null = keep forever
    raw:
      max_file_size_kb: null    # null = unlimited
    staging:
      lease_minutes: 30         # ingest task lease before retry
      max_attempts: 3           # leases before a task is marked failed
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_FORBIDDEN_PATTERNS: list[str] = [
    r"api[_\-]?key\s*=\s*['\"]?[A-Za-z0-9]{20,}",
    r"-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----",
    r"(?i)password\s*=\s*['\"][^'\"]{8,}['\"]",
    r"(?i)secret\s*=\s*['\"][^'\"]{8,}['\"]",
]


class PolicyViolation(Exception):
    """Raised when a memory operation violates the active policy."""

    def __init__(self, reason: str, tool: str = "", layer: str = "") -> None:
        self.reason = reason
        self.tool = tool
        self.layer = layer
        super().__init__(f"[PolicyViolation] {tool}/{layer}: {reason}")


@dataclass
class TrashPolicy:
    retention_days: int = 30
    allow_purge: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TrashPolicy":
        return cls(
            retention_days=int(d.get("retention_days", 30)),
            allow_purge=bool(d.get("allow_purge", True)),
        )


@dataclass
class SessionPolicy:
    """Controls session file expiry for the janitor."""

    max_age_days: int | None = None  # None = no expiry

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SessionPolicy":
        v = d.get("max_age_days")
        return cls(max_age_days=int(v) if v is not None else None)


@dataclass
class AuditPolicy:
    """Controls audit log rotation for the janitor."""

    retention_days: int | None = None  # None = keep forever

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AuditPolicy":
        v = d.get("retention_days")
        return cls(retention_days=int(v) if v is not None else None)


@dataclass
class StagingPolicy:
    """Controls the ingest task lease/retry behaviour.

    A task returned by ingest_next is leased (not deleted): if the agent does
    not confirm with ingest_done within *lease_minutes*, the task becomes
    available again, up to *max_attempts* leases; after that it is marked
    'failed' and kept in staging for inspection.
    """

    lease_minutes: int = 30
    max_attempts: int = 3

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StagingPolicy":
        return cls(
            lease_minutes=int(d.get("lease_minutes", 30)),
            max_attempts=int(d.get("max_attempts", 3)),
        )


@dataclass
class RawPolicy:
    """Controls maximum size for raw/ ingest documents."""

    max_file_size_kb: int | None = None  # None = unlimited

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RawPolicy":
        v = d.get("max_file_size_kb")
        return cls(max_file_size_kb=int(v) if v is not None else None)

    def max_bytes(self) -> int | None:
        return self.max_file_size_kb * 1024 if self.max_file_size_kb is not None else None


@dataclass
class LayerPolicy:
    """Per-layer policy overrides."""

    write_mode: str | None = None           # "direct" | "proposal" | None (inherit)
    writable_by: list[str] = field(default_factory=lambda: ["agent", "system"])
    readable_by: list[str] = field(default_factory=lambda: ["agent", "human", "system"])
    require_citation: bool = False
    require_secret_scan: bool = False
    max_file_size_kb: int | None = None     # None → inherit global

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LayerPolicy":
        return cls(
            write_mode=d.get("write_mode"),
            writable_by=d.get("writable_by", ["agent", "system"]),
            readable_by=d.get("readable_by", ["agent", "human", "system"]),
            require_citation=bool(d.get("require_citation", False)),
            require_secret_scan=bool(d.get("require_secret_scan", False)),
            max_file_size_kb=d.get("max_file_size_kb"),
        )


@dataclass
class MemoryPolicy:
    """Root policy object — loaded once, queried everywhere."""

    write_mode: str = "direct"                      # global default
    layers: dict[str, LayerPolicy] = field(default_factory=dict)
    forbidden_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_FORBIDDEN_PATTERNS)
    )
    max_file_size_kb: int = 100
    max_files_per_layer: dict[str, int] = field(default_factory=dict)
    trash: TrashPolicy = field(default_factory=TrashPolicy)
    session: SessionPolicy = field(default_factory=SessionPolicy)
    audit: AuditPolicy = field(default_factory=AuditPolicy)
    raw: RawPolicy = field(default_factory=RawPolicy)
    staging: StagingPolicy = field(default_factory=StagingPolicy)

    # compiled regex cache
    _compiled: list[re.Pattern] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._compiled = [re.compile(p) for p in self.forbidden_patterns]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryPolicy":
        layers: dict[str, LayerPolicy] = {}
        for name, cfg in d.get("layers", {}).items():
            layers[name] = LayerPolicy.from_dict(cfg)

        forbidden = d.get("forbidden_patterns", DEFAULT_FORBIDDEN_PATTERNS)
        trash_cfg = d.get("trash", {}) or {}
        session_cfg = d.get("session", {}) or {}
        audit_cfg = d.get("audit", {}) or {}
        raw_cfg = d.get("raw", {}) or {}
        staging_cfg = d.get("staging", {}) or {}
        max_files = d.get("max_files_per_layer", {}) or {}

        return cls(
            write_mode=d.get("write_mode", "direct"),
            layers=layers,
            forbidden_patterns=forbidden,
            max_file_size_kb=int(d.get("max_file_size_kb", 100)),
            max_files_per_layer={k: int(v) for k, v in max_files.items()},
            trash=TrashPolicy.from_dict(trash_cfg),
            session=SessionPolicy.from_dict(session_cfg),
            audit=AuditPolicy.from_dict(audit_cfg),
            raw=RawPolicy.from_dict(raw_cfg),
            staging=StagingPolicy.from_dict(staging_cfg),
        )

    @classmethod
    def default(cls) -> "MemoryPolicy":
        return cls()

    # ------------------------------------------------------------------ #
    # Accessors                                                            #
    # ------------------------------------------------------------------ #

    def layer_policy(self, layer: str) -> LayerPolicy:
        return self.layers.get(layer, LayerPolicy())

    def effective_write_mode(self, layer: str) -> str:
        lp = self.layer_policy(layer)
        return lp.write_mode if lp.write_mode is not None else self.write_mode

    def effective_max_size_bytes(self, layer: str) -> int:
        lp = self.layer_policy(layer)
        kb = lp.max_file_size_kb if lp.max_file_size_kb is not None else self.max_file_size_kb
        return kb * 1024

    def effective_max_files(self, layer: str) -> int | None:
        """Return the maximum number of files allowed in *layer*, or None if unlimited."""
        return self.max_files_per_layer.get(layer)

    # ------------------------------------------------------------------ #
    # Core check                                                           #
    # ------------------------------------------------------------------ #

    def check_action(
        self,
        tool: str,
        layer: str,
        *,
        actor: str = "agent",
        content: str = "",
        require_sources: bool | None = None,
        sources: list[str] | None = None,
    ) -> None:
        """Raise PolicyViolation if the action is not allowed.

        Args:
            tool:            Tool name being invoked (e.g. "memory_save").
            layer:           Memory layer (e.g. "wiki", "agent", "session").
            actor:           Who is calling ("agent", "system", "human").
            content:         File content about to be written (for secret scan).
            require_sources: Override citation requirement check.
            sources:         Provided source ids; checked when citation required.
        """
        lp = self.layer_policy(layer)
        is_write = tool not in {"memory_list", "memory_read", "memory_recall", "ingest_next"}

        # 1. writable_by check
        if is_write and actor not in lp.writable_by:
            raise PolicyViolation(
                f"actor '{actor}' is not in writable_by {lp.writable_by} for layer '{layer}'",
                tool=tool,
                layer=layer,
            )

        # 2. content size check
        if is_write and content:
            max_bytes = self.effective_max_size_bytes(layer)
            if len(content.encode("utf-8")) > max_bytes:
                raise PolicyViolation(
                    f"content size {len(content.encode())} bytes exceeds "
                    f"max_file_size_kb={max_bytes // 1024} for layer '{layer}'",
                    tool=tool,
                    layer=layer,
                )

        # 3. forbidden patterns — global scan for all write operations
        if is_write and content and self._compiled:
            for pattern in self._compiled:
                if pattern.search(content):
                    raise PolicyViolation(
                        f"content matches forbidden pattern '{pattern.pattern}' — "
                        "possible secret detected",
                        tool=tool,
                        layer=layer,
                    )

        # 4. citation requirement
        need_citation = require_sources if require_sources is not None else lp.require_citation
        if is_write and tool == "memory_save" and need_citation:
            if not sources:
                raise PolicyViolation(
                    f"layer '{layer}' requires citation (sources must not be empty)",
                    tool=tool,
                    layer=layer,
                )

    def check_purge_allowed(self, layer: str) -> None:
        """Raise if purge (permanent delete) is disabled by policy."""
        if not self.trash.allow_purge:
            raise PolicyViolation(
                "permanent purge is disabled by policy (trash.allow_purge=false)",
                tool="memory_purge",
                layer=layer,
            )
