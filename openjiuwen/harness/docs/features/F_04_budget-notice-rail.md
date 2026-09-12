# 预算告警 Rail（BudgetNoticeRail）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-12 |
| 状态 | **已实现** |
| 范围 | `harness/rails/budget_notice_rail.py`（新）、`harness/prompts/sections/budget_notice.py`（新）、`harness/prompts/sections/__init__.py`（`SectionName.BUDGET_NOTICE`）、`harness/schema/stop_condition.py`（`BudgetLimit` + `StopConditionEvaluator.budget()`）、`harness/task_loop/loop_coordinator.py`（`token_usage` / `elapsed_seconds` / `budget_limits()`）、`harness/manifest/harness_elements.py`（`core.budget_notice` 元素）、`harness/rails/__init__.py`（导出） |
| 测试基线 | `python -m pytest tests/unit_tests/harness/rails/test_budget_notice_rail.py -q --no-cov` → **13 passed** |
| Refs | #1348 |
| 关系 | 读 S_03 的停止条件求值器与 `LoopCoordinator`；section 归 S_06；声明式装配归 S_12；rail 契约归 S_04 |

## 背景

DeepAgent 的 task loop 由**三个**硬预算共同约束，全部落在 `schema/stop_condition.py` 的
求值器链上：`MaxRoundsEvaluator`（轮次）、`TokenBudgetEvaluator`（累计 token）、
`TimeoutEvaluator`（墙钟）。`LoopCoordinator.should_continue()` 用 OR 语义汇总它们，
先到者停。

宿主侧（jiuwenswarm）原先自建了一个 `IterationBudgetRail`：它只对**轮次**告警，并且
把 `max_iterations` / `budget_warning_threshold` 抄一份到自己（宿主）配置里，与 loop 真正
生效的上限是两条独立的数；`max_iterations` 一旦在某处改了而告警阈值没跟着改，就会漂移。
评审提出的两个问题正是这个缺口：

1. 只告警轮次，不告警 token（以及时间）——而 loop 明明三个都管。
2. rail 本身与宿主无关（只依赖 `DeepAgentRail` / `PromptSection` / `LoopCoordinator`），
   属于通用能力，应上收到 agent-core。

## 决策

1. **预算上限的唯一来源是 loop 的求值器链。** 新增
   `StopConditionEvaluator.budget() -> BudgetLimit | None`（`stop_condition.py`），
   三个资源型求值器分别返回 `rounds` / `tokens` / `seconds` 的硬上限；`LoopCoordinator`
   聚合为 `budget_limits()`，并暴露 `token_usage` / `elapsed_seconds`。`BudgetNoticeRail`
   **只读**这些值，自身不携带任何上限，因此告警与"真正会让 loop 停下的数"永远一致。
2. **rail 只提示，不参与停止。** 停止由求值器负责；rail 只在 `before_model_call` 注入一段
   系统提示，让模型主动收敛（完成或给出可用的部分结果），避免被硬停。
3. **多资源、可配阈值。** 对每个预算按 `remaining <= threshold` 判断：轮次支持绝对剩余
   （`round_remaining`）或比例（`round_ratio`），token/时间用比例（`token_ratio` /
   `time_ratio`，默认 15%，轮次默认 20%）。只列出接近上限的资源。
4. **声明式装配。** 注册 `@harness_element(ElementKind.RAIL, name="core.budget_notice")`
   与 `BudgetNoticeInput`，宿主可只按名字 + 参数装配；同时 rail 也可直接构造（宿主自建
   rails 列表时用）。
5. **i18n 归 sections。** 文案放 `prompts/sections/budget_notice.py` 的
   `CN` / `EN` 模板，`build_budget_notice_section(language, notices)` 产出
   `PromptSection(name=SectionName.BUDGET_NOTICE, ...)`，与 `heartbeat` 等 section 一致。
6. **修正 `TaskCompletionRail` 覆盖语义。** `enable_task_loop` 时，`DeepAgent` 只在调用方
   **未提供** `TaskCompletionRail` 时才注入默认实例。此前无条件追加，导致用户/宿主传入的
   `TaskCompletionRail`（带 `max_rounds`）与默认实例并存、且默认实例因排在最后而生效——
   注释宣称的"可覆盖"实际不成立。修正后宿主可传入带 `max_rounds`/`timeout_seconds` 的实例，
   让循环预算真实落地，同时供本 rail 通过 `budget_limits()` 读取。

## 数据结构

- `BudgetLimit(kind, limit)`：`kind ∈ {rounds, tokens, seconds}`，`limit` 为硬上限。
- `LoopCoordinator.budget_limits() -> tuple[BudgetLimit, ...]`：跳过返回 `None` 的谓词型
  求值器。
- `BudgetNoticeRail` 参数：`enabled` / `round_remaining` / `round_ratio` / `token_ratio` /
  `time_ratio`。
- 每次 `before_model_call`：读 limits + usage → 生成 `notices` → `remove_section` 后按需
  `add_section`；`before_invoke` / `uninit` 负责清理，保证跨 invoke 不残留。

## 拒绝的方案

1. **把 jiuwenswarm 的 `IterationBudgetRail` 原样搬进 agent-core。** 只解决"位置"，
   不解决"只 rounds + 自带上限漂移"两个实质问题，属于化妆式迁移。
2. **让 rail 自带 `max_iterations` / 阈值配置。** 这正是原实现的漂移根源；并行配置不可取。
3. **让 rail 参与停止决策（自己 `request_abort` / 改 evaluator）。** 与求值器职责重叠，
   破坏"停止归 evaluators"的边界。
4. **每个资源一个独立 rail。** 三个 section 竞争同一提示位、重复生命周期，收益为零。
5. **只保留 rounds 告警。** 评审明确指出的缺口；且 token/时间预算同样会被硬停，需要同样的
   "收敛提示"。

## 验证

- `tests/unit_tests/harness/rails/test_budget_notice_rail.py`：**13 passed**，覆盖
  - `budget()` 在三个资源型求值器上的取值、谓词型返回 `None`；
  - `LoopCoordinator.budget_limits()` 聚合与 usage 访问器；
  - section 的 i18n 渲染与空输入返回 `None`；
  - rail 在 token/轮次接近上限时注入、健康时不注入、`enabled=False` 不注入、
    预算恢复/`before_invoke`/`uninit` 时清理。
- 导入与端到端构造冒烟（`LoopCoordinator` + 三个求值器 + section 渲染）。

## 已知遗留

- 默认 `TaskCompletionRail.build_evaluators()` 未接 `TokenBudgetEvaluator`；token 告警
  只有在宿主显式配置 token 预算时才出现。后续可让 `TaskCompletionInput` 暴露 `max_tokens`
  并在构建求值器时接线（本 feature 未改默认停止条件，避免影响既有行为）。
- 宿主（jiuwenswarm）侧的配置映射、`config.yaml` 键与文档在另一仓库落地。jiuwenswarm
  通过传入 `TaskCompletionRail(max_rounds=max_iterations)` 让轮次预算真实生效；token/时间
  预算目前无宿主配置来源，故仅轮次告警。
