# Swarmflow 三层并发治理（ConcurrencyGovernor）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-26 |
| 范围 | `workflow/concurrency.py`、`workflow/engine/admission.py`、`workflow/engine/cap.py`、`workflow/engine/runtime.py`、`workflow/engine/primitives.py`、`workflow/engine/runner.py`、`workflow/tool_swarmflow.py`、`workflow/runner.py`、`workflow/backends/team_worker_backend.py`、`harness/async_tools.py`、`harness/native_harness.py`、`schema/blueprint.py`、`agent/agent_configurator.py`、`rails/team_context.py`、`rails/team_tool_rail.py`、`tools/tool_factory.py`、`agent/coordination/handlers/workflow.py`、`i18n.py`；jiuwenswarm `config_loader.py` |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/` 全量通过；`test_concurrency_governor.py` + `test_swarmflow_leader.py`（含 `test_invoke_release_ticket_when_launch_fails`、`test_swarmflow_tool_allows_up_to_max_workflows`）+ `test_async_tools.py` + `test_worker_backend.py` |
| Refs | SDD-0008；延续 `F_35`（async 两段式）、`F_43`（pause/resume）、`F_44`（worker 命名演进） |

## 背景

放开多 run 并行后，需要同时约束 L1（同时 workflow 数）、L2（单 run 内 `agent()` 并行）、L3（Leader 下全局 agent 总和）。在 `F_35` 异步两段式框架之上，旧版 `SwarmflowTool.invoke` 用 `has_running("swarmflow")` 硬限单 run；engine 内嵌 `Runtime.sem`，无法配置、无法跨 run 协调。本特性将 L1–L3 收敛为 `ConcurrencyGovernor`，engine 改为 `AgentAdmission` 协议注入。

三层 cap 由 `ConcurrencyLimits`（`@dataclass(frozen=True)`，`workflow/concurrency.py`）承载，字段与默认：
`max_workflows: int = 16`（L1）、`agents_per_run: int | None = None`（L2）、`max_agents_total: int = 64`（L3）。
L2 有效 cap 由 `resolve_agents_per_run_cap(agents_per_run, *, cap_override=None) -> int`（`engine/cap.py`，build 期与
运行期共享单一来源）解析，优先级 `cap_override > agents_per_run > min(16, cpu_count-2)`，`max(1, …)` clamp≥1。

## 执行流程

```
SwarmflowTool.invoke
  ├─ script_path 校验
  ├─ governor is None → ToolOutput 失败（未装配）
  ├─ admit_workflow() [L1] → None → 拒绝，无 run_id
  ├─ new_swarmflow_run_id() → wf_{12hex}
  ├─ launch_async_tool(format_completed/format_failed 闭包捕获 run_id)
  │     └─ 异常 → invoke 内 release_workflow(ticket)（幂等），与 finally 互斥
  └─ map_result → swarmflow.launched（run_id + task_id）

run_background (async task)
  ├─ run_swarmflow(..., run_id=, agent_gate=RunAgentAdmission)
  │     └─ engine primitives.agent() → agent_gate.acquire() [先 L2 后 L3，阻塞]
  └─ finally: release_workflow(ticket) [L1 --，幂等 discard]

_relaunch (resume，F_43)
  └─ launch_async_tool(同一 inputs) —— 不二次 admit；复用 inputs 内 ticket/agent_gate
```

**与 journal 正交**：`run_id` 只作进程内身份与 Leader 播报；journal 路径仍由
`(team, session, workflow_name)` 决定（`S_18`），多 run 同名脚本共享同一 journal 前缀。

## 关键决策

1. **L1 计数拒绝，L2/L3 Semaphore 阻塞。** `invoke` 须立即返回 launched 或明确错误；脚本内 `parallel()` 适合阻塞排队。L1 计数由**在飞 ticket 集合**（`_active_tickets: set[int]`）而非裸计数维护——`release_workflow` 用 `discard` 语义，**幂等**：重复 release 同一 ticket 或 release 未知 ticket 均为 no-op，机制级防双 release，不依赖 invoke/finally 互斥约定；集合大小被 `max_workflows` 限制（有界）。
2. **engine 只依赖 `AgentAdmission` 协议**（`engine/admission.py`），Swarmflow 注入 `RunAgentAdmission`（先 L2 per-run、后 L3 global）。engine **不 import** `concurrency.py`。
3. **Governor per-Leader**，与 `AsyncToolRuntime` 作用域一致；`has_running` 保留作 registry 观测，**不作 L1 门禁**。
4. **多 run 身份隔离同批交付**：`run_id` 在 `invoke` admit 后由 `tool_swarmflow.new_swarmflow_run_id()` 生成（`wf_{12hex}`），**只在 invoke 铸造**——`run_background` 只读 inputs 中的 run_id 不重新生成，故并行 run 各自独立；L1 拒绝时不分配 run_id。worker 名 `{run_prefix}-{label_slug}-{n}`（`run_prefix` = 完整 run_id slug 化不截断，`label_slug` = label slug 化；取代 `F_44` 的 `wf-{label}-` 前缀；无 run_id 回退 `wf-{slug}-{n}`；不同 run_id 不碰撞）。Leader 中途 i18n 含 `run_id`（started/phase）。
5. **build 期校验**（`validate_swarmflow_concurrency(limits) -> int`，返回 L2 cap）：共 5 条约束——`max_workflows≥1`、`max_agents_total≥1`、`agents_per_run≥1`（若显式设置）、有效 L2≤L3、`max_agents_total≥max_workflows`；不满足经 `raise_error(StatusCode.AGENT_TEAM_CONFIG_INVALID, reason=...)` 抛 `ValidationError`（与 `blueprint.py` 同层配置校验一致，不抬升配置）。触发点两处，均仅 `enable_swarmflow=True`：① `TeamAgentSpec.build()` 内显式调用（fail-fast）；② `agent_configurator` 构造 Governor 前调用取 `l2_cap`（正常路径只跑一次）。engine 内部 `BackendError` 属 `S_18` 错误边界，不转 StatusCode。
6. **终态文案**：四个对外方法 `map_result` / `format_launched_message` / `format_completed_injection` / `format_failed_injection`（`tool_swarmflow.py`）。`AsyncToolRecord.format_completed/format_failed` 回调（类型 `CompletionFormatter` / `FailureFormatter`，`harness/async_tools.py` 新增导出）+ `swarmflow.*` i18n；`run_background` 只返回脚本原始结果（`summarize_run` 进 completion 闭包，不进 `run_background` 返回值）。回调返回 `None` 或未设时框架回退默认 `async_tool.*` 文案——**默认文案不含 run_id**（仅 tool + result/error），故只有走 `swarmflow.*` 回调的终态才带 run_id，其它 AsyncTool 终态无 run_id。
7. **pause 与 L1 释放**（`F_43`）：pause 停 task，`WorkflowAborted → CancelledError` 触发 `run_background.finally` **立即** release ticket；resume（`_relaunch`）复用同 ticket 但不重新 admit，故 resume 期间不占 L1 槽（resume 的 finally 再 release 为幂等 no-op）。注：F_43 原设计意图为「pause 期间 L1 槽仍被占用」，但现行 finally 对 CancelledError 也会 release——二者在「resume 不重新 admit」上一致。多 run 时 controller 遍历全部 active handle。
8. **BREAKING（engine 内部）**：`Runtime.sem` 移除 → `Runtime.agent_gate`；对外 `run_workflow(cap=)` 仍映射 `cap_override` 供单测。

## 装配链

- `TeamAgentSpec.swarmflow_concurrency`（类型 `ConcurrencyLimits`，`default_factory=ConcurrencyLimits`，
  默认值 `max_workflows=16` / `agents_per_run=None` / `max_agents_total=64`）→ `TeamAgentSpec.build()` 在
  `enable_swarmflow=True` 时调 `validate_swarmflow_concurrency`（fail-fast）。
- `agent_configurator`（**仅** `role=LEADER` 且 `enable_swarmflow`）：`l2_cap = validate_swarmflow_concurrency(spec.swarmflow_concurrency)`
  取返回值，构造 `ConcurrencyGovernor(spec.swarmflow_concurrency, agents_per_run_cap=l2_cap)`（per-Leader 单例，
  与 `AsyncToolRuntime` 同作用域）+ `swarmflow_model_resolver`，经 `inject_team_handles` 写入
  `SWARMFLOW_CONCURRENCY_GOVERNOR` / `SWARMFLOW_MODEL_RESOLVER`。
- `build_team_tool_rail` → `get_swarmflow_concurrency_governor(context)` → `TeamToolRail(swarmflow_concurrency_governor=)`
  → `_build_tools` → `create_team_tools(concurrency_governor=)` → `SwarmflowTool(concurrency_governor=...)`；
  **swarmflow 工具 gate 仍为 `model_resolver is not None`**（governor 与 resolver 同批注入，未单独 gate governor）。
- `governor is None` 时 `invoke` 返回 `Swarmflow concurrency governor is not configured`。
- jiuwenswarm：`config_loader` 可选透传 `swarmflow_concurrency` YAML 块。

## 拒绝的方案

| 方案 | 理由 |
|------|------|
| L1 继续用 `has_running` | 只能表达有/无，不可配置 cap |
| workflow 层 Semaphore 排队 | `invoke` 挂起，Leader 无法决策 |
| build 期抬升 L3 掩盖配置错误 | 运维难以发现错误配置 |
| `run_id.py` 独立模块 | 仅一处调用，合并进 `tool_swarmflow.py` |
| resume 二次 `admit_workflow` | 会重复占 L1；resume 复用 pause 前 inputs |

## 验证

- `test_concurrency_governor.py`：admit/release、L2/L3 跨 run、build 校验、engine fallback、
  release 幂等（`test_release_is_idempotent_for_same_ticket`）、未知 ticket no-op（`test_release_unknown_ticket_is_noop`）
- `test_swarmflow_leader.py`：L1 满额拒绝、至多 `max_workflows` 路、launch 失败 release ticket、`run_id` 启动回执与 handler 播报
- `test_async_tools.py`：自定义 `format_completed` / `format_failed`
- `test_worker_backend.py`：`run_id` 成员名前缀

## 已知遗留

- `ConcurrencyGovernor.snapshot()` 未暴露 Monitor / TUI HTTP API。
- L3 长等待无 debug 日志（阻塞在 sem 上时无可观测排队深度）。
- pause 多 run 并发场景 e2e 仅 controller 逻辑覆盖，未专门压测 L1 与 pause 交错。
- **i18n run_id 覆盖缺口**：`workflow.human_prompt` / `workflow.human_replied`（人工交互）与
  `async_tool.*`（无回调时的默认回退）不含 `{run_id}` 占位符，多 run 并行时这几类消息无法从文案区分来源 run。
  `WorkflowHandler` 对 `workflow.started` / `workflow.phase` 传 `run_id=payload.run_id or "?"`（缺失回退 `"?"`），
  对 `human_prompt` / `human_replied` **故意不传 run_id**——人工交互靠 `correlation_id` 路由（corr 跨 resume 稳定、
  与人一一对应），run_id 在此无叙事价值；`async_tool.*` 是无回调时的默认回退，swarmflow 已用 `swarmflow.*`
  覆盖带 run_id，其它 AsyncTool 暂无 run_id 概念。可区分来源 run 的仅：启动 `swarmflow.launched`、
  中途 `workflow.started`/`workflow.phase`、终态 `swarmflow.completed`/`swarmflow.failed`。
