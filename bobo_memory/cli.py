"""
bobo-memory CLI — command line interface.

Usage:
  bobo-memory init [--agent-type NAME] [--scope SCOPE]
  bobo-memory status
  bobo-memory audit [--date YYYY-MM-DD] [--limit N]
  bobo-memory lint
  bobo-memory ingest --file PATH [--adapter markdown]

(More sub-commands added in M3/M4/M5)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _get_client(args: argparse.Namespace):
    from bobo_memory import MemoryClient
    kwargs: dict = {"project_root": getattr(args, "project_root", None) or "."}
    if getattr(args, "agent_type", None):
        kwargs["agent_type"] = args.agent_type
    if getattr(args, "scope", None):
        kwargs["scope"] = args.scope
    return MemoryClient(**kwargs)


# ------------------------------------------------------------------ #
# init                                                                 #
# ------------------------------------------------------------------ #

def cmd_init(args: argparse.Namespace) -> None:
    from bobo_memory.config import BoboConfig
    root = Path(getattr(args, "project_root", None) or ".").resolve()

    cfg = BoboConfig.load(
        project_root=root,
        agent_type=getattr(args, "agent_type", None),
        scope=getattr(args, "scope", "project"),
    )

    bobo_dir = root / ".bobo"
    config_file = bobo_dir / "config.yaml"

    if config_file.exists() and not getattr(args, "force", False):
        print(f"[bobo-memory] Already initialised at {bobo_dir}")
        print("  Use --force to reinitialise.")
        return

    cfg.save(config_file)

    # create standard directories
    from bobo_memory import MemoryClient
    client = MemoryClient(project_root=root, agent_type=cfg.agent_type, scope=cfg.scope)

    print(f"[bobo-memory] Initialised at {bobo_dir}")
    print(f"  agent_type : {cfg.agent_type}")
    print(f"  scope      : {cfg.scope}")
    print(f"  layers     : {', '.join(cfg.enabled_layers)}")
    print()
    print("Next steps:")
    print("  1. Open .bobo/memory/ in Obsidian to browse your memory graph.")
    print("  2. Add build_system_prompt() to your agent's system prompt.")
    print("  3. Pass to_openai_tools() (or equivalent) to your LLM call.")


# ------------------------------------------------------------------ #
# status                                                               #
# ------------------------------------------------------------------ #

def cmd_status(args: argparse.Namespace) -> None:
    client = _get_client(args)
    st = client.status()
    print(json.dumps(st, indent=2, ensure_ascii=False, default=str))


# ------------------------------------------------------------------ #
# audit                                                                #
# ------------------------------------------------------------------ #

def cmd_audit(args: argparse.Namespace) -> None:
    client = _get_client(args)
    events = client.audit_log(
        date=getattr(args, "date", None),
        limit=getattr(args, "limit", 50),
    )
    if not events:
        print("[bobo-memory] No audit events found.")
        return
    for ev in events:
        status = "✓" if ev.get("ok") else "✗"
        print(f"{ev['ts']} {status} {ev.get('op','?'):20s} layer={ev.get('layer',''):<10s} {ev.get('path','')}")


# ------------------------------------------------------------------ #
# lint                                                                 #
# ------------------------------------------------------------------ #

def cmd_lint(args: argparse.Namespace) -> None:
    client = _get_client(args)
    report = client.lint()
    try:
        print(json.dumps(report.model_dump() if hasattr(report, "model_dump") else report, indent=2, ensure_ascii=False))
    except Exception:
        print(report)


# ------------------------------------------------------------------ #
# ingest                                                               #
# ------------------------------------------------------------------ #

def cmd_ingest(args: argparse.Namespace) -> None:
    client = _get_client(args)
    file_path = getattr(args, "file", None)
    adapter = getattr(args, "adapter", "markdown")
    if not file_path:
        print("[bobo-memory] Error: --file is required for ingest.", file=sys.stderr)
        sys.exit(1)
    result = client.ingest(adapter=adapter, path=file_path)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


# ------------------------------------------------------------------ #
# Main parser                                                          #
# ------------------------------------------------------------------ #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bobo-memory",
        description="Universal memory middleware for LLM agents.",
    )
    parser.add_argument(
        "--project-root", default=".", help="Project root directory (default: cwd)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init", help="Initialise bobo-memory in the current project.")
    p_init.add_argument("--agent-type", default="default", dest="agent_type")
    p_init.add_argument("--scope", default="project", choices=["user", "project", "local"])
    p_init.add_argument("--force", action="store_true", help="Overwrite existing config.")
    p_init.set_defaults(func=cmd_init)

    # status
    p_status = sub.add_parser("status", help="Show memory system status.")
    p_status.add_argument("--agent-type", default=None, dest="agent_type")
    p_status.add_argument("--scope", default="project")
    p_status.set_defaults(func=cmd_status)

    # audit
    p_audit = sub.add_parser("audit", help="Show recent audit log entries.")
    p_audit.add_argument("--date", default=None, help="Date YYYY-MM-DD (default: today)")
    p_audit.add_argument("--limit", type=int, default=50)
    p_audit.set_defaults(func=cmd_audit)

    # lint
    p_lint = sub.add_parser("lint", help="Run wiki health check.")
    p_lint.set_defaults(func=cmd_lint)

    # ingest
    p_ingest = sub.add_parser("ingest", help="Ingest a file into raw/ + staging/.")
    p_ingest.add_argument("--file", required=True, help="File to ingest.")
    p_ingest.add_argument("--adapter", default="markdown", help="Adapter name (default: markdown)")
    p_ingest.set_defaults(func=cmd_ingest)

    # proposal
    p_proposal = sub.add_parser("proposal", help="Manage write proposals.")
    p_prop_sub = p_proposal.add_subparsers(dest="proposal_cmd", required=True)

    p_prop_list = p_prop_sub.add_parser("list", help="List pending proposals.")
    p_prop_list.add_argument("--layer", default=None)
    p_prop_list.set_defaults(func=cmd_proposal_list)

    p_prop_accept = p_prop_sub.add_parser("accept", help="Accept a proposal and merge it into memory.")
    p_prop_accept.add_argument("--id", required=True, dest="proposal_id")
    p_prop_accept.set_defaults(func=cmd_proposal_accept)

    p_prop_reject = p_prop_sub.add_parser("reject", help="Reject a proposal.")
    p_prop_reject.add_argument("--id", required=True, dest="proposal_id")
    p_prop_reject.set_defaults(func=cmd_proposal_reject)

    # snapshot
    p_snap = sub.add_parser("snapshot", help="Manage agent memory snapshots.")
    p_snap_sub = p_snap.add_subparsers(dest="snapshot_cmd", required=True)

    p_snap_save = p_snap_sub.add_parser("save", help="Export current memory as a snapshot.")
    p_snap_save.add_argument("--agent-type", default=None, dest="agent_type")
    p_snap_save.add_argument("--scope", default="user")
    p_snap_save.add_argument("--out", default=None, help="Output directory (default: .bobo/snapshots/<agent_type>/)")
    p_snap_save.set_defaults(func=cmd_snapshot_save)

    p_snap_apply = p_snap_sub.add_parser("apply", help="Apply a snapshot to local memory.")
    p_snap_apply.add_argument("--agent-type", default=None, dest="agent_type")
    p_snap_apply.add_argument("--scope", default="user")
    p_snap_apply.add_argument("--replace", action="store_true", help="Replace (overwrite) local memory.")
    p_snap_apply.set_defaults(func=cmd_snapshot_apply)

    p_snap_status = p_snap_sub.add_parser("status", help="Check snapshot sync status.")
    p_snap_status.add_argument("--agent-type", default=None, dest="agent_type")
    p_snap_status.add_argument("--scope", default="user")
    p_snap_status.set_defaults(func=cmd_snapshot_status)

    # serve (viewer)
    p_serve = sub.add_parser("serve", help="Start the optional web viewer.")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.set_defaults(func=cmd_serve)

    # mcp (Model Context Protocol server, stdio)
    p_mcp = sub.add_parser("mcp", help="Run the MCP stdio server (requires bobo-memory[mcp]).")
    p_mcp.add_argument("--agent-type", default=None, dest="agent_type")
    p_mcp.add_argument("--scope", default=None)
    p_mcp.set_defaults(func=cmd_mcp)

    return parser


# ------------------------------------------------------------------ #
# proposal                                                             #
# ------------------------------------------------------------------ #

def cmd_proposal_list(args: argparse.Namespace) -> None:
    from bobo_memory.tools.proposal import list_proposals
    root = Path(getattr(args, "project_root", ".")).resolve()
    proposals = list_proposals(root, layer=getattr(args, "layer", None))
    if not proposals:
        print("[bobo-memory] No pending proposals.")
        return
    for p in proposals:
        print(f"  [{p.get('proposal_id','?')}] {p.get('layer','?')} | {p.get('summary','?')} ({p.get('file','')})")


def cmd_proposal_accept(args: argparse.Namespace) -> None:
    client = _get_client(args)
    from bobo_memory.tools.proposal import accept_proposal
    result = accept_proposal(client.project_root, args.proposal_id, client)
    print(json.dumps(result, indent=2, default=str))


def cmd_proposal_reject(args: argparse.Namespace) -> None:
    root = Path(getattr(args, "project_root", ".")).resolve()
    from bobo_memory.tools.proposal import reject_proposal
    result = reject_proposal(root, args.proposal_id)
    print(json.dumps(result, indent=2, default=str))


# ------------------------------------------------------------------ #
# snapshot                                                             #
# ------------------------------------------------------------------ #

def cmd_snapshot_save(args: argparse.Namespace) -> None:
    client = _get_client(args)
    from bobo_memory.snapshot.manager import SnapshotManager
    from bobo_memory.core.paths import snapshot_dir
    agent_type = getattr(args, "agent_type", None) or client.agent_type
    scope = getattr(args, "scope", "user")
    out = getattr(args, "out", None)
    mgr = SnapshotManager(agent_type, client.project_root)
    target = Path(out) if out else snapshot_dir(agent_type, client.project_root)
    result = mgr.export(target, scope=scope)
    print(json.dumps(result, indent=2, default=str))


def cmd_snapshot_apply(args: argparse.Namespace) -> None:
    client = _get_client(args)
    from bobo_memory.snapshot.manager import SnapshotManager
    agent_type = getattr(args, "agent_type", None) or client.agent_type
    scope = getattr(args, "scope", "user")
    mgr = SnapshotManager(agent_type, client.project_root)
    if getattr(args, "replace", False):
        result = mgr.replace(scope=scope)
    else:
        result = mgr.initialize(scope=scope)
    print(json.dumps(result, indent=2, default=str))


def cmd_snapshot_status(args: argparse.Namespace) -> None:
    client = _get_client(args)
    from bobo_memory.snapshot.manager import SnapshotManager
    agent_type = getattr(args, "agent_type", None) or client.agent_type
    scope = getattr(args, "scope", "user")
    mgr = SnapshotManager(agent_type, client.project_root)
    action = mgr.check(scope=scope)
    print(json.dumps({"action": action, "agent_type": agent_type, "scope": scope}, indent=2))


# ------------------------------------------------------------------ #
# serve                                                                #
# ------------------------------------------------------------------ #

def cmd_serve(args: argparse.Namespace) -> None:
    root = getattr(args, "project_root", ".")
    port = getattr(args, "port", 8765)
    from bobo_memory.viewer.app import serve
    serve(project_root=root, port=port)


# ------------------------------------------------------------------ #
# mcp                                                                  #
# ------------------------------------------------------------------ #

def cmd_mcp(args: argparse.Namespace) -> None:
    from bobo_memory.mcp_server import serve_mcp
    serve_mcp(
        getattr(args, "project_root", "."),
        agent_type=getattr(args, "agent_type", None),
        scope=getattr(args, "scope", None),
    )


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
