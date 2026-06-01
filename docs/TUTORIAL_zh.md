# bobo-memory 使用教程

本教程从零开始，带你完成：**初始化 → 接入 Agent → 读写记忆 → 摄入外部资料 → 治理与分发**。

前置阅读：[README_zh.md](../README_zh.md)

跨项目 Agent 集成请直接用：[INTEGRATION_zh.md](INTEGRATION_zh.md) · [AGENT_BRIEF_zh.md](AGENT_BRIEF_zh.md)

---

## 1. 它解决什么问题？

普通 ChatBot 对话结束后，上下文就丢了。`bobo-memory` 把「该记住的事」写成项目里的 Markdown 文件：

- 人类可用 Obsidian / 编辑器直接查看、编辑
- Agent 通过 **工具调用** 自己决定写什么（库本身不调用 LLM）
- 每次写入都经过 **策略检查 → 路径护栏 → 原子写入 → 审计日志**

典型用法：研究助手、代码 Agent、团队知识库、长对话压缩前的会话摘要。

---

## 2. 环境准备

```bash
# 安装
pip install bobo-memory

# 进入你的项目目录
cd your-project/

# 初始化（创建 .bobo/ 目录与 config）
bobo-memory init --agent-type my-agent --scope project
```

初始化后会生成：

| 路径 | 作用 |
|------|------|
| `.bobo/config.yaml` | Agent 类型、作用域、启用的记忆层、写入策略 |
| `.bobo/memory/` | 各层记忆的 Markdown 文件 |
| `.bobo/audit/` | 每次读写的 JSONL 审计记录 |

查看状态：

```bash
bobo-memory status
```

---

## 3. 五分钟：不用 LLM 也能试

仓库自带示例，**不调用 OpenAI**，只演示保存与召回：

```bash
cd bobo_memory   # 项目根目录
python examples/openai_demo.py
```

脚本会：

1. 创建 `MemoryClient`
2. 调用 `memory_save` 写入一条 auto 层记忆（如 Q3 预算）
3. 用 BM25 做 `recall` 检索
4. 打印 `status()`

完成后打开 `.bobo/memory/auto/`，能看到 `q3-budget.md` 和更新后的 `MEMORY.md` 索引。

---

## 4. 接入你的 Agent（核心三步）

无论用 OpenAI、Anthropic 还是 LangChain，集成模式相同：

```python
from bobo_memory import MemoryClient

mem = MemoryClient(
    project_root=".",           # 含 .bobo/ 的项目根
    agent_type="my-agent",      # 与 init 时一致
    scope="project",            # user | project | local
)

# ① 拼接 system prompt（见下方「谁写哪段提示词」）
system_prompt = mem.build_system_prompt(
    "你是一个有帮助的研究助手。"   # 仅写 Agent 角色/任务；记忆用法由库自动追加
)

# ② 把工具定义交给 LLM
tools = mem.to_openai_tools()        # OpenAI
# tools = mem.to_anthropic_tools()   # Anthropic
# tools = mem.to_langchain_tools()     # LangChain

# ③ LLM 返回 tool_call 时，统一走 dispatch
result = mem.dispatch_tool_call(tool_name, arguments)
```

### 谁写哪段提示词？

| 内容 | 谁提供 |
|------|--------|
| Agent 角色、语气、业务规则 | **你** — `build_system_prompt()` 的第一个参数（`base_prompt`） |
| 记忆类型、何时保存/不保存、如何写 MEMORY.md、如何 recall | **库自动** — 各层的 `build_memory_prompt()`（见 `bobo_memory/core/memdir.py`） |
| 作用域说明（user/project/local）、引用要求 | **库自动** — `scope_note` / `citation_note` |
| Auto 层额外约定 | **库自动** — `AUTO_MEMORY_GUIDELINES` |
| 工具何时调用、参数含义 | **库自动** — `to_openai_tools()` 里各工具的 `description` |

因此 **不必** 自己写「请用 memory_save…」这类记忆教学文案；传空字符串也可以，只要后面把 `tools` 交给模型：

```python
system_prompt = mem.build_system_prompt()  # 只有记忆片段，无自定义角色
system_prompt = mem.build_system_prompt("你是代码审查助手。")  # 常见写法
```

若 Agent 经常「说了记住却不调工具」，可在 `base_prompt` 或 `extra_guidelines` 里**加一句**业务侧强调（可选，非记忆模块本体）：

```python
mem.build_system_prompt(
    "你是研究助手。",
    extra_guidelines=["用户明确要求记住时，必须调用 memory_save。"],
)
```

### 4.1 与 OpenAI 的完整对话循环（示意）

```python
import json
from openai import OpenAI
from bobo_memory import MemoryClient

mem = MemoryClient(project_root=".", agent_type="my-agent", scope="project")
client = OpenAI()

messages = [
    {"role": "system", "content": mem.build_system_prompt("你是研究助手。")},
    {"role": "user", "content": "记住：Q3 预算上限 5 万美元。"},
]

while True:
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        tools=mem.to_openai_tools(),
    )
    msg = resp.choices[0].message
    messages.append(msg.model_dump(exclude_none=True))

    if not msg.tool_calls:
        print(msg.content)
        break

    for tc in msg.tool_calls:
        args = json.loads(tc.function.arguments)
        out = mem.dispatch_tool_call(tc.function.name, args)
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps(out, ensure_ascii=False),
        })
```

要点：**bobo-memory 只负责工具执行与落盘**；选模型、多轮对话、流式输出都由你的应用负责。

---

## 5. 记忆分层：写什么、写哪里？

| 层级 | 适合存什么 | 作用域 |
|------|------------|--------|
| `auto` | 项目级事实（预算、技术栈、约定） | 当前项目 |
| `agent` | 某类 Agent 的长期偏好与经验 | user / project / local |
| `wiki` | 结构化知识（实体、概念、来源引用） | 当前项目 |
| `session` | 当前长对话的摘要（给压缩用） | 临时 |
| `team` | 团队共享、可进 Git 的知识 | 仓库 |

**实用建议：**

- 用户说「记住这个」→ 多数情况用 `layer: "auto"`
- 只跟「这一类 Agent」有关的习惯 → `layer: "agent"`
- 从文档整理出的百科条目 → `layer: "wiki"`，并填 `sources`

`scope` 含义：

- `project`：记忆在项目 `.bobo/memory/agent/<type>/project/`
- `local`：本机项目内私有
- `user`：跨项目，落在 `~/.bobo/`（用户级）

---

## 6. 常用工具与参数

Agent 通过 function calling 调用以下工具（也可在代码里直接 `dispatch_tool_call`）。

### 6.1 保存与更新

```python
# 新建
mem.dispatch_tool_call("memory_save", {
    "layer": "auto",
    "topic": "q3-budget",           # 文件名 slug → q3-budget.md
    "content": "Q3 总预算上限 5 万美元。",
    "summary": "Q3 预算 5 万",      # 写入 MEMORY.md 索引的一行摘要
    "tags": ["budget", "2026"],
    "sources": [],                  # wiki 层建议填 raw 来源 ID
})

# 更新已有文件（路径相对项目根）
mem.dispatch_tool_call("memory_update", {
    "file": ".bobo/memory/auto/q3-budget.md",
    "content": "Q3 总预算上调至 6 万美元。",
    "summary": "Q3 预算 6 万",
})
```

### 6.2 浏览与检索

```python
# 读索引
mem.dispatch_tool_call("memory_list", {"layer": "auto"})

# 读单文件
mem.dispatch_tool_call("memory_read", {
    "file": ".bobo/memory/auto/q3-budget.md",
})

# BM25 召回（也可直接用 mem.recall）
mem.dispatch_tool_call("memory_recall", {
    "query": "预算上限",
    "k": 5,
    "layers": ["auto", "agent"],
})

# 或 Python API
pack = mem.recall("预算", layers=["auto"])
for f in pack.files:
    print(f.filename, f.score)
```

### 6.3 删除生命周期

```python
mem.dispatch_tool_call("memory_forget", {
    "file": ".bobo/memory/auto/q3-budget.md",
    "reason": "已过期",
})   # → 移到 .trash/

mem.dispatch_tool_call("memory_restore", {"trash_file": "..."})
mem.dispatch_tool_call("memory_purge", {"file": "...", "confirm": True})
```

---

## 7. 摄入外部资料（不经过 LLM 落盘）

适合：用户上传文章、剪藏网页、日志文件等。

```python
# ① 摄入：写入 raw/ + 进入 staging 队列（不调用 LLM）
mem.ingest(adapter="markdown", path="article.md")

# ② Agent 下一轮用工具拉任务
result = mem.dispatch_tool_call("ingest_next", {})
# 返回原文 + 指引：由 Agent 自行整理进 wiki / memory
```

CLI 等价：

```bash
bobo-memory ingest --file article.md --adapter markdown
```

可选：监听目录自动摄入

```python
mem.watch_directory("./inbox", adapter="markdown")
```

---

## 8. 策略与提案模式（团队治理）

编辑 `.bobo/config.yaml`：

```yaml
policy:
  write_mode: direct          # 全局：direct 直接写 | proposal 进待审
  layers:
    wiki:
      require_citation: true
      write_mode: proposal    # 仅 wiki 层走提案
  forbidden_patterns:           # 拦截 API Key、私钥等
    - "api[_-]?key\\s*=\\s*..."
  max_file_size_kb: 100
```

`write_mode: proposal` 时，写入会落到 `.bobo/proposals/`，需人工或流程批准：

```bash
bobo-memory proposal list
bobo-memory proposal accept --id <uuid>
bobo-memory proposal reject --id <id>
```

审计谁在何时写了什么：

```bash
bobo-memory audit --date 2026-05-28 --limit 20
```

健康检查（wiki 断链等）：

```bash
bobo-memory lint
```

---

## 9. 长对话：会话摘要与压缩

当消息列表接近 token 上限时：

```python
# 是否该提取会话摘要（由你的子 Agent 执行 extract_prompt）
if mem.session.should_extract(messages):
    prompt = mem.session.build_extract_prompt(messages)
    # 用 prompt 调 LLM，结果写入 session 层

# 压缩上下文（有 session 摘要时可少调一次 LLM）
if mem.compact.should_compact(messages, token_budget=180_000):
    summary = mem.compact.compact_with_session_memory(messages)
    keep = mem.compact.calculate_keep_index(messages)
    keep = mem.compact.adjust_index_to_preserve_invariants(messages, keep)
    new_messages = [
        messages[0],
        mem.compact.create_compact_boundary(summary),
        *messages[keep:],
        *mem.compact.build_post_compact_attachments(tool_specs=mem.tool_specs()),
    ]
```

---

## 10. 快照：把 Agent 记忆随项目分发

```bash
# 导出当前 agent 记忆
bobo-memory snapshot save --scope project

# 新环境应用
bobo-memory snapshot apply --scope project

bobo-memory snapshot status
```

适合：预制「研究员 Agent」记忆包、CI 部署时恢复知识库。

---

## 11. 用 Obsidian 管理记忆

1. 在 Obsidian 中把 vault 指到项目根，或只打开 `.bobo/memory/`
2. `MEMORY.md` 是各层入口索引（有行数/体积上限，避免 prompt 爆炸）
3. 单条记忆是带 frontmatter 的 `.md`，例如：

```markdown
---
sources: []
tags: [budget, Q3]
created: 2026-05-28
updated: 2026-05-28
---

正文内容……
```

人类改文件后，Agent 下次 `memory_read` / `memory_recall` 会读到最新内容。

---

## 12. Web 查看器（可选）

```bash
pip install "bobo-memory[viewer]"
bobo-memory serve --port 8765
```

浏览器打开本地 UI 浏览记忆与审计（具体页面以实现为准）。

---

## 13. 常见问题

**Q：`MemoryClient` 报找不到 config？**  
先在项目根执行 `bobo-memory init`，或显式传 `config_path`。

**Q：写入被拒绝？**  
检查 `forbidden_patterns`（敏感信息）、`max_file_size_kb`、wiki 是否要求 `sources` / `proposal` 模式。

**Q：Agent 从不调用记忆工具？**  
在 `build_system_prompt` 的 `extra_guidelines` 里强调「重要事实必须 memory_save」；并确保 `tools` 已传给 LLM。

**Q：`agent_type` / `scope` 不一致？**  
`init` 与 `MemoryClient(...)` 参数需一致，否则读写目录对不上。

**Q：和向量数据库的关系？**  
默认 BM25 全文检索；安装 `[embedding]` 后可扩展向量召回（见 README）。

---

## 14. 推荐学习路径

| 步骤 | 动作 |
|------|------|
| 1 | `bobo-memory init` + `status` |
| 2 | 跑 `examples/openai_demo.py`，用 Obsidian 看 `.bobo/memory/` |
| 3 | 把 `build_system_prompt` + `to_openai_tools` + `dispatch_tool_call` 接到你的 Agent |
| 4 | 试 `ingest` + `ingest_next` 处理一篇 markdown |
| 5 | 按需配置 `proposal`、快照、会话压缩 |

更多 API 细节见源码 `bobo_memory/client.py` 与 `bobo_memory/tools/specs.py`。
