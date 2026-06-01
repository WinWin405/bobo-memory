# bobo-memory

[English](README.md) | **简体中文**

**面向 LLM Agent 的通用记忆中间件** — 基于文件、支持作用域隔离、自备 LLM（BYO LLM）。

`bobo-memory` 为任意 Python LLM 项目提供持久化、可查看、可治理的记忆系统，无需数据库、向量库或内置 LLM。全部记忆以 Markdown 文件形式存在，可在 Obsidian 中直接打开浏览。

**新手建议**：按 [使用教程](docs/TUTORIAL_zh.md) 从初始化到接入 Agent 逐步操作。

**跨项目集成（给编码 Agent / LLM）**：[集成规范](docs/INTEGRATION_zh.md) · [速查单页](docs/AGENT_BRIEF_zh.md) · [多用户 Web 教程](docs/AGENT_SAAS_TUTORIAL_zh.md)（登录态、记忆展示、磁盘治理）

---

## 核心理念

| 原则 | 实现方式 |
|---|---|
| 文件即记忆 | 所有记忆均为 `.bobo/memory/` 下的 `*.md` 文件 |
| 索引优先注入 | `MEMORY.md` 作为 prompt 入口（硬性上限 200 行 / 25 KB） |
| BYO LLM | 零 LLM 调用 — 模块只提供规则与工具，由 Agent 决定写入内容 |
| 四条工程护栏 | 每次写入：`policy → guard → atomic_write → audit` |
| 作用域隔离 | Agent 记忆支持 `user` / `project` / `local` 作用域 |
| 来源引用 | 记忆可追溯到 `raw/` 源文件 |
| 可分发快照 | Agent 记忆可打包为带版本号的资产 |

---

## 安装

```bash
pip install bobo-memory
```

可选扩展：

```bash
pip install "bobo-memory[viewer]"     # Web UI：fastapi + uvicorn
pip install "bobo-memory[embedding]"  # 向量召回：sentence-transformers
```

---

## 快速开始

### 1. 初始化

```bash
cd your-project/
bobo-memory init --agent-type my-agent --scope project
```

将创建 `.bobo/config.yaml` 及记忆目录结构。

### 2. 接入你的 Agent

```python
from bobo_memory import MemoryClient

mem = MemoryClient(
    project_root=".",
    agent_type="my-agent",
    scope="project",
)

# 将记忆上下文注入 system prompt
system_prompt = mem.build_system_prompt("You are a helpful assistant.")

# 将工具传给 LLM 调用
tools = mem.to_openai_tools()   # 或 to_anthropic_tools() / to_langchain_tools()

# 分发 LLM 返回的工具调用
result = mem.dispatch_tool_call(tool_name, arguments)
```

### 3. 接收外部信息流

```python
# 摄入文件（写入 raw/ + staging/ — 不调用 LLM）
mem.ingest(adapter="markdown", path="article.md")

# Agent 在下一轮处理
result = mem.dispatch_tool_call("ingest_next", {})
# → 返回原始内容 + 整合进 wiki/memory 的指引
```

---

## 记忆分层

| 层级 | 存储内容 | 作用域 |
|---|---|---|
| `agent` | Agent 类型相关的长期记忆 | user / project / local |
| `auto` | 项目级持久事实 | project |
| `wiki` | LLM 维护的知识库（实体、概念、来源） | project |
| `session` | 当前会话摘要，用于上下文压缩 | 临时 |
| `team` | Git 跟踪的团队共享知识 | 仓库 |

---

## 可用工具（暴露给 LLM）

| 工具 | 说明 |
|---|---|
| `memory_save` | 保存新记忆文件并更新索引 |
| `memory_update` | 更新已有记忆文件 |
| `memory_list` | 读取 MEMORY.md 索引 |
| `memory_read` | 读取指定记忆文件 |
| `memory_recall` | 跨记忆层 BM25 检索 → ContextPack |
| `memory_forget` | 软删除（移至 `.trash/`） |
| `memory_restore` | 从回收站恢复 |
| `memory_purge` | 永久删除（保留审计日志） |
| `wiki_link` | Wiki 页面间双向交叉引用 |
| `wiki_log` | 追加到 `wiki/log.md` 时间线 |
| `ingest_next` | 从 `staging/pending.json` 拉取下一条任务 |

---

## 策略配置（`.bobo/config.yaml`）

```yaml
policy:
  write_mode: direct          # "direct" 或 "proposal"
  layers:
    wiki:
      require_citation: true  # wiki 页面必须引用 raw 来源
      write_mode: proposal    # wiki 写入进入 proposals/ 待审核
    team:
      require_secret_scan: true
  forbidden_patterns:         # 每次写入全局生效
    - "api[_-]?key\\s*=\\s*['\"]?[A-Za-z0-9]{20,}"
    - "-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----"
  max_file_size_kb: 100
  trash:
    retention_days: 30
    allow_purge: true
```

---

## 会话记忆与上下文压缩

```python
# 判断是否该提取会话摘要
if mem.session.should_extract(messages):
    extract_prompt = mem.session.build_extract_prompt(messages)
    # 使用该 prompt  fork 一个沙箱子 Agent

# 上下文压缩（若存在 session 记忆则不调用 LLM）
if mem.compact.should_compact(messages, token_budget=180_000):
    summary = mem.compact.compact_with_session_memory(messages)
    keep = mem.compact.calculate_keep_index(messages)
    keep = mem.compact.adjust_index_to_preserve_invariants(messages, keep)
    new_messages = [
        messages[0],                                    # 保留 system
        mem.compact.create_compact_boundary(summary),   # 边界消息
        *messages[keep:],                               # 尾部
        *mem.compact.build_post_compact_attachments(tool_specs=mem.tool_specs()),
    ]
```

---

## 快照 — 可分发的 Agent 记忆

```bash
# 将当前 Agent 记忆导出为快照（随项目分发）
bobo-memory snapshot save --scope project

# 在新环境中应用快照
bobo-memory snapshot apply --scope project

# 查看快照同步状态
bobo-memory snapshot status
```

---

## 提案队列

当 `write_mode: proposal` 时，写入进入 `.bobo/proposals/` 待审核：

```bash
bobo-memory proposal list
bobo-memory proposal accept --id <id>
bobo-memory proposal reject --id <id>
```

---

## CLI 参考

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

## 磁盘布局

```
.bobo/
├── config.yaml
├── memory/
│   ├── auto/           MEMORY.md + *.md       (Auto Memory)
│   ├── agent/<type>/   project/ local/ user/  (Agent Memory)
│   ├── session/        <session_id>.md        (Session summaries)
│   ├── team/           MEMORY.md + *.md       (Team Memory)
│   └── wiki/           index.md  log.md  entities/  concepts/  sources/
├── raw/                <YYYY-MM-DD>/<source_id>.md   (不可变原文)
├── staging/            pending.json           (摄入队列)
├── proposals/          <layer>/<topic>.<uuid>.md
├── audit/              audit-<YYYY-MM-DD>.jsonl
└── snapshots/          <agent_type>/snapshot.json + *.md

~/.bobo/               (用户级 Agent 记忆，跨项目)
```

---

## 框架集成

适用于任何支持 function calling 的 LLM 框架：

```python
# OpenAI
tools = mem.to_openai_tools()

# Anthropic
tools = mem.to_anthropic_tools()

# LangChain
tools = mem.to_langchain_tools()

# 任意自定义框架
for spec in mem.tool_specs():
    print(spec.name, spec.parameters)
```

---

## 依赖要求

- Python 3.10+
- `pydantic >= 2.0`
- `pyyaml >= 6.0`
- `rank-bm25 >= 0.2.2`
- `filelock >= 3.12`
- `watchdog >= 4.0`

---

## 本地开发

```bash
git clone https://github.com/<your-org>/bobo-memory.git
cd bobo-memory
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[viewer]"
pytest bobo_memory/tests/
```

## 许可证

[MIT](LICENSE)
