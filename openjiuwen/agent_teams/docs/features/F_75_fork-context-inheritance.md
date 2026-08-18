# 团队 Fork 上下文继承

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-08-06（D6 / D7 能力开关补充：2026-08-08） |
| 范围 | `agent_teams/fork.py` · `fork_compact.py`（新增），`tools/tool_member.py`（新增 `CheckpointTool` + `SpawnTeammateTool` 扩展），`tools/team.py`（`_pending_forks` / `_checkpoints` + `set_snapshot_length` / `set_store_checkpoint_fn` + `fork_enabled`），`agent/team_agent.py`（`_on_teammate_created` 重写 + `_resolve_fork_native` + `share_checkpoints_with`），`agent/agent_configurator.py`（`_snapshot_length` 回调 + `enable_fork` 透传），`spawn/inprocess_spawn.py`（注入 + compaction 调用），`harness/team_harness.py`（`get_deep_agent`），`schema/blueprint.py`（`TeamAgentSpec.enable_fork`），`tools/tool_factory.py`（注册 `checkpoint` + `fork_enabled()` 门控），`tools/tool_permissions.py`（`SHARED_TOOLS` 加 `checkpoint`），`tools/locales/__init__.py`（`omit` capability 槽），`external/`（`client.py` / `sdk_mcp.py` 排除 checkpoint），`tools/locales/`（中英文参数 + 描述），`tools/locales/descs/*/checkpoint.md`（新增）+ `spawn_teammate.md`（`{{fork_usage}}` 槽）+ `descs/*/fragments/fork_usage.md`（新增） |
| 测试基线 | `tests/unit_tests/agent_teams/test_fork.py` 48 passed；`tools/` + `external/` + `agent/` + `test_team_tools` + `test_hitt` + `test_predefined_team` 612 passed / 14 skipped；`rails/` + `prompts/` + `test_policy` + `test_persistent_team` + `test_language_propagation` + `test_harness` 93 passed |
| Refs | #984 |

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
存储的是位置索引，不绑定语义方向 — 由 `spawn_teammate` 的 `fork` / `fork_mode` 参数决定消费方式。
字典挂载在 leader 的 `TeamAgent` 上，`inprocess_spawn` 中通过 `share_checkpoints_with()` 共享给 teammate，并通过 `TeamBackend.set_store_checkpoint_fn()` 注册回调，确保任意成员打的快照对 leader 可见。

外部成员（MCP / CLI）排除 `checkpoint` 工具 — 无 `DeepAgent`，快照无意义。`create_team_tools` 在 `external/client.py:221` 和 `sdk_mcp.py:97` 传入 `exclude_tools={"checkpoint"}`。

### D3 Fork Compaction — 上下文压缩

`compact_context(agent, split_at=N, direction="before"|"after")` 在 fork 注入后将一侧消息通过一次模型调用压缩为摘要，另一侧全量保留。`direction="before"`（默认）压缩 checkpoint 之前、保留之后；`direction="after"` 保留之前、压缩之后。`direction="after"` 时保留头会扩展过 checkpoint 调用结果块，避免保留头以悬空工具调用结尾（会被上下文 rail 标记为"中断"）。
选用子代理的 `deep_config.model.invoke()` 做压缩，消息刚注入、KV cache 热，只多消耗约 500 输出 token。

压缩通过 `context_engine.get_context()` → `set_messages()` 直接替换上下文，不重新创建 session。压缩在 `inprocess_spawn` 中、`Runner.run_agent_team` 之前完成，对子代理透明。

### D4 Fork 语义

`spawn_teammate` 用 `fork`（`true` | `"ckpt-name"`）+ `fork_mode` 表达消费方式，五种模式：

| fork | fork_mode | 行为 |
|------|-----------|------|
| `true` | `full`（默认） | 全量注入 |
| `"ckpt"` | `before`（默认） | 保留 checkpoint 之前的消息 |
| `"ckpt"` | `after` | 保留从 checkpoint 起的消息 |
| `"ckpt"` | `keep_before_compact_after` | 保留前，把之后压缩为摘要 |
| `"ckpt"` | `keep_after_compact_before` | 保留后，把之前压缩为摘要 |

compact 两种模式捕获全量源上下文，由 `compact_context` 按 `direction` 裁剪（`keep_before_compact_after`→`after`、`keep_after_compact_before`→`before`），使 split 索引仍与注入上下文对齐。
未传 `fork_mode` 时：`fork=true` 默认 `full`，命名 fork 默认 `before`（保持首版截断行为）。
live fork 传非 `full` 模式 → warning 忽略按 `full` 处理；未知 `fork_mode` → 回退 `full`。

### D5 封装约定

fork 代码需要访问几个 "protected" 属性（`_native`、`_named_checkpoints`、`_snapshot_length` 等）。
遵循封装原则，在对应类上添加了公开方法替代直接属性访问：

- `TeamHarness.get_deep_agent()` — 替代 `harness._native`
- `TeamAgent.share_checkpoints_with()`、`set_checkpoint()`、`_set_checkpoints_from()` — 替代 `_named_checkpoints` 写入和跨实例赋值
- `TeamBackend.set_snapshot_length()` / `set_store_checkpoint_fn()` — 替代直接赋 `_xxx`
- `fork_compact` 使用已有的公开 `react_agent` property 替代 `_react_agent`

### D6 能力开关 `TeamAgentSpec.enable_fork`（默认关）

fork 首版把三个表面都无条件打开了：`checkpoint` 进 `SHARED_TOOLS`（每个成员都看得到），
`spawn_teammate` 的 schema 硬编码 `fork` / `fork_source` / `fork_mode`，描述里常驻一整节 fork 文档。
这与同仓其余可选能力的做法相反——`swarmflow`（`enable_swarmflow`）、`spawn_human_agent`
（`enable_hitt`）、`spawn_bridge_agent`（`enable_bridge`）、`spawn_external_cli`
（`external_cli_agents` 非空）全都有 spec 级 capability ceiling。fork 也应如此：
它只在 `spawn_mode="inprocess"` 下可用，且并非所有团队都需要上下文继承，
却让每个成员为它常驻工具与描述 token。

新增 `TeamAgentSpec.enable_fork: bool = False` → `TeamBackend(enable_fork=...)` →
`fork_enabled()`，**一个信号门控三处**：

| 表面 | 门控点 |
|---|---|
| `checkpoint` 工具注册 | `tool_factory` 减法链 `allowed - {"checkpoint"}` |
| `spawn_teammate` 的 `fork`/`fork_source`/`fork_mode` 属性 | `SpawnTeammateTool.__init__` 按 `fork_enabled` 拼 `properties` |
| 描述里的「上下文继承（Fork）」整节 | 同一构造传 `omit={"fork_usage"}`，槽收敛为空串 |

**三者必须同源**：schema 有参数而描述不提，LLM 不知道怎么用；描述讲了而 schema 没有，
LLM 会围绕一个填不了的参数反复权衡——后者比完全不提更糟。这条与 `leader_policy` 的
`{{collaboration_mechanism}}`（gate `swarmflow_enabled`）是同一个模式。

沿用现有的能力门风格，`invoke` 内保留防御性检查作为 MCP 兜底：MCP server 直接
`await tool.invoke(arguments)`、不校验 `input_params`，所以被省略的属性必须在
`invoke` 里被拒，**且拒在写成员行之前**（否则成员已建、fork 却没生效）。
`CheckpointTool` 同理。

不给 `build_team` 加运行时下调参数（不同于 `enable_hitt`）：fork 是执行期优化，
不是 leader 该按团队实例挑的团队形态。

**行为变更**：升级后 fork 默认不可用，需要的部署显式 `TeamAgentSpec(enable_fork=True)`。
capability 默认关是本仓一贯的 fail-safe 取向，且 fork 合入仅一天，无存量依赖。

### D7 描述模板化扩展：capability 槽

`{{slot}}` 机制原本只有一种语义——从 `fragments/<slot>.md` 加载，缺文件即构造期炸
（S_08 不变量 6）。fork 段落需要"按开关出现或消失"，加一种最小扩展：
`t(desc_key, omit={"<slot>"})`，被点名的槽填空串、不读片段文件。

没有走"两份 md（`spawn_teammate.md` / `spawn_teammate_fork.md`）"的形态路线：fork
只是描述里的一节，复制整份 md 会让另外 50 行同步维护两遍，必然漂移——AGENTS.md 对
调度器消息已经下过同样的判断（"同一条消息两处文案必然漂移"）。

`omit` 是**调用方显式点名**，不是"槽找不到片段就当空"的隐式回退——后者会把拼错的
槽名静默吞掉，正是不变量 6 要堵的洞。渲染后统一 `strip()`，使位于文末的槽被省略时
不留悬空空行。

### D8 触发时机与校验

fork 决策在 `_on_teammate_created` 中执行 — `spawn_teammate` 工具仅标记意图，实际上下文注入发生在成员初始化完成的异步时刻。
对非法组合打 warning 并降级：
- live fork（`fork=true`）传非 `full` 的 `fork_mode` → 忽略，按 `full` 注入
- 命名 fork 的 checkpoint 不存在（或 `fork_source` 与其创建者不符）→ 回退为全量，并给 leader 发一条含可用快照名的消息
- 未知 `fork_mode` → 回退为全量

## 拒绝的方案

- **`since_checkpoint` 独立增量模式**：仅取 checkpoint 之后的消息缺少文件原文作依据，信息不完整；与 compact 组合语义重叠。首版从 API 移除。**后续 5 模式扩展（`fork_mode`）部分复活了 keep-after 语义**（`after` / `keep_after_compact_before`）——区别在于它们是完整继承上下文的一种取舍形态，而非独立的增量模式。
- **`fork=true + compact=true`**：全量上下文无需主动压缩，超出限制时 `FullCompactProcessor` 兜底。保留增加 API 复杂度无收益。**5 模式扩展后由 `fork_mode` 统一表达**，live fork 传非 `full` 模式会被忽略。
- **`fork="ckpt"` 时 compact 截断后再压缩**：截断后再压缩全部语义不清，不如 `compact=true` 统一为 "全量注入 + ckpt 分界"。**`keep_before_compact_after` 是这条思路的一种显式形态**（保留前 + 压缩后），经 `fork_mode` 明确表达。
- **第三方 CLI 成员 fork**：外部 CLI 对话存于外进程，无 `DeepAgent` / `ContextEngine`，无法取或注入上下文。永久不做。
- **直接访问 protected 成员**：`_native` / `_react_agent` / `_named_checkpoints` 等多处 external access 违反 `G.CLS.11`。已通过 D5 的封装约定解决。
- **fork 无条件开放**（首版做法）：见 D6。可选能力常驻每个成员的工具列表与描述，与同仓四个 capability ceiling 的做法相反。
- **fork 描述拆成两份 md**：`spawn_teammate.md` + `spawn_teammate_fork.md` 的形态路线会把与 fork 无关的 50 行复制两份，必然漂移。改用 capability 槽（D7）。
- **`build_team(enable_fork=...)` 运行时下调**：fork 是执行期优化，不是 leader 该按团队实例挑的团队形态；`enable_hitt` 那套 ceiling + 实例开关在这里没有对应场景。

## 验证

- `test_fork.py`（35 passed）：覆盖 `ForkContext.from_agent`（全量 / 截断 / `keep="after"` + 孤儿 ToolMessage 剔除 / 边界 / SystemMessage 剥离 / roundtrip），`CheckpointTool`（invoke / map_result），`TeamBackend` fork 方法（mark→consume / 无回调 / 回调写入 / fallback），`SpawnTeammateTool` fork 参数（fork / fork_source / fork_mode / 无 fork 不标记），`compact_context`（分段替换 / split_at=0 跳过 / ≥len 跳过 / `direction="after"` 反向压缩 + 保留头扩展）。
- `test_fork.py::TestOnTeammateCreatedFork`（10 cases，装配路径）：覆盖 `TeamAgent._on_teammate_created` 的 fork 解析——live fork（字符串 / 布尔）/ 命名 checkpoint 截断 / `fork_mode` 5 模式派发 / checkpoint 缺失回退 / live fork 非 `full` 忽略 / 无 fork / `fork_source` 可解析与不可解析 / `fork_source` 指向 leader。这组用例是两次运行时 crash（`'NoneType' object has no attribute 'messages'`）的回归护栏：修复前 live fork 与 checkpoint 截断两条路径均抛 `AttributeError`，修复后全绿。
- `test_mcp_server.py`（8 passed）：验证外部 MCP 成员排除 `checkpoint` 工具。
- `test_fork.py` 的 capability gate 组（13 cases）：`enable_fork=False` × (cn/en) × (leader/teammate) 下 `checkpoint` 不注册、`spawn_teammate` 无 fork 属性、描述里不含 "fork" 字样且无残留 `{{`；`enable_fork=True` 下三者齐备；`CheckpointTool.invoke` 与 `SpawnTeammateTool.invoke`（三组 fork 参数）在关闭时拒绝并给出 `enable_fork` 指引，且拒绝时**不写成员行**；不传 fork 参数的普通 spawn 不受影响。
- 全量 `tests/unit_tests/agent_teams/` 0 新增失败。

## 已知遗留

- **Checkpoint 非持久化**：~~存于内存，进程重启后丢失。后续可接入 session state。~~ **已解决**，见 [[F_76_fork-checkpoint-persistence]]——checkpoints 现持久化于 session per-team namespace，冷恢复（`recover_from_session`）自动还原；顺带修复了 leader 自身 `checkpoint()` 工具的路由缺口（此前落 `TeamBackend._checkpoints` 兜底 dict，fork 读不到）。
- **subprocess spawn fork**：跨进程 fork 的 payload 未接线，当前仅支持 `spawn_mode="inprocess"`。`enable_fork=True` 配 `spawn_mode="process"` 不报错，fork 静默失效——门控只管能力开关，不校验 spawn 模式的组合。
