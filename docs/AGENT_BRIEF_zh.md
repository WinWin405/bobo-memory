# bobo-memory 集成速查（粘贴给 Agent / LLM）

> 完整规范见 [INTEGRATION_zh.md](INTEGRATION_zh.md)  
> **多用户 Web / SQL Agent 部署**（初始化、记忆展示、磁盘治理）：[AGENT_SAAS_TUTORIAL_zh.md](AGENT_SAAS_TUTORIAL_zh.md)

## 安装与初始化

```bash
pip install bobo-memory
cd <project_root> && bobo-memory init --agent-type <TYPE> --scope project
```

## 五行集成

```python
from bobo_memory import MemoryClient
import json

mem = MemoryClient(project_root="<ROOT>", agent_type="<TYPE>", scope="project")
system = mem.build_system_prompt("<你的 Agent 角色，勿写记忆教程>")
tools = mem.to_openai_tools()
# tool_call 时: json.dumps(mem.dispatch_tool_call(name, args), ensure_ascii=False)
```

## 数据流

```
build_system_prompt(角色) → LLM system
to_openai_tools()         → LLM tools
dispatch_tool_call()      → 写 .bobo/memory/*.md + 审计
```

库已自动注入：记忆类型、保存/召回协议、MEMORY.md 索引、scope 说明。工具 description 含用法。

## 工具一览

| name | 用途 | 必填参数 |
|------|------|----------|
| `memory_save` | 新建记忆 | `layer`, `topic`, `content` |
| `memory_update` | 更新记忆 | `file`, `content` |
| `memory_list` | 读索引 | `layer` |
| `memory_read` | 读全文 | `file` |
| `memory_recall` | BM25 检索 | `query` |
| `memory_forget` | 软删 | `file` |
| `memory_restore` | 恢复 | `trash_file` |
| `memory_purge` | 永久删 | `file`, `confirm=true` |
| `wiki_link` | Wiki 互链 | `from_topic`, `to_topic` |
| `wiki_log` | Wiki 时间线 | `kind`, `title`, `summary` |
| `ingest_next` | 处理摄入队列 | （无） |

`layer` 取值：`auto`（项目事实，默认）、`agent`（Agent 长期）、`wiki`（知识库）。

## 响应格式

- 成功：`{"ok": true, ...}`
- 失败：`{"ok": false, "error": "..."}`
- 提案：`{"ok": true, "proposal": true, "proposal_file": "...", ...}`

**回传 LLM 时用完整 JSON 字符串。**

## layer 选型

- 用户说记住 / 项目事实 → `memory_save` + `layer: "auto"`
- Agent 专属习惯 → `layer: "agent"`
- 文档入库 → `ingest()` 然后 `ingest_next`，再 `memory_save`/`wiki_*`

## 路径

- 所有 `file` 参数：**相对 project_root**
- 例：`.bobo/memory/auto/topic.md`

## 禁止

- 手写 `.bobo/memory/` 绕过工具
- 自写记忆系统 prompt（用 `build_system_prompt`）
- `agent_type`/`scope` 与 init 不一致

## 验收

```python
r = mem.dispatch_tool_call("memory_save", {
    "layer": "auto", "topic": "test", "content": "x", "summary": "test"
})
assert r["ok"]
```
