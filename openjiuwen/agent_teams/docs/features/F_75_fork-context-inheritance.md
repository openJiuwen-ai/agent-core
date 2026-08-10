# 团队 Fork 上下文继承

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-08-06 |
| 范围 | `agent_teams/fork.py` · `fork_compact.py`（新增），`tools/tool_member.py`（新增 `CheckpointTool` + `SpawnTeammateTool` 扩展），`tools/team.py`（`_pending_forks` / `_checkpoints` + `set_snapshot_length` / `set_store_checkpoint_fn`），`agent/team_agent.py`（`_on_teammate_created` 重写 + `_resolve_fork_native` + `share_checkpoints_with`），`agent/agent_configurator.py`（`_snapshot_length` 回调），`spawn/inprocess_spawn.py`（注入 + compaction 调用），`harness/team_harness.py`（`get_deep_agent`），`tools/tool_factory.py`（注册 `checkpoint`），`tools/tool_permissions.py`（`SHARED_TOOLS` 加 `checkpoint`），`external/`（`client.py` / `sdk_mcp.py` 排除 checkpoint），`tools/locales/`（中英文参数 + 描述），`tools/locales/descs/*/checkpoint.md`（新增）+ `spawn_teammate.md`（fork 文档段） |
| 测试基线 | `tests/unit_tests/agent_teams/test_fork.py` 35 passed；全量 `tests/unit_tests/` 0 新增失败 |
| Refs | — |

## 背景

团队中多个并行的执行者需要对同一个代码仓库有共同的理解（如基类接口、项目架构）。
每个执行者独立启动时需要重新读取文件、搜索代码、分析结构 — 重复的 IO 和 token 消耗。
需要一个机制让已经理解代码的代理将上下文直接继承给新成员，省去重复探索。

## 决策

### D1 ForkContext — 上下文捕获

`ForkContext.from_agent(agent, checkpoint=N)` 捕获代理当前全部对话历史。`checkpoint=None` 返回全量，`checkpoint=N` 截断到消息索引 N 之前。捕获的是 `UserMessage` / `AssistantMessage` / `ToolMessage` 三类消息——`SystemMessage` 不在 `ContextEngine` 的消息池中，不在继承范围。目标代理的角色由 `TeamPolicyRail` 注入，与 fork 源无关。

序列化使用已有 VCS codec（`encode_message` / `decode_message`），不引入新格式。

### D2 CheckpointTool — 命名快照

`checkpoint(name="code-ready")` 保存当前 `len(messages)` 到共享字典 `_named_checkpoints`。
存储的是位置索引，不绑定语义方向 — 由 `spawn_teammate` 的 `fork` / `compact` 参数决定消费方式。
字典挂载在 leader 的 `TeamAgent` 上，`inprocess_spawn` 中通过 `share_checkpoints_with()` 共享给 teammate，并通过 `TeamBackend.set_store_checkpoint_fn()` 注册回调，确保任意成员打的快照对 leader 可见。

外部成员（MCP / CLI）排除 `checkpoint` 工具 — 无 `DeepAgent`，快照无意义。`create_team_tools` 在 `external/client.py:221` 和 `sdk_mcp.py:97` 传入 `exclude_tools={"checkpoint"}`。

### D3 Fork Compaction — 上下文压缩

`compact_context(agent, split_at=N)` 在 fork 注入后将 checkpoint 之前的消息通过一次模型调用压缩为摘要，之后的全量保留。
选用子代理的 `deep_config.model.invoke()` 做压缩，消息刚注入、KV cache 热，只多消耗约 500 输出 token。

压缩通过 `context_engine.get_context()` → `set_messages()` 直接替换上下文，不重新创建 session。压缩在 `inprocess_spawn` 中、`Runner.run_agent_team` 之前完成，对子代理透明。

### D4 Fork 语义

仅保留三个有实用价值的组合：

| fork | compact | 行为 |
|------|---------|------|
| `true` | — | 全量注入 |
| `"ckpt"` | false | 截断到 ckpt 之前 |
| `"ckpt"` | true | ckpt 为分界：之前压缩为摘要，之后全量保留 |

其他组合由系统内置 `FullCompactProcessor` 自动兜底。

### D5 封装约定

fork 代码需要访问几个 "protected" 属性（`_native`、`_named_checkpoints`、`_snapshot_length` 等）。
遵循封装原则，在对应类上添加了公开方法替代直接属性访问：

- `TeamHarness.get_deep_agent()` — 替代 `harness._native`
- `TeamAgent.share_checkpoints_with()`、`set_checkpoint()`、`_set_checkpoints_from()` — 替代 `_named_checkpoints` 写入和跨实例赋值
- `TeamBackend.set_snapshot_length()` / `set_store_checkpoint_fn()` — 替代直接赋 `_xxx`
- `fork_compact` 使用已有的公开 `react_agent` property 替代 `_react_agent`

### D6 触发时机与校验

fork 决策在 `_on_teammate_created` 中执行 — `spawn_teammate` 工具仅标记意图，实际上下文注入发生在成员初始化完成的异步时刻。
对非法组合打 warning 并降级：
- `compact=true` 未配合 checkpoint fork → 忽略 compact
- checkpoint 不存在 → 回退为全量

## 拒绝的方案

- **`since_checkpoint` 独立增量模式**：仅取 checkpoint 之后的消息缺少文件原文作依据，信息不完整；与 compact 组合语义重叠。已砍掉，从 API 移除。
- **`fork=true + compact=true`**：全量上下文无需主动压缩，超出限制时 `FullCompactProcessor` 兜底。保留增加 API 复杂度无收益。
- **`fork="ckpt"` 时 compact 截断后再压缩**：截断后再压缩全部语义不清，不如 `compact=true` 统一为 "全量注入 + ckpt 分界"。
- **第三方 CLI 成员 fork**：外部 CLI 对话存于外进程，无 `DeepAgent` / `ContextEngine`，无法取或注入上下文。永久不做。
- **直接访问 protected 成员**：`_native` / `_react_agent` / `_named_checkpoints` 等多处 external access 违反 `G.CLS.11`。已通过 D5 的封装约定解决。

## 验证

- `test_fork.py`（35 passed）：覆盖 `ForkContext.from_agent`（全量 / 截断 / 边界 / SystemMessage 剥离 / roundtrip），`CheckpointTool`（invoke / map_result），`TeamBackend` fork 方法（mark→consume / 无回调 / 回调写入 / fallback），`SpawnTeammateTool` fork 参数（fork / fork_source / compact / 无 fork 不标记），`compact_context`（分段替换 / split_at=0 跳过 / ≥len 跳过）。
- `test_fork.py::TestOnTeammateCreatedFork`（10 cases，装配路径）：覆盖 `TeamAgent._on_teammate_created` 的 fork 解析——live fork（字符串 / 布尔）/ 命名 checkpoint 截断 / checkpoint 缺失回退 / compact split / compact 无命名降级 / 无 fork / `fork_source` 可解析与不可解析 / `fork_source` 指向 leader。这组用例是两次运行时 crash（`'NoneType' object has no attribute 'messages'`）的回归护栏：修复前 live fork 与 checkpoint 截断两条路径均抛 `AttributeError`，修复后全绿。
- `test_mcp_server.py`（8 passed）：验证外部 MCP 成员排除 `checkpoint` 工具。
- 全量 `tests/unit_tests/agent_teams/` 0 新增失败。

## 已知遗留

- **Checkpoint 非持久化**：存于内存，进程重启后丢失。后续可接入 session state）。
- **subprocess spawn fork**：跨进程 fork 的 payload 未接线，当前仅支持 `spawn_mode="inprocess"`。
