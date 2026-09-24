# 团队成员的总预算 Rail（TaskCompletion + BudgetNotice）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-14 |
| 范围 | `openjiuwen/agent_teams/agent/agent_configurator.py`（`_task_loop_budget_rail_specs` + `setup_agent` 注入） |
| 测试基线 | `python -m pytest tests/unit_tests/agent_teams/agent/test_agent_configurator.py -q --no-cov` → **4 passed**；`tests/unit_tests/agent_teams/test_harness.py` + `test_team_harness_integration.py` → 合计 **28 passed** |
| Refs | #1348 |

## 背景

agent-core 上收了通用的 `BudgetNoticeRail`（[[F_04]]，`openjiuwen/harness/docs/features/F_04_budget-notice-rail.md`）：
它在模型调用前读取 task loop 的**真实**预算（`LoopCoordinator.budget_limits()`），在接近上限时注入
一段"收敛"系统提示。单 agent 宿主（jiuwenswarm）通过传 `TaskCompletionRail(max_rounds=max_iterations)`
把外层轮次预算落地，`BudgetNoticeRail` 才有东西可读。

团队路径是另一条装配道：`AgentConfigurator.setup_agent` **对所有本地成员强制
`enable_task_loop=True`**（`agent_configurator.py` 的 `build_spec` update），但 `team_rail_specs`
里从未注入 `core.task_completion` / `core.budget_notice`。后果：

1. 每个团队成员的**外层 task loop 无轮次上限**——agent-core 只在调用方未提供
   `TaskCompletionRail` 时自动注入一个**无 `max_rounds`** 的默认实例；
2. 没有 `BudgetNoticeRail`，即使有预算，成员也不会在耗尽前收到收敛提示；
3. 单 agent 修好了，团队没修——同一个框架两条装配道行为不一致。

## 决策

1. **成员的轮次上限 = 它自己的 `max_iterations`。** `setup_agent` 注入
   `RailSpec(type="core.task_completion", params={"max_rounds": agent_spec.max_iterations})`。
   团队成员的 `max_iterations` 本来就是它底层 ReAct 的上限；用它同时作为外层 task loop
   的轮次上限，与单 agent 宿主的 `max_rounds=max_iterations` 选择一致。
2. **成员同样获得预算告警。** 注入 `RailSpec(type="core.budget_notice")`，阈值走 rail 默认
   （轮次按剩余比例 20%）。团队 spec 目前没有 budget 配置面，默认值即可。
3. **不重复挂载。** 只注入 `base_rails`（blueprint 用户声明）里**没有**的同类型 rail——
   一个 blueprint 若自带 `core.task_completion` / `core.budget_notice`，以它的为准。
4. **token / 时间上限保持 opt-in。** 团队侧不注入 `max_tokens` / `timeout_seconds`；与
   单 agent 宿主同一策略（静默的 token/时间硬上限比无上限更危险）。
5. **抽成可测 helper。** 逻辑落在 `AgentConfigurator._task_loop_budget_rail_specs(agent_spec)`，
   `setup_agent` 只负责去重后并入 `team_rail_specs`，单测直接打 helper。

## 拒绝的方案

1. **在 `DeepAgent` 里把默认 `TaskCompletionRail` 自动接上 `config.max_iterations`。**
   会改变所有宿主（含不经配置器的低层 DeepAgent）的既有行为；团队问题应在团队装配层解决，
   而不是把默认语义变成"max_iterations 也是外层轮次上限"。
2. **新增 `core.team.task_completion` / `core.team.budget_notice` 团队元素。**
   这两个 rail 与团队无关，是 harness 通用能力；团队侧直接复用 harness 元素，避免同能力两份声明。
3. **只注入 `BudgetNoticeRail` 不注入 `TaskCompletionRail`。** 没有 `max_rounds` 时
   `budget_limits()` 里没有轮次项，rail 无预算可读，等于没修。
4. **把阈值/上限做成团队 spec 字段（本轮）。** 团队侧没有真实的配置来源，先给默认值；
   等有需求再走 `TeamAgentSpec` 扩字段（扩 Spec 才是设计）。

## 验证

- `tests/unit_tests/agent_teams/agent/test_agent_configurator.py`：新增 2 例，验证
  `_task_loop_budget_rail_specs` 把 `max_iterations` 映射成 `max_rounds`、并带
  `core.budget_notice`；缺 `max_iterations` 时降级为 `None`（rail 默认不设上限）。
- `tests/unit_tests/agent_teams/agent` + `test_harness.py` + `test_team_harness_integration.py`
  合计 28 passed（另有一批 **既存**失败：`opentelemetry.exporter` 缺失、review-feedback
  evolution 用例，与本次改动无关，改动前后一致）。
- `ruff check`：改动文件无新增问题（既有 I001 不属本次改动）。

## 已知遗留

- 团队 spec 没有 `max_tokens` / `timeout_seconds` / 告警阈值的配置面，只能吃 rail 默认值；
  需要时按"扩 Spec"路径补。
- swarmflow worker 走 `swarmflow_worker_base_spec` 另建，不经本 helper，未覆盖其 task-loop
  预算（本轮只覆盖 `AgentConfigurator` 装配的本地成员）。
- 端到端（真实团队跑满轮次触发 notice）未做，依赖单测与装配契约。
