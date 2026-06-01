"""
Proposal Queue — redirects writes to .bobo/proposals/ when policy.write_mode=proposal.

Proposals are pending memory writes waiting for human/lint review before merging.
CLI: bobo-memory proposal list/accept/reject
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bobo_memory.client import MemoryClient

from bobo_memory.core.atomic import atomic_write, file_lock
from bobo_memory.core.paths import proposals_dir


def write_proposal(
    *,
    client: "MemoryClient",
    layer: str,
    topic: str,
    filename: str,
    content: str,
    summary: str,
    tags: list[str],
    sources: list[str],
) -> dict[str, Any]:
    """Write a proposal file instead of directly saving to memory."""
    proposals = proposals_dir(client.project_root) / layer
    proposals.mkdir(parents=True, exist_ok=True)

    proposal_id = str(uuid.uuid4())[:8]
    proposal_filename = f"{topic}.{proposal_id}.md"
    proposal_path = proposals / proposal_filename

    today = date.today().isoformat()
    header = (
        f"---\n"
        f"proposal_id: {proposal_id}\n"
        f"layer: {layer}\n"
        f"target_file: {filename}\n"
        f"summary: \"{summary}\"\n"
        f"sources: {sources}\n"
        f"tags: {tags}\n"
        f"status: pending\n"
        f"created: {today}\n"
        f"---\n\n"
    )

    with file_lock(proposal_path):
        atomic_write(proposal_path, header + content)

    client._log(
        "proposal_created", layer,
        str(proposal_path.relative_to(client.project_root)),
        tool="memory_save",
    )

    return {
        "ok": True,
        "proposal": True,
        "proposal_file": str(proposal_path.relative_to(client.project_root)),
        "proposal_id": proposal_id,
        "message": (
            f"Write redirected to proposal queue (layer={layer}, write_mode=proposal). "
            f"Review with: bobo-memory proposal accept --id {proposal_id}"
        ),
    }


def list_proposals(project_root: Path, layer: str | None = None) -> list[dict[str, Any]]:
    """Return all pending proposals, optionally filtered by layer."""
    import yaml

    base = proposals_dir(project_root)
    if not base.exists():
        return []

    proposals: list[dict] = []
    pattern = "**/*.md"
    for pf in base.glob(pattern):
        try:
            text = pf.read_text(encoding="utf-8")
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 2:
                    meta = yaml.safe_load(parts[1]) or {}
                    if meta.get("status") == "pending":
                        if layer and meta.get("layer") != layer:
                            continue
                        proposals.append({
                            "file": str(pf.relative_to(project_root)),
                            **meta,
                        })
        except Exception:
            pass
    return proposals


def accept_proposal(project_root: Path, proposal_id: str, client: "MemoryClient") -> dict:
    """Accept a proposal — merge it into the target memory layer."""
    import yaml

    base = proposals_dir(project_root)
    matches = list(base.rglob(f"*.{proposal_id}.md"))
    if not matches:
        return {"ok": False, "error": f"Proposal '{proposal_id}' not found"}

    pf = matches[0]
    text = pf.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {"ok": False, "error": "Invalid proposal file format"}

    meta = yaml.safe_load(parts[1]) or {}
    content = parts[2].lstrip()

    layer = meta.get("layer", "auto")
    filename = meta.get("target_file", "proposal.md")
    summary = meta.get("summary", "")
    sources = meta.get("sources") or []
    tags = meta.get("tags") or []

    from bobo_memory.tools.handlers import _layer_dir, _build_frontmatter
    from bobo_memory.core.memdir import update_entrypoint_index
    from bobo_memory.core.atomic import atomic_write, file_lock

    mem_dir = _layer_dir(client, layer)
    mem_dir.mkdir(parents=True, exist_ok=True)
    file_path = mem_dir / filename

    front = _build_frontmatter(sources, tags)
    full_content = front + content

    with file_lock(file_path):
        atomic_write(file_path, full_content)
    update_entrypoint_index(mem_dir, filename=filename, summary=summary)

    # Mark proposal as accepted
    import re
    new_text = re.sub(r"status: pending", "status: accepted", text, count=1)
    with file_lock(pf):
        atomic_write(pf, new_text)

    client._log("proposal_accepted", layer, str(file_path.relative_to(project_root)), tool="proposal_accept")
    return {"ok": True, "accepted": proposal_id, "written_to": str(file_path.relative_to(project_root))}


def reject_proposal(project_root: Path, proposal_id: str) -> dict:
    """Reject a proposal — mark it rejected (file kept for audit)."""
    import re

    base = proposals_dir(project_root)
    matches = list(base.rglob(f"*.{proposal_id}.md"))
    if not matches:
        return {"ok": False, "error": f"Proposal '{proposal_id}' not found"}

    pf = matches[0]
    text = pf.read_text(encoding="utf-8")
    new_text = re.sub(r"status: pending", "status: rejected", text, count=1)
    from bobo_memory.core.atomic import atomic_write
    atomic_write(pf, new_text)
    return {"ok": True, "rejected": proposal_id}
