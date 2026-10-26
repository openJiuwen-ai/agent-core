# Swarmflow verify/fork 结构化进度事件（VERIFY_STARTED/COMPLETED + fork 父标识）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-18 |
| 范围 | `workflow/engine/progress.py`（ProgressKind + 事件字段）、`workflow/engine/primitives.py`（verify() 事件发射、AgentSession fork 父标识）、`schema/events.py`（WorkflowProgressTeamEvent +7 字段）、`workflow/tool_swarmflow.py`（字段桥接）、测试 `test_verify_fork_progress_events.py`（新增） |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/` 离线 UT（含新增 7 例） |
| Refs | #751（SDD-0017 verify / SDD-0014 fork 的可观测性补全） |

## 决策

1. **新增 `VERIFY_STARTED` / `VERIFY_COMPLETED` 两种事件**（`VERIFY_SETTLED` 为 `verify_settled` 旧事件名的 back-compat 别名，消费端路由到同一 handler），不新建事件通道：`WorkflowProgressEvent` 加 7 个 verify 专有字段（`verify_reviewers` / `verify_verdict` / `verify_threshold` / `verify_votes` / `verify_reviewer_labels` / `verify_reviewer_roles` / `verify_id`），加法兼容。STARTED 在 reviewer 扇出前发（名册 + 阈值），COMPLETED 在计票后发（verdict + 逐票明细）。journal 重放时事件按原序重发，下游按 label 幂等折叠。

2. **COMPLETED 的每票带 `name`，镜像 `_reviewer_call` 的 label 规则**（`reviewer.label or f"{base}-{i}"`），并携带 `agent_id` / `role`——消费端凭 name 与 reviewer 节点 label 对齐、凭 agent_id 与节点 id 对齐。decision 从引擎内部的 bool 映射回 `"pass"/"fail"`（None = 未投/畸形）。

3. **fork 父子关系用字段不用新事件**：fork() 时取父的 `self._label` 存入 `_parent_session_id`，fork 子会话每次 `send()` 的 `AGENT_STARTED` 携带 `parent_session_id`。父无 label 时为 None，前端不画边。

4. **团队事件桥接逐字段透传**：`WorkflowProgressTeamEvent` 加同名 7 字段，`tool_swarmflow._build_progress_message` 显式搬运（该桥是逐字段构造，漏改即字段被静默丢弃）。

## 拒绝的方案

- **verify 发独立 VOTE 事件（每票一条）**：票已在 reviewer 自己的 AGENT_COMPLETED.outcome 里，再发一条是重复通道；COMPLETED 聚合一次携带全部票，事件量 O(轮次) 而非 O(票数)。
- **`parent_session_id` 传 member_name**：不进快照的 agent 字段，前端无从解析；label 已满足跨层对齐。
- **COMPLETED 携带聚合 feedback 全文**：feedback 是逐票 feedback 的拼接（`_aggregate_feedback`），票里已有，不重复传输。

## 验证

- 离线 UT（`test_verify_fork_progress_events.py`，MockBackend）：STARTED/COMPLETED 各恰一次且有序；votes 的 name 与 reviewer 节点 label 对齐（含 `base-i` 回退）；SKIP reviewer → verdict=None + `voted=False`；fork 子会话 AGENT_STARTED 带 `parent_session_id` 而父/普通 session 恒为 None。
