"""
Configuration loader for bobo-memory.

Loads from .bobo/config.yaml (or a custom path).
All values can be overridden by environment variables.

Example config.yaml:
  agent_type: researcher
  scope: project
  enabled_layers:
    - agent
    - auto
    - wiki
    - session
  policy:
    write_mode: direct
    layers:
      wiki:
        require_citation: true
        write_mode: proposal
    max_file_size_kb: 100
    max_files_per_layer:
      auto: 200
      agent: 500
    trash:
      retention_days: 30
      allow_purge: true
    session:
      max_age_days: 90
    audit:
      retention_days: 90
    raw:
      max_file_size_kb: 1024
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from bobo_memory.core.policy import MemoryPolicy


class BoboConfig(BaseModel):
    """Top-level bobo-memory configuration."""

    model_config = {"arbitrary_types_allowed": True}

    agent_type: str = "default"
    scope: str = "project"
    enabled_layers: list[str] = Field(
        default_factory=lambda: ["agent", "auto", "wiki", "session"]
    )
    policy: MemoryPolicy = Field(default_factory=MemoryPolicy.default)
    project_root: Path = Field(default_factory=Path.cwd)

    @classmethod
    def load(
        cls,
        project_root: Path | str | None = None,
        config_path: Path | str | None = None,
        *,
        agent_type: str | None = None,
        scope: str | None = None,
        enabled_layers: list[str] | None = None,
    ) -> "BoboConfig":
        """Load config from yaml file, then apply any explicit overrides.

        Priority (highest → lowest):
          1. Explicit keyword arguments
          2. Environment variables
          3. config.yaml values
          4. Defaults
        """
        root = Path(project_root).resolve() if project_root else _detect_project_root()

        # locate config file
        if config_path:
            cfg_file = Path(config_path)
        else:
            cfg_file = root / ".bobo" / "config.yaml"

        raw: dict[str, Any] = {}
        if cfg_file.exists():
            with open(cfg_file, encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
                if isinstance(loaded, dict):
                    raw = loaded

        # build policy
        policy_raw = raw.pop("policy", {}) or {}
        policy = MemoryPolicy.from_dict(policy_raw)

        # build config
        cfg = cls(
            agent_type=raw.get("agent_type", "default"),
            scope=raw.get("scope", "project"),
            enabled_layers=raw.get("enabled_layers", ["agent", "auto", "wiki", "session"]),
            policy=policy,
            project_root=root,
        )

        # apply env overrides
        if env_type := os.environ.get("BOBO_AGENT_TYPE"):
            cfg.agent_type = env_type
        if env_scope := os.environ.get("BOBO_SCOPE"):
            cfg.scope = env_scope

        # apply explicit argument overrides
        if agent_type is not None:
            cfg.agent_type = agent_type
        if scope is not None:
            cfg.scope = scope
        if enabled_layers is not None:
            cfg.enabled_layers = enabled_layers

        cfg.project_root = root
        return cfg

    def save(self, config_path: Path | str | None = None) -> None:
        """Write current config back to yaml (creates .bobo/ if needed)."""
        path = Path(config_path) if config_path else (self.project_root / ".bobo" / "config.yaml")
        path.parent.mkdir(parents=True, exist_ok=True)

        policy_data: dict[str, Any] = {
            "write_mode": self.policy.write_mode,
            "max_file_size_kb": self.policy.max_file_size_kb,
            "forbidden_patterns": self.policy.forbidden_patterns,
            "trash": {
                "retention_days": self.policy.trash.retention_days,
                "allow_purge": self.policy.trash.allow_purge,
            },
            "layers": {
                name: {
                    "write_mode": lp.write_mode,
                    "writable_by": lp.writable_by,
                    "readable_by": lp.readable_by,
                    "require_citation": lp.require_citation,
                    "require_secret_scan": lp.require_secret_scan,
                }
                for name, lp in self.policy.layers.items()
            },
        }
        if self.policy.max_files_per_layer:
            policy_data["max_files_per_layer"] = dict(self.policy.max_files_per_layer)
        if self.policy.session.max_age_days is not None:
            policy_data["session"] = {"max_age_days": self.policy.session.max_age_days}
        if self.policy.audit.retention_days is not None:
            policy_data["audit"] = {"retention_days": self.policy.audit.retention_days}
        if self.policy.raw.max_file_size_kb is not None:
            policy_data["raw"] = {"max_file_size_kb": self.policy.raw.max_file_size_kb}
        policy_data["staging"] = {
            "lease_minutes": self.policy.staging.lease_minutes,
            "max_attempts": self.policy.staging.max_attempts,
        }

        data: dict[str, Any] = {
            "agent_type": self.agent_type,
            "scope": self.scope,
            "enabled_layers": self.enabled_layers,
            "policy": policy_data,
        }
        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(data, fh, allow_unicode=True, sort_keys=False)


def _detect_project_root() -> Path:
    """Walk up from cwd to find the git root; fall back to cwd."""
    cwd = Path.cwd()
    cur = cwd
    for _ in range(20):
        if (cur / ".git").exists() or (cur / ".bobo").exists():
            return cur
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return cwd
