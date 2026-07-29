# Agent Teams Harness（NativeHarness 运行时外壳）

team 成员的运行时外壳层。`NativeHarness` 是 `DeepAgent` 的子类——它 **就是** 那个 DeepAgent，
只替换交互模型：用单 supervisor 协程一次驱动一个 outer round（`submit_round` +
`wait_round_completion`），保留 DeepAgent 的全部能力（task loop / 中断恢复 / plan mode /
SESSION_SPAWN / rails / state）。`TeamHarness` 在其上做 team 适配。

## 模块地图

```
harness/
├── protocol.py        # HarnessProtocol（结构化契约，isinstance 校验）：send / abort / pause / resume / stop / outputs
├── native_harness.py  # NativeHarness(DeepAgent)：supervisor + round 驱动 + send/steer/follow_up；run_once 非流式单次 invoke（不开 supervisor，返回 Runner.run_agent 格式 dict）
├── team_harness.py    # TeamHarness：在 NativeHarness 上做 team 适配（build / role）；run_once 转发（单轮 worker 入口）
├── control.py         # _CmdSend / _CmdAbort / _CmdPause / _CmdResume / _CmdRoundFinished / _CmdStop
├── state.py           # HarnessInternalState / InboxMessage / ActiveRound / HarnessState / RoundPhase
├── outputs.py         # _OutputIterator / _END 输出迭代
├── snapshot_rail.py   # PhaseSnapshotRail：inner-loop 阶段追踪 + iteration/round 边界快照 + cooperative stop
└── async_tools.py     # 异步后台工具框架（AsyncTool / AsyncToolRuntime / render_result_text）
```

## pause / abort / resume（两个正交动词）

停止点一律落在 **inner ReAct iteration 边界**。`abort` 丢弃当前 round（→ IDLE，下次 `send` 起
全新 round）；`pause` 停在干净边界并**保留 round**（→ PAUSED，`resume` 原地续跑，不追加新的
user turn）。

`PhaseSnapshotRail` 是正确性权威：它在 `before/after_model_call` 上做 cooperative
`force_finish`，保证 loop 总能停在边界、**绝不打断运行中的 tool**（副作用不可撤销）；supervisor
的 hard-cancel 只是及时性优化，且 gated 到 `model_call_in_flight`（只能落在 parked 的 LLM
`await`）。tool 阶段的 pause 经 `PAUSING` 过渡态等待边界，ack 延迟到 `_on_round_done` resolve。

该 rail 用 **harness back-ref**（公共只读属性 `harness.active_round`）定位活跃 round —— inner loop 跑在
TaskScheduler 的 exec task 里，ContextVar 读不到。详见 [`S_18`](../docs/specs/S_18_harness-interaction-contract.md)
与 [`F_60`](../docs/features/F_60_native-harness-pause-abort-resume.md)。

## 异步工具框架（`async_tools.py`）

照搬 Claude Code 两段式范式的通用异步后台工具框架，**限定在 NativeHarness 范围、零
TeamAgent 依赖**。详见 `docs/features/F_35`（起步版）、`F_41`（统一 ID + 管控工具 + 磁盘溢写）
与 `docs/specs/S_20`。

**两段式（强约束下的唯一可行解）**：openjiuwen 工具循环与 Anthropic API 一样强配对——
每个 `tool_call` 必产生紧随的 `ToolMessage`，`invoke` 同步阻塞、不可挂起 `tool_result`。
所以异步工具：① `invoke` 立即返回 `launched(task_id)` 闭合配对、不阻塞当前轮；② 完成结果
**绕过工具协议**，经 `NativeHarness.send(text, immediate=False)` 作为独立注入式消息进下一轮
（IDLE 起新轮、RUNNING 排 follow-up，**统一不分 active/idle**）。

| 符号 | 职责 |
|---|---|
| `AsyncTool(TeamTool)` | 异步工具基类。持 `parent_agent`(NativeHarness)；子类实现 `run_background(task_id, inputs)`；`invoke` 立即返回 launched |
| `AsyncToolRuntime` | 挂在 NativeHarness 上的后台任务注册表 + 完成注入器。`launch(task_id, coro_factory, *, tool_name, description, format_completed=None, format_failed=None)` / `has_running` / `cancel_all` + 管控面 `get` / `list_all` / `cancel(task_id)` / `wait(task_id, timeout)`；`_tasks` 是 `dict[task_id, Task]`（非 set）+ per-task `_events`；完成经 `_maybe_spill`（内联 vs 写盘）后调注入回调（= `harness.send(immediate=False)`）。`_run`：回调存在且返回非 `None` 则用回调文本 inject，否则回退 `async_tool.completed` / `async_tool.failed` |
| `AsyncToolRecord` | 注册表行（task_id / tool_name / status / result / error / `output_file` / `format_completed` / `format_failed` 可选回调） |
| `CompletionFormatter` / `FailureFormatter` | 类型别名（`Callable[[Any], str \| None]` / `Callable[[str], str \| None]`，`async_tools.__all__` 导出） |
| `render_result_text` | 通用结果渲染，**完整不截断**（str 原样 / dict-list JSON / 其它 str()） |

**NativeHarness 集成**：`async_tool_runtime`（lazy property，`inject=self._inject_async_completion`）
+ `launch_async_tool(...)`（透传 `format_completed` / `format_failed` 给 `async_tool_runtime.launch`）
+ `stop()` 调 `cancel_all()`。

**工具拿 harness 的方式**：照 `sessions_spawn` 形状——`AsyncTool` 持 `parent_agent`，由
`TeamToolRail.init(agent)` 的 `agent`(NativeHarness) 在装配期注入（invoke 时 harness 早已 start）。

**第一个实现 = swarmflow**（`workflow/tool_swarmflow.py`）。基类契约演进：旧版 `run_background` 返回
文本直接进 `async_tool.completed`；现版 `run_background` 返回**原始结果**（`Any`，非 `str`），终态文本由子类经
`format_completed` / `format_failed` 回调产出（swarmflow 注入 `swarmflow.completed` / `swarmflow.failed`，含
`run_id` + `summarize_run` 摘要）；无回调时框架用 `render_result_text` + 默认 `async_tool.*`（不含 run_id）。
新增异步工具时继承 `AsyncTool`、实现 `run_background`、按需传 `format_*` 回调、经装配注入 `parent_agent`
即可——不必再手搓后台 task + 事件 + handler。详见 `F_47` / `S_20` / `S_21`。

**统一 task_id**：`AsyncTool.invoke` 用 `id_generator.generate_id(self.card.name)`（前缀体系，
swarmflow→`w`、未注册→`t`）；`id_generator.py` 在 agent_teams 顶层（非 core）。

**管控工具**（`tools/tool_async.py`，三个普通 `TeamTool`、非 `AsyncTool` —— 同步立即返回）：
`async_tasks_list` / `async_task_output`（`block`/`timeout` + 读盘）/ `async_task_cancel`，经
`parent_agent.async_tool_runtime` 操作，列入 `LEADER_ONLY_TOOLS` 始终注入（不 gate）。

**磁盘溢写**：结果文本 >`spill_threshold`（默认 32KB）时写
`paths.async_tool_output_dir(team_name, session_id)/{task_id}.output` + 注入"摘要+路径+取回提示"；
≤阈值完整内联（尊重"完整回灌"）。溢写目录由 `TeamToolRail._wire_async_spill` 经
`output_dir_resolver` 惰性注入（runtime 不知 team_name/session_id）+ `register_cleanup_path`
注册清理。详见 `S_20` / `F_41`。

## 铁律

1. **框架不牵扯 TeamAgent。** 只依赖 NativeHarness 公共表面（`send` / `launch_async_tool` /
   `model`）。`TeamAgent.deliver_input` 只是 `harness.send` 的薄包装；要注入直接用 harness。
2. **没有异步 tool_result。** 启动段与完成段是两个独立回合；完成结果绝不回到原 `tool_use_id`。
3. **不收编 sessions_spawn / 不走 TaskScheduler 的 SESSION_SPAWN。** 避免结果写进 DeepAgent
   `pending_follow_ups`（进 session checkpoint 的 user-bound 语义）污染恢复。
