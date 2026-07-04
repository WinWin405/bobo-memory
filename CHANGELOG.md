# Changelog

## 0.2.0 (2026-07-03)

安全与治理链路加固版本。公开 API（`MemoryClient`、`dispatch_tool_call`、
`to_openai_tools` / `to_anthropic_tools` / `to_langchain_tools`、`ingest`、
`recall`、`status`）保持兼容。

### 安全 / 治理

- `wiki_link` / `wiki_log` 现在与其他写入工具一样经过 policy（writable_by、
  secret 扫描、大小限制）与 guard 路径护栏检查。
- `dispatch_tool_call(actor=...)` 的 `actor` 现在真正传入 policy 检查与审计
  日志（此前被忽略，始终按 `agent` 处理）。
- `memory_update` 现在尊重 `write_mode: proposal`：对提案模式层的更新会被
  重定向到 `.bobo/proposals/`，不再直接落盘。
- `memory_forget` / `memory_purge` 增加 `writable_by` 策略检查。
- `bobo-memory proposal accept` 合并前重新执行 policy + guard 检查（大小、
  secret 扫描、路径边界），阻止含密钥的提案入库。
- `agent_type` 与 `topic` 净化加强：拒绝空值、`.`、`..` 等可穿透目录树的
  取值；`topic` 不得映射到保留协议文件（`MEMORY.md`、`INDEX.md`），
  `memory_update` 亦不可直接改写协议文件。

### 稳定性

- `atomic_write` 默认在 rename 前 `fsync`（可用 `durable=False` 关闭），
  掉电不再丢已确认的写入；Windows 上 `os.replace` 遇目标被占用时短暂重试。
- frontmatter / proposal 头部改为安全生成（proposal 用 `yaml.safe_dump`），
  summary 含引号、换行或 tags 含 `[],` 不再产生损坏文件；MEMORY.md 索引行
  强制单行。
- `memory_save` 覆盖已有记忆时保留原 `created` 日期。
- `wiki_log` 超过层大小上限时自动轮转为 `log-<timestamp>.md`，不再无限增长。

### 召回质量

- `recall` 只返回与查询至少共享一个词元的文件；不相关的 top-k 填充结果不再
  浪费调用方 token 预算。

### 提示词

- `memory_save` 工具化保存协议取代旧的"两步手写文件"说明（旧说明教的操作
  路径已被护栏禁止）；新增"何时保存"触发指引。
- 多层注入时共享指令块只出现一次（`build_shared_instructions()`），注入
  token 约省一半；两层同时启用时自动附加分层路由规则。
- 各层 `build_prompt(include_instructions=False)` 可单独渲染精简片段，
  默认行为不变。

### 自动记忆（搭便车模式，零额外 LLM 调用）

- 新增 `MemoryClient.memory_nudge(messages)`：纯规则扫描最近消息中的纠正 /
  偏好 / 决策 / "记住"信号（中英文），命中且过冷却期时返回一行提醒，附加到
  下一次请求的 system prompt，由主模型在正常回合内完成保存。
- 新增 `MemoryClient.find_similar(content)`：保存前的 BM25 去重检查。

### 集成面

- 新增 MCP stdio server（`bobo-memory mcp`，需 `pip install "bobo-memory[mcp]"`），
  把 11 个记忆工具暴露给任意 MCP 宿主；所有调用走完整治理链路。
  兑现了此前声明但未实现的 `[mcp]` extra。
- viewer 扩展为只读 REST API：新增 `/layers`、`/memories/{layer}`、
  `/memory?file=`、`/recall?query=`、`/storage`，读取经 guard 路径校验。

### 摄入队列（租约 / 确认）

- `ingest_next` 改为租约语义：任务不再立即从 staging 删除，而是标记
  `in_progress` 并记录租约时间——Agent 崩溃不再丢任务。
- 新增 `ingest_done` 工具（第 12 个工具）：确认整合完成后从队列移除；
  未确认的任务在租约到期后自动重试，超过 `staging.max_attempts`（默认 3）
  次后标记 `failed` 并保留待人工检查。
- 新增策略节 `staging: {lease_minutes: 30, max_attempts: 3}`。

### 锁文件集中化

- `.bobo` 树内文件的写锁统一放到 `<.bobo>/locks/<hash>.lock`，不再在记忆
  目录留下 `*.lock`（Windows 上 filelock 进程异常退出时无法自删）；
  `.bobo` 树外的路径保留旁路锁以保证与目标同文件系统（远程挂载场景）。
- `run_janitor()` 新增 `locks` 步骤：清理 `.bobo/` 下超过 24 小时的陈旧
  锁文件（含旧版本散落的旁路锁）。

### CI

- 新增 GitHub Actions 工作流：Ubuntu / Windows / macOS × Python 3.10 / 3.13
  全矩阵测试，另加一个最小依赖任务验证可选依赖保持可选。

### 打包 / 清理

- `watchdog` 降为可选依赖：`pip install "bobo-memory[watch]"`（仅
  `watch_directory()` 需要）。
- 新增 `py.typed`，下游可获得类型提示。
- 移除 M1–M5 里程碑遗留的 ImportError 回退脚手架（`client.recall` /
  `compact` / `snapshot` / `team`），导入错误不再被吞掉。
- 工具分发表改为模块级构建一次，不再每次调用重建。

## 0.1.1

初始公开版本。
