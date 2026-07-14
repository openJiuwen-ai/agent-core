# NativeHarness 异步工具框架（起步版）+ swarmflow 接入

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-26 |
| 范围 | 新增 `harness/async_tools.py`（`AsyncTool` / `AsyncToolRuntime` / `AsyncToolRecord` / `render_result_text`）；`harness/native_harness.py`（`async_tool_runtime` / `launch_async_tool` / `stop` cancel）；`workflow/tool_swarmflow.py`（`SwarmflowTool` 改 `AsyncTool` 子类）、`workflow/observer.py`（`summarize_run`）；`tools/tool_factory.py`、`rails/team_tool_rail.py`、`rails/elements.py`、`rails/team_context.py`、`agent/agent_configurator.py`（装配注入改造）；`agent/team_agent.py`（删 `_launch_swarmflow` / `run_swarmflow_background`）、`agent/state.py`（删 `swarmflow_active` / `swarmflow_tasks`）、`agent/coordination/handlers/workflow.py`（删 `workflow_completed` 叙述）、`i18n.py`（`async_tool.*`，删 `workflow.completed`） |
| 测试基线 | `tests/unit_tests/agent_teams/harness/test_async_tools.py` + `workflow/test_swarmflow_leader.py` + `workflow/test_observer.py` → 13 passed；回归 `test_team_tools` / `test_predefined_team` / `test_hitt` / `test_harness_manifest` / `test_bridge_avatar_setup` / `test_team_policy_rail` → 233 passed, 14 skipped；`harness/` + `workflow/` + `test_team_agent` → 84 passed |
| Refs | `#751` |

## 背景

swarmflow 此前是 leader 的异步工具：`invoke` 调 launcher 后立即返回，编排在
`TeamAgent.run_swarmflow_background` 后台跑。但**最终结果（脚本 `run(args)` 返回值 +
4 层 `WorkflowRun`）被完全丢弃**——leader 只收到 `workflow_started/phase/completed`
三个里程碑文本，看不到工作流产出；失败时只 `team_logger.error`，leader 永远等不到
结束。需求遂升级为：在 native harness 里建一套**通用异步后台工具框架**，照搬
Claude Code 两段式范式，把后续异步工具的路一次性铺平，swarmflow 作为第一个实现。

## 核心架构判断

Claude Code 两段式的根因是 Anthropic API 硬约束：`tool_use` 必须紧跟匹配
`tool_result`，不能挂起——"没有异步 tool_result"，完成结果只能绕过工具协议作为
独立注入式 user 消息回来。

openjiuwen 工具循环**有同样强约束**：`ReActAgent`（`react_agent.py:1379-1471`）→
`ability_manager.execute` 用 `asyncio.gather` 并行、`await tool.invoke()` 同步阻塞
（`ability_manager.py:671,889`）→ 每个 `tool_call` 必产生一个 `ToolMessage`（强配对、
无挂起）。故不能挂起 tool_result，否则整轮 LLM 卡死。Claude Code 的两段式正是这里
需要的范式：**① 工具 `invoke` 立即返回 launched(task_id) 闭合配对；② 完成结果稍后
经注入式消息进下一轮。**

## 关键决策

1. **框架限定 NativeHarness 层，零 TeamAgent 依赖。** `NativeHarness(DeepAgent)` 自带
   `send` / `schedule_auto_invoke_on_spawn_done` / `loop_controller` / `event_handler`。
   框架（`AsyncToolRuntime`）挂在 harness 上；异步工具持 `parent_agent`(NativeHarness)
   引用拿入口（照 `sessions_spawn` 形状）。`TeamAgent.deliver_input` 只是 `harness.send`
   的薄包装——框架直接用 harness 入口绕开 TeamAgent。**拒绝方案**：让 runtime 注入
   `TeamAgent.deliver_input`、挂 `agent_configurator`（把框架绑死在 team 上，违背"限定
   NativeHarness"）。
2. **完成注入复用 `harness.send(text, immediate=False)`，统一不分 active/idle。** IDLE 起
   新轮播报、RUNNING 排 follow-up 不打断用户当前交互（`native_harness.py:370-375`）。比
   `sessions_spawn._complete_session_spawn` 的显式 active→steer / idle→auto-invoke 双
   分支更干净，且天然满足"执行期间 leader 可继续处理其他用户输入"。**拒绝方案**：物理
   复用 task loop 的 `SESSION_SPAWN` 路径（结果写进 DeepAgent 内部 `pending_follow_ups`、
   进 session checkpoint 的 user-bound 语义，与 team 的 db inbox 持久化层混用会污染
   checkpoint 恢复）。
3. **完整结果不截断。** `render_result_text` 完整渲染（str 原样 / dict-list JSON / 其它
   `str()`），token 成本由用户承担（用户明确要求完整）。
4. **swarmflow 第一个实现，不收编 sessions_spawn。** `SwarmflowTool` 改 `AsyncTool` 子类，
   `run_background` 跑 `run_swarmflow` 捕获返回值，结果 = `summarize_run(observer.run)` +
   `render_result_text(返回值)`；phase 中途叙述仍走 `WORKFLOW_PROGRESS`（swarmflow 特有，
   保留），完成/失败走框架经 harness 注入。`workflow_completed` 不再由 `WorkflowHandler`
   叙述。`sessions_spawn` 原样不动。

## 装配链改造

后台执行 + 完成注入全部移入框架（NativeHarness），TeamAgent 退出 swarmflow：
- `agent_configurator` 在 leader+`enable_swarmflow` 时构造 worker-model resolver 闭包
  （`resolve_member_model(ctx.team_spec, ...)`），经 `BuildContext.extras` 的
  `TeamHandleKey.SWARMFLOW_MODEL_RESOLVER` 注入（取代旧 `SWARMFLOW_LAUNCHER`）。
- `team.tool` element provider 传 `messager` + `swarmflow_model_resolver` 给 `TeamToolRail`；
  `TeamToolRail.init(agent)` 把 `agent`(NativeHarness) 作为 `parent_agent` 传入
  `create_team_tools`，后者构造 `SwarmflowTool(parent_agent, messager, team_name,
  model_resolver)`，并以 `swarmflow_model_resolver is None` 作 gate。
- 单实例约束（原 `swarmflow_active`）改由 `SwarmflowTool.invoke` 查
  `parent_agent.async_tool_runtime.has_running("swarmflow")`，不再需要 `TeamAgentState`。

## 已知遗留 / 后续阶段

- 起步版只做最小两段式闭环：**不含**取回工具（TaskOutput）、list/cancel 工具、磁盘
  输出存储、agentId 作用域通知队列、统一全局 ID 体系。
- 框架暂放 `agent_teams/harness/`（team 专属起步），跑通后再**下沉 harness 通用层**让所有
  DeepAgent 可用。
- `state.py` 旧注释曾称 `swarmflow_active` "read by TeamPolicyRail"——实为过时（rail 只读
  静态 `enable_swarmflow`），已随删除一并修正。
- 后续 `F_47` 将 L1 单实例约束升级为可配置 `ConcurrencyGovernor`（多 run 并行），
  `has_running` 仅作 async registry 观测；当前规约见 `S_21_swarmflow-concurrency-governor.md`。
- 后续 formatter 回调机制（`S_20`）：`launch` / `launch_async_tool` 增 `format_completed` /
  `format_failed` 回调（类型 `CompletionFormatter` / `FailureFormatter`，`async_tools.__all__` 导出），
  `run_background` 改返回**原始脚本结果**（不再内部拼接字符串），终态文案由回调注入（swarmflow →
  `swarmflow.completed` / `swarmflow.failed`，含 `run_id` + `summarize_run`）；回调返回 `None` 或未设时
  回退默认 `async_tool.*` 文案（不含 run_id）。swarmflow 构造另注入 `concurrency_governor`（`F_47`，
  经 `agent_configurator` → `TeamHandleKey.SWARMFLOW_CONCURRENCY_GOVERNOR` 装配），装配链参数相应演进。
