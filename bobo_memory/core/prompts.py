"""
Prompt template library for bobo-memory.

These templates are injected into agent system prompts to teach LLMs:
  - how / when to save memories
  - scope semantics (user vs project vs local)
  - citation requirements
  - what NOT to save
  - how to recall

All templates return plain strings ready for prompt concatenation.
"""

from __future__ import annotations

from pathlib import Path


# --------------------------------------------------------------------------- #
# Scope notes                                                                  #
# --------------------------------------------------------------------------- #

SCOPE_NOTES: dict[str, str] = {
    "user": (
        "This is your **user-scoped** memory. "
        "It persists across all projects. "
        "Save general preferences, skills, and knowledge that should apply everywhere — "
        "not project-specific details."
    ),
    "project": (
        "This is your **project-scoped** memory. "
        "It is shared by all agents and team members working on this project. "
        "Save project-specific facts, conventions, and decisions here."
    ),
    "local": (
        "This is your **local-scoped** memory. "
        "It is specific to this machine/environment and not shared. "
        "Save environment-specific configuration, local paths, and machine-specific notes here."
    ),
}


def scope_note(scope: str) -> str:
    return SCOPE_NOTES.get(scope, "")


# --------------------------------------------------------------------------- #
# Citation requirement                                                         #
# --------------------------------------------------------------------------- #

CITATION_REQUIRED_NOTE = """
## Citation requirement

This memory layer requires that every saved memory includes at least one source reference.
Set the `sources` field in the YAML frontmatter to the list of raw source IDs
(file paths relative to the project root, e.g. `raw/2026-05-28/abc123.md`).

Example frontmatter:
```yaml
---
sources:
  - raw/2026-05-28/abc123.md
tags: [budget, Q3]
created: 2026-05-28
updated: 2026-05-28
---
```

Do NOT save memories without traceable sources in this layer.
""".strip()

CITATION_OPTIONAL_NOTE = """
You may optionally include a `sources:` field in the frontmatter to trace
this memory back to its origin. This is encouraged but not required.
""".strip()


def citation_note(*, required: bool) -> str:
    return CITATION_REQUIRED_NOTE if required else CITATION_OPTIONAL_NOTE


# --------------------------------------------------------------------------- #
# Frontmatter template                                                         #
# --------------------------------------------------------------------------- #

def frontmatter_template(
    *,
    sources: list[str] | None = None,
    tags: list[str] | None = None,
    created: str = "",
    updated: str = "",
) -> str:
    """Return a YAML frontmatter block string."""
    from datetime import date

    today = date.today().isoformat()
    sources_str = "\n  - ".join(sources or [])
    sources_block = f"sources:\n  - {sources_str}" if sources else "sources: []"
    tags_str = ", ".join(tags or [])
    tags_block = f"tags: [{tags_str}]" if tags else "tags: []"

    return (
        "---\n"
        f"{sources_block}\n"
        f"{tags_block}\n"
        f"created: {created or today}\n"
        f"updated: {updated or today}\n"
        "---\n"
    )


# --------------------------------------------------------------------------- #
# Wiki-specific prompts                                                        #
# --------------------------------------------------------------------------- #

WIKI_INGEST_PROMPT = """
## Wiki ingest workflow

When ingesting a new source into the wiki:

1. Read the source carefully.
2. Write a **summary page** in `wiki/sources/<source-id>.md`.
3. Update `wiki/index.md` with a link and one-line description.
4. For each entity or concept mentioned:
   - Update or create the corresponding page in `wiki/entities/` or `wiki/concepts/`.
   - Add a cross-reference (`## See also`) in both the new page and related pages.
5. Note any contradictions with existing wiki pages explicitly.
6. Append an entry to `wiki/log.md`:
   ```
   ## [YYYY-MM-DD] ingest | <source title>
   Brief summary of what was added/updated.
   ```

A single source should typically touch 5–15 wiki pages.
""".strip()

WIKI_QUERY_PROMPT = """
## Wiki query workflow

When answering a question using the wiki:

1. Read `wiki/index.md` to identify relevant pages.
2. Use `memory_recall(query=..., layers=["wiki"])` for semantic search.
3. Read the most relevant pages.
4. Synthesise an answer with citations (`[page](path)`).
5. If the answer is a useful synthesis, save it back as a new wiki page.
""".strip()

WIKI_LINT_PROMPT = """
## Wiki health check

When asked to lint the wiki:

1. Identify orphan pages (no inbound links from index.md or other pages).
2. Find pages with broken links.
3. Look for duplicate or near-duplicate content.
4. Flag claims that may be contradicted by newer sources.
5. Identify important concepts mentioned but lacking their own page.
6. Report findings — do NOT auto-fix; let the human review and confirm.
""".strip()


# --------------------------------------------------------------------------- #
# Session Memory prompts                                                       #
# --------------------------------------------------------------------------- #

SESSION_EXTRACT_PROMPT_TEMPLATE = """
You are a memory extraction assistant. Your ONLY job is to write a structured
session summary to the file: `{session_path}`

Rules:
- Summarise the key decisions, facts, and action items from the conversation.
- Be concise — aim for 200–500 words.
- Use markdown headings: ## Key Decisions, ## Facts Learned, ## Open Questions, ## Action Items
- Do NOT reproduce conversation verbatim.
- You may ONLY use the `memory_update` tool on exactly this file: `{session_path}`
- Any other tool call will be rejected.

Write the summary now.
""".strip()


def session_extract_prompt(session_path: str | Path) -> str:
    return SESSION_EXTRACT_PROMPT_TEMPLATE.format(session_path=str(session_path))


# --------------------------------------------------------------------------- #
# Layer routing                                                                 #
# --------------------------------------------------------------------------- #

LAYER_ROUTING_NOTE = """
## Choosing a memory layer

- **Agent memory** — knowledge tied to your role/agent type (see its scope note).
- **Auto memory** — project-wide facts shared by every agent working on this project.
- Pick ONE layer per fact; never save the same fact to both.
""".strip()


# --------------------------------------------------------------------------- #
# Auto Memory compact system prompt                                             #
# --------------------------------------------------------------------------- #

AUTO_MEMORY_GUIDELINES = """
## Auto Memory guidelines

Auto Memory captures long-lived information that should persist across sessions:
- User or project preferences discovered during conversation
- External context not derivable from the codebase
- Cross-session collaboration constraints
- Non-obvious facts that would take significant effort to rediscover

Do NOT use Auto Memory for:
- Information already in source files
- Temporary task state
- One-off notes with no future relevance
""".strip()
