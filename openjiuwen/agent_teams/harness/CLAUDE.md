# Agent Teams Harness（NativeHarness 运行时外壳）

team 成员的运行时外壳层。`NativeHarness` 是 `DeepAgent` 的子类——它 **就是** 那个 DeepAgent，
只替换交互模型：用单 supervisor 协程一次驱动一个 outer round（`submit_round` +
`wait_round_completion`），保留 DeepAgent 的全部能力（task loop / 中断恢复 / plan mode /
SESSION_SPAWN / rails / state）。`TeamHarness` 在其上做 team 适配。

## 模块地图

```
harness/
├── protocol.py        # HarnessProtocol（结构化契约，isinstance 校验）
├── native_harness.py  # NativeHarness(DeepAgent)：supervisor + round 驱动 + send/steer/follow_up
├── team_harness.py    # TeamHarness：在 NativeHarness 上做 team 适配（build / role）
├── control.py         # _CmdSend / _CmdAbort / _CmdPause / _CmdRoundFinished / _CmdStop
├── state.py           # HarnessInternalState / InboxMessage / ActiveRound / HarnessState
├── outputs.py         # _OutputIterator / _END 输出迭代
├── snapshot_rail.py   # SnapshotRail：round 边界快照（回滚用）
└── async_tools.py     # 异步后台工具框架（AsyncTool / AsyncToolRuntime / render_result_text）
```

## 异步工具框架（`async_tools.py`）

照搬 Claude Code 两段式范式的通用异步后台工具框架，**限定在 NativeHarness 范围、零
TeamAgent 依赖**。详见 `docs/features/F_35` 与 `docs/specs/S_20`。

**两段式（强约束下的唯一可行解）**：openjiuwen 工具循环与 Anthropic API 一样强配对——
每个 `tool_call` 必产生紧随的 `ToolMessage`，`invoke` 同步阻塞、不可挂起 `tool_result`。
所以异步工具：① `invoke` 立即返回 `launched(task_id)` 闭合配对、不阻塞当前轮；② 完成结果
**绕过工具协议**，经 `NativeHarness.send(text, immediate=False)` 作为独立注入式消息进下一轮
（IDLE 起新轮、RUNNING 排 follow-up，**统一不分 active/idle**）。

| 符号 | 职责 |
|---|---|
| `AsyncTool(TeamTool)` | 异步工具基类。持 `parent_agent`(NativeHarness)；子类实现 `run_background(task_id, inputs)`；`invoke` 立即返回 launched |
| `AsyncToolRuntime` | 挂在 NativeHarness 上的后台任务注册表 + 完成注入器。`launch` / `has_running` / `cancel_all`；完成调注入回调（= `harness.send(immediate=False)`） |
| `AsyncToolRecord` | 注册表行（task_id / tool_name / status / result / error） |
| `render_result_text` | 通用结果渲染，**完整不截断**（str 原样 / dict-list JSON / 其它 str()） |

**NativeHarness 集成**：`async_tool_runtime`（lazy property，`inject=self._inject_async_completion`）
+ `launch_async_tool(...)` + `stop()` 调 `cancel_all()`。

**工具拿 harness 的方式**：照 `sessions_spawn` 形状——`AsyncTool` 持 `parent_agent`，由
`TeamToolRail.init(agent)` 的 `agent`(NativeHarness) 在装配期注入（invoke 时 harness 早已 start）。

**第一个实现 = swarmflow**（`workflow/tool_swarmflow.py`）。新增异步工具时继承 `AsyncTool`、实现
`run_background`、经装配注入 `parent_agent` 即可——不必再手搓后台 task + 事件 + handler。

## 铁律

1. **框架不牵扯 TeamAgent。** 只依赖 NativeHarness 公共表面（`send` / `launch_async_tool` /
   `model`）。`TeamAgent.deliver_input` 只是 `harness.send` 的薄包装；要注入直接用 harness。
2. **没有异步 tool_result。** 启动段与完成段是两个独立回合；完成结果绝不回到原 `tool_use_id`。
3. **不收编 sessions_spawn / 不走 TaskScheduler 的 SESSION_SPAWN。** 避免结果写进 DeepAgent
   `pending_follow_ups`（进 session checkpoint 的 user-bound 语义）污染恢复。
