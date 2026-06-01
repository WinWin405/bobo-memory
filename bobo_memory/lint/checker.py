"""
Wiki and memory health checker (Lint).

Generates a LintReport without modifying any files.
The agent reads the report and uses tools to fix issues.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class LintReport(BaseModel):
    """Result of a lint pass over the memory system."""

    orphans: list[str] = []          # pages with no inbound links
    broken_links: list[str] = []     # links pointing to non-existent files
    duplicates: list[list[str]] = [] # groups of near-duplicate files
    stale: list[str] = []            # pages whose sources no longer exist
    missing_xref: list[str] = []     # pages mentioned but lacking their own page
    missing_citation: list[str] = [] # pages that require citation but have none
    summary: str = ""


def run_lint(project_root: Path | str) -> LintReport:
    """Run a full lint pass and return a LintReport."""
    project_root = Path(project_root)
    wiki_dir = project_root / ".bobo" / "memory" / "wiki"
    raw_dir = project_root / ".bobo" / "raw"

    report = LintReport()

    if not wiki_dir.exists():
        report.summary = "Wiki directory not found — nothing to lint."
        return report

    md_files = list(wiki_dir.rglob("*.md"))
    if not md_files:
        report.summary = "No markdown files found in wiki."
        return report

    # Build a set of all known file names
    all_names = {f.name for f in md_files} | {f.stem for f in md_files}
    all_paths = {str(f.relative_to(wiki_dir)) for f in md_files}

    # Collect links per file
    inbound: dict[str, list[str]] = {str(f.relative_to(wiki_dir)): [] for f in md_files}
    broken: list[str] = []

    link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

    for f in md_files:
        try:
            content = f.read_text(encoding="utf-8")
        except OSError:
            continue

        rel = str(f.relative_to(wiki_dir))

        for _, href in link_pattern.findall(content):
            if href.startswith("http://") or href.startswith("https://"):
                continue
            # Resolve relative to the file's parent directory within wiki
            target = (f.parent / href).resolve()
            try:
                target_rel = str(target.relative_to(wiki_dir.resolve()))
            except ValueError:
                broken.append(f"{rel} → {href}")
                continue
            if target_rel in inbound:
                inbound[target_rel].append(rel)
            elif not target.exists():
                broken.append(f"{rel} → {href}")

    report.broken_links = broken

    # Orphan detection (not linked from index.md or any other page)
    index_path = "index.md"
    for rel, inbound_from in inbound.items():
        if rel == index_path:
            continue
        if not inbound_from:
            report.orphans.append(rel)

    # Citation check: look for files with empty sources: []
    citation_missing: list[str] = []
    for f in md_files:
        try:
            content = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if content.startswith("---"):
            front = content.split("---", 2)
            if len(front) >= 3 and ("sources: []" in front[1] or "sources:\n" not in front[1]):
                rel = str(f.relative_to(wiki_dir))
                # Only flag if it's in sources/ or entities/ (citation-critical)
                if any(part in rel for part in ("sources/", "entities/")):
                    citation_missing.append(rel)
    report.missing_citation = citation_missing

    total = len(md_files)
    report.summary = (
        f"Lint complete: {total} pages scanned. "
        f"{len(report.orphans)} orphans, "
        f"{len(report.broken_links)} broken links, "
        f"{len(report.missing_citation)} pages missing citations."
    )
    return report
