# bobo-memory

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![GitHub](https://img.shields.io/badge/github-WinWin405%2Fbobo--memory-181717?logo=github)](https://github.com/WinWin405/bobo-memory)

[English](README.md) | [简体中文](README_zh.md)

**Universal memory middleware for LLM agents** — file-based, scope-aware, BYO LLM.

`bobo-memory` gives any Python LLM project a persistent, visible, governable memory system without requiring a database, a vector store, or an embedded LLM. The entire memory lives as Markdown files you can open in Obsidian.

**Getting started**: follow the [tutorial](docs/TUTORIAL_zh.md) (Chinese) from init through agent integration.

**For coding agents / integrators**: [integration guide](docs/INTEGRATION_zh.md) · [one-page brief](docs/AGENT_BRIEF_zh.md) · [multi-user Web / SaaS](docs/AGENT_SAAS_TUTORIAL_zh.md)

---

## Core philosophy

| Principle | Implementation |
|---|---|
| Files are memory | All memories are `*.md` files in `.bobo/memory/` |
| Index-first prompting | `MEMORY.md` acts as the prompt entry-point (hard-capped at 200 lines / 25 KB) |
| BYO LLM | Zero LLM calls — the module exposes rules + tools, the agent decides what to write |
| Four engineering rails | Every write: `policy → guard → atomic_write → audit` |
| Scope isolation | `user` / `project` / `local` scopes for agent memory |
| Source citation | Memories trace back to `raw/` source files |
| Distributable snapshots | Agent memory packaged as a versioned asset |

---

## Installation

```bash
pip install bobo-memory
```

Optional extras:

```bash
pip install "bobo-memory[viewer]"     # web UI: fastapi + uvicorn
pip install "bobo-memory[embedding]"  # vector recall: sentence-transformers
```

---

## Quick start

### 1. Initialise

```bash
cd your-project/
bobo-memory init --agent-type my-agent --scope project
```

This creates `.bobo/config.yaml` and the memory directory structure.

### 2. Integrate with your agent

```python
from bobo_memory import MemoryClient

mem = MemoryClient(
    project_root=".",
    agent_type="my-agent",
    scope="project",
)

# Inject memory context into system prompt
system_prompt = mem.build_system_prompt("You are a helpful assistant.")

# Pass tools to your LLM call
tools = mem.to_openai_tools()   # or to_anthropic_tools() / to_langchain_tools()

# Dispatch tool calls coming back from the LLM
result = mem.dispatch_tool_call(tool_name, arguments)
```

### 3. Receive external information streams

```python
# Ingest a file (lands in raw/ + staging/ — no LLM called)
mem.ingest(adapter="markdown", path="article.md")

# Agent picks it up next turn
result = mem.dispatch_tool_call("ingest_next", {})
# → returns raw content + instruction to integrate into wiki/memory
```

---

## Memory layers

| Layer | What it stores | Scope |
|---|---|---|
| `agent` | Agent-type specific long-term memory | user / project / local |
| `auto` | Project-wide persistent facts | project |
| `wiki` | LLM-maintained knowledge base (entities, concepts, sources) | project |
| `session` | Current-session summary for context compaction | ephemeral |
| `team` | Git-tracked shared team knowledge | repo |

---

## Available tools (exposed to the LLM)

| Tool | Description |
|---|---|
| `memory_save` | Save a new memory file + update index |
| `memory_update` | Update an existing memory file |
| `memory_list` | Read the MEMORY.md index |
| `memory_read` | Read a specific memory file |
| `memory_recall` | BM25 search across memory layers → ContextPack |
| `memory_forget` | Soft-delete (move to `.trash/`) |
| `memory_restore` | Restore from trash |
| `memory_purge` | Permanent delete (audit log kept) |
| `wiki_link` | Bidirectional cross-reference between wiki pages |
| `wiki_log` | Append to `wiki/log.md` timeline |
| `ingest_next` | Pull next task from `staging/pending.json` |

---

## Policy configuration (`.bobo/config.yaml`)

```yaml
policy:
  write_mode: direct          # "direct" or "proposal"
  layers:
    wiki:
      require_citation: true  # wiki pages must cite raw sources
      write_mode: proposal    # wiki writes go to proposals/ for review
    team:
      require_secret_scan: true
  forbidden_patterns:         # applied globally on every write
    - "api[_-]?key\\s*=\\s*['\"]?[A-Za-z0-9]{20,}"
    - "-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----"
  max_file_size_kb: 100
  trash:
    retention_days: 30
    allow_purge: true
```

---

## Session memory & context compaction

```python
# Check if it's time to extract a session summary
if mem.session.should_extract(messages):
    extract_prompt = mem.session.build_extract_prompt(messages)
    # Fork a sandboxed subagent with this prompt

# Context compaction (no LLM call if session memory exists)
if mem.compact.should_compact(messages, token_budget=180_000):
    summary = mem.compact.compact_with_session_memory(messages)
    keep = mem.compact.calculate_keep_index(messages)
    keep = mem.compact.adjust_index_to_preserve_invariants(messages, keep)
    new_messages = [
        messages[0],                                    # keep system
        mem.compact.create_compact_boundary(summary),   # boundary msg
        *messages[keep:],                               # tail
        *mem.compact.build_post_compact_attachments(tool_specs=mem.tool_specs()),
    ]
```

---

## Snapshots — distributable agent memory

```bash
# Export current agent memory as a snapshot (ship with your project)
bobo-memory snapshot save --scope project

# Apply a snapshot to a fresh environment
bobo-memory snapshot apply --scope project

# Check snapshot sync status
bobo-memory snapshot status
```

---

## Proposal queue

When `write_mode: proposal`, writes go to `.bobo/proposals/` for review:

```bash
bobo-memory proposal list
bobo-memory proposal accept --id <id>
bobo-memory proposal reject --id <id>
```

---

## CLI reference

```bash
bobo-memory init      [--agent-type NAME] [--scope SCOPE]
bobo-memory status
bobo-memory audit     [--date YYYY-MM-DD] [--limit N]
bobo-memory lint
bobo-memory ingest    --file PATH [--adapter markdown]
bobo-memory proposal  list / accept --id X / reject --id X
bobo-memory snapshot  save / apply / status
bobo-memory serve     [--port 8765]
```

---

## Disk layout

```
.bobo/
├── config.yaml
├── memory/
│   ├── auto/           MEMORY.md + *.md       (Auto Memory)
│   ├── agent/<type>/   project/ local/ user/  (Agent Memory)
│   ├── session/        <session_id>.md        (Session summaries)
│   ├── team/           MEMORY.md + *.md       (Team Memory)
│   └── wiki/           index.md  log.md  entities/  concepts/  sources/
├── raw/                <YYYY-MM-DD>/<source_id>.md   (immutable originals)
├── staging/            pending.json           (ingest queue)
├── proposals/          <layer>/<topic>.<uuid>.md
├── audit/              audit-<YYYY-MM-DD>.jsonl
└── snapshots/          <agent_type>/snapshot.json + *.md

~/.bobo/               (user-scoped agent memory, cross-project)
```

---

## Framework integrations

Works with any LLM framework that supports function calling:

```python
# OpenAI
tools = mem.to_openai_tools()

# Anthropic
tools = mem.to_anthropic_tools()

# LangChain
tools = mem.to_langchain_tools()

# Any custom framework
for spec in mem.tool_specs():
    print(spec.name, spec.parameters)
```

---

## Requirements

- Python 3.10+
- `pydantic >= 2.0`
- `pyyaml >= 6.0`
- `rank-bm25 >= 0.2.2`
- `filelock >= 3.12`
- `watchdog >= 4.0`

---

## Development

```bash
git clone https://github.com/WinWin405/bobo-memory.git
cd bobo-memory
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[viewer]"
pytest bobo_memory/tests/
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and pull requests are welcome on [GitHub](https://github.com/WinWin405/bobo-memory/issues).

## License

[MIT](LICENSE) — see [LICENSE](LICENSE) for the full text.
