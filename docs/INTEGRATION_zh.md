# bobo-memory 集成规范（Agent / LLM 专用）

> **用途**：交给其他项目的编码 Agent 或 LLM，按本文完成集成。  
> **原则**：bobo-memory **不调用 LLM**；你的 Agent 负责对话，bobo-memory 负责 **system prompt 片段 + 工具定义 + 工具执行落盘**。

人类教程：[TUTORIAL_zh.md](TUTORIAL_zh.md) · 产品说明：[README_zh.md](../README_zh.md)  
多用户 Web 集成（懒初始化、记忆 API、janitor）：[AGENT_SAAS_TUTORIAL_zh.md](AGENT_SAAS_TUTORIAL_zh.md)

---

## 0. 集成检查清单（按顺序执行）

```
[ ] pip install bobo-memory
[ ] 在目标项目根执行: bobo-memory init --agent-type <TYPE> --scope project|user|local
[ ] 确认存在 .bobo/config.yaml
[ ] 代码中实例化 MemoryClient(project_root, agent_type, scope) — 与 init 参数一致
[ ] 每轮 LLM 请求: system = client.build_system_prompt(<你的角色 prompt>)
[ ] 每轮 LLM 请求: tools = client.to_openai_tools() | to_anthropic_tools() | to_langchain_tools()
[ ] 收到 tool_calls 后: result = client.dispatch_tool_call(name, json.loads(arguments))
[ ] 将 result 序列化为 JSON 字符串，作为 tool 角色消息回传给 LLM
[ ] （可选）应用侧 ingest: client.ingest(adapter="markdown", path="...")
```

---

## 1. 架构（一行理解）

```
你的 Agent 应用                    bobo-memory
─────────────────                 ─────────────
LLM API 调用          ──tools──►   ToolSpec 列表
system prompt         ◄─拼接──    build_system_prompt() + MEMORY.md 索引
tool_call 参数        ──dispatch►  policy → guard → atomic_write → audit
                      ◄─JSON──    {ok: true|false, ...}
磁盘                  ◄──────────   .bobo/memory/**/*.md
```

**你不需要写**：记忆类型说明、如何维护 MEMORY.md、recall 流程、scope 语义 — 已由库注入 `build_system_prompt()`。

**你需要写**：Agent 角色/任务（`base_prompt` 字符串）。

---

## 2. 最小集成代码（复制即用）

```python
import json
from bobo_memory import MemoryClient

# 单例：整个进程复用同一 client（agent_type/scope 与 bobo-memory init 一致）
mem = MemoryClient(
    project_root="/path/to/your-project",  # 必须含 .bobo/
    agent_type="your-agent",
    scope="project",
)

def prepare_llm_request(user_messages: list[dict]) -> tuple[str, list[dict], list[dict]]:
    """返回 (system_prompt, tools, messages) 供你的 LLM SDK 使用。"""
    system = mem.build_system_prompt("你是 <业务描述> 助手。")
    tools = mem.to_openai_tools()
    messages = [{"role": "system", "content": system}, *user_messages]
    return system, tools, messages

def handle_tool_calls(tool_calls: list) -> list[dict]:
    """将 OpenAI 风格 tool_calls 转为 tool 结果消息。"""
    out = []
    for tc in tool_calls:
        args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
        result = mem.dispatch_tool_call(tc.function.name, args)
        out.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps(result, ensure_ascii=False),
        })
    return out
```

### OpenAI 多轮循环骨架

```python
messages = prepare_llm_request([{"role": "user", "content": "..."}])[2]

while True:
    resp = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        tools=mem.to_openai_tools(),
    )
    msg = resp.choices[0].message
    messages.append(msg.model_dump(exclude_none=True))

    if not msg.tool_calls:
        break  # msg.content 为最终回复

    messages.extend(handle_tool_calls(msg.tool_calls))
```

### Anthropic

```python
tools = mem.to_anthropic_tools()
# tool_use block → mem.dispatch_tool_call(name, input) → tool_result content = json.dumps(result)
```

### LangChain

```python
lc_tools = mem.to_langchain_tools()  # 或自行绑定 mem.dispatch_tool_call
```

---

## 3. MemoryClient API（集成侧必用）

| 方法 | 何时调用 | 返回值 |
|------|----------|--------|
| `MemoryClient(project_root, agent_type, scope, ...)` | 进程启动 | 客户端实例 |
| `build_system_prompt(base_prompt="", include=None, extra_guidelines=None)` | **每次** LLM 请求前（或会话开始时） | `str`，拼入 system |
| `to_openai_tools()` / `to_anthropic_tools()` / `to_langchain_tools()` | 传给 LLM 的 tools 参数 | 框架原生格式 |
| `dispatch_tool_call(name, arguments, actor="agent")` | LLM 返回每个 tool_call | `dict`，见 §5 |
| `tool_specs()` | 自定义框架、调试 | `list[ToolSpec]` |
| `recall(query, k=5, layers=None, token_budget=8000)` | 应用侧预检索（不经 LLM） | `ContextPack` |
| `ingest(adapter, path=..., payload=...)` | 用户上传文件，**不调用 LLM** | `dict` |
| `status()` | 健康检查 / 调试 | `dict` |

### `build_system_prompt` 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `base_prompt` | `""` | **仅** Agent 角色/业务规则 |
| `include` | `["agent_memory", "auto_memory"]` | 可加 `"wiki_index"` |
| `extra_guidelines` | `None` | 额外字符串列表，追加在末尾 |

### 环境变量覆盖（可选）

| 变量 | 作用 |
|------|------|
| `BOBO_AGENT_TYPE` | 覆盖 agent_type |
| `BOBO_SCOPE` | 覆盖 scope |
| `BOBO_DISABLE_AGENT_MEMORY` | `1` 禁用 agent 层 prompt |
| `BOBO_DISABLE_AUTO_MEMORY` | `1` 禁用 auto 层 prompt |

---

## 4. 记忆分层 — 工具 `layer` 选型

| `layer` | 路径（project scope 示例） | 何时用 |
|---------|---------------------------|--------|
| `auto` | `.bobo/memory/auto/*.md` | 项目级长期事实、用户偏好、跨会话约束 |
| `agent` | `.bobo/memory/agent/<type>/project/*.md` | 绑定 agent 类型的长期经验 |
| `wiki` | `.bobo/memory/wiki/**` | 结构化知识库、实体、需引用的来源 |
| `session` | `.bobo/memory/session/*.md` | 长对话摘要（常由子 Agent 写入） |
| `team` | `.bobo/memory/team/*.md` | 团队共享、可进 Git |

| `scope`（init + Client 一致） | 含义 |
|-------------------------------|------|
| `project` | 项目内 `.bobo/memory/agent/<type>/project/` |
| `user` | 用户级 `~/.bobo/memory/agent/<type>/user/`，跨项目 |
| `local` | 本机环境私有 |

**默认建议**：用户说「记住」→ `layer: "auto"`；Agent 专属习惯 → `layer: "agent"`。

---

## 5. 工具响应契约（dispatch 统一格式）

所有工具返回 **JSON 可序列化 dict**：

### 成功

```json
{"ok": true, "...": "工具特定字段"}
```

### 失败

```json
{"ok": false, "error": "人类可读错误信息"}
```

### 提案模式（write_mode=proposal 时 memory_save 可能被重定向）

```json
{
  "ok": true,
  "proposal": true,
  "proposal_file": ".bobo/proposals/wiki/topic.abc12345.md",
  "proposal_id": "abc12345",
  "message": "Write redirected to proposal queue..."
}
```

集成时：**始终把完整 dict  JSON 字符串回传给 LLM**，不要只传 `error` 文本。

---

## 6. 工具目录（LLM function calling）

共 **11** 个工具。名称与 schema 以运行时 `mem.tool_specs()` 为准；以下为集成参考。

### 6.1 `memory_save`

**用途**：新建记忆文件 + 更新该层 `MEMORY.md` 索引一行。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `layer` | `agent` \| `auto` \| `wiki` | ✓ | 目标层 |
| `topic` | string | ✓ | 文件 slug，如 `q3-budget` → `q3-budget.md` |
| `content` | string | ✓ | Markdown 正文（库会自动加 YAML frontmatter） |
| `summary` | string | | 索引一行摘要；缺省用 topic 推导 |
| `tags` | string[] | | frontmatter tags |
| `sources` | string[] | | raw 路径，wiki 层常必填（看 policy） |

**成功**：`{"ok": true, "file": ".bobo/memory/auto/q3-budget.md", "layer": "auto"}`

### 6.2 `memory_update`

| 参数 | 必填 | 说明 |
|------|------|------|
| `file` | ✓ | 相对项目根路径 |
| `content` | ✓ | 完整新内容（含 frontmatter 若需保留） |
| `summary` | | 若提供则更新 MEMORY.md 对应行 |

### 6.3 `memory_list`

| 参数 | 必填 |
|------|------|
| `layer` | ✓，`agent` \| `auto` \| `wiki` \| `session` \| `team` |

**成功**：含 `index` 字段（MEMORY.md 内容）。

### 6.4 `memory_read`

| 参数 | 必填 |
|------|------|
| `file` | ✓，相对路径 |

**成功**：`content` 为文件全文。

### 6.5 `memory_recall`

| 参数 | 必填 | 默认 |
|------|------|------|
| `query` | ✓ | |
| `k` | | 5 |
| `layers` | | 全部 enabled_layers |
| `token_budget` | | 8000 |

**成功**：`{"ok": true, "pack": { "query", "files": [{filename, path, summary, score, content, ...}], "formatted_markdown", ... }}`

应用侧也可直接：`pack = mem.recall("query")` → `pack.format_for_prompt()`。

### 6.6 `memory_forget` / `memory_restore` / `memory_purge`

| 工具 | 关键参数 |
|------|----------|
| `memory_forget` | `file`, 可选 `reason` → 移入 `.trash/` |
| `memory_restore` | `trash_file` |
| `memory_purge` | `file`, `confirm: true`（永久删除） |

### 6.7 `wiki_link`

`from_topic`, `to_topic`, 可选 `kind`（如 `related`, `cites`）。

### 6.8 `wiki_log`

`kind`, `title`, `summary` → 追加 `wiki/log.md`。

### 6.9 `ingest_next`

无参数。从 `staging/pending.json` 取下一条摄入任务。

**成功示例**：

```json
{
  "ok": true,
  "task": {"source_id": "...", "raw_path": ".bobo/raw/...", "title": "..."},
  "raw_content": "...",
  "instruction": "Please integrate this source into memory/wiki..."
}
```

无任务：`{"ok": true, "task": null, "message": "No pending ingest tasks."}`

---

## 7. 应用侧摄入（不经 LLM）

```python
mem.ingest(adapter="markdown", path="/abs/or/rel/article.md")
# 之后由 LLM 在对话中调用 ingest_next，或你主动 dispatch_tool_call("ingest_next", {})
```

适配器：`markdown`（及 stream 模块注册的其他 adapter）。

---

## 8. 策略与失败（集成必处理）

写入前自动检查（`policy` in `.bobo/config.yaml`）：

- 敏感信息正则 `forbidden_patterns` → `ok: false`
- `max_file_size_kb`
- 层级别 `require_citation` / `write_mode: proposal`

| 现象 | 处理 |
|------|------|
| `ok: false` | 将 `error` 原样给 LLM，让其修正参数或告知用户 |
| `proposal: true` | 告知用户待审核，或走 `bobo-memory proposal accept` |
| 路径越界 | guard 拒绝，检查 `file` 是否在 `.bobo/memory/` 下 |

---

## 9. 目录约定（路径不要猜错）

```
<project_root>/.bobo/
  config.yaml
  memory/auto/          MEMORY.md + *.md
  memory/agent/<type>/{project|local|user}/
  memory/wiki/
  memory/session/
  raw/<YYYY-MM-DD>/<id>.md
  staging/pending.json
  proposals/
  audit/audit-<date>.jsonl
```

工具参数 `file` **一律相对 `project_root`**，例如 `.bobo/memory/auto/foo.md`。

---

## 10. 反模式（集成时不要做）

| ❌ 不要 | ✅ 应该 |
|--------|--------|
| 自己拼记忆教学 prompt | `build_system_prompt()` |
| 直接写 `.bobo/memory/*.md` 文件（绕过工具） | `dispatch_tool_call("memory_save", ...)` |
| 只把工具 `error` 字符串给模型 | 完整 JSON `{ok, error, ...}` |
| `agent_type`/`scope` 与 init 不一致 | 统一配置 |
| 每个 tool_call 新建 MemoryClient | 进程级单例 |
| 在 bobo-memory 内调 LLM 做 save | 由你的 Agent LLM 决定何时调工具 |

---

## 11. 与现有 Agent 框架对接模式

### 模式 A：原生 function calling（推荐）

LLM 自带 tools → `dispatch_tool_call` 执行 → tool 消息回传。

### 模式 B：框架 Tool 节点

将 `mem.to_langchain_tools()` 注册到图；handler 内部仍应落到 `dispatch_tool_call` 以保证 audit/policy。

### 模式 C：无 function calling

1. `system = mem.build_system_prompt(...)` 已含 recall 指引  
2. 应用侧 `pack = mem.recall(user_query)`，把 `pack.format_for_prompt()` 拼进 user 消息  
3. 让用户确认后，应用侧代码调用 `dispatch_tool_call("memory_save", {...})`（不经 LLM 工具）

---

## 12. 验证集成是否成功

```bash
cd <your-project>
bobo-memory status
python -c "
from bobo_memory import MemoryClient
import json
m = MemoryClient(project_root='.', agent_type='your-agent', scope='project')
r = m.dispatch_tool_call('memory_save', {
    'layer': 'auto', 'topic': 'integration-test',
    'content': 'test', 'summary': 'integration test'
})
print(json.dumps(r, indent=2))
assert r.get('ok'), r
"
```

检查 `.bobo/memory/auto/integration-test.md` 与 `MEMORY.md` 是否新增索引行。

---

## 13. 给「集成 Agent」的执行指令（可复制到任务描述）

```text
任务：在本项目中集成 bobo-memory。

约束：
1. 依赖 pip install bobo-memory；项目根 bobo-memory init --agent-type <与代码一致> --scope project
2. 使用 MemoryClient；禁止手写 .bobo/memory 下的 md（除调试）
3. system prompt = client.build_system_prompt(<仅业务角色>)
4. LLM tools = client.to_openai_tools()（或 anthropic/langchain 等价）
5. 每个 tool_call → client.dispatch_tool_call(name, args) → JSON 字符串作为 tool 结果
6. 用户要求记住 → 优先 memory_save layer=auto；检索 → memory_recall 或 memory_list/memory_read
7. 文件上传 → client.ingest()；处理队列 → ingest_next
8. 处理 ok:false 与 proposal:true

验收：memory_save 后 .bobo/memory 有文件且 MEMORY.md 有索引；多轮对话后 recall 能命中。

参考：docs/INTEGRATION_zh.md、docs/AGENT_BRIEF_zh.md
```

---

## 14. 版本与依赖

- Python ≥ 3.10
- 包：`bobo-memory`（`from bobo_memory import MemoryClient`）
- 可选：`bobo-memory[viewer]`、`bobo-memory[embedding]`
