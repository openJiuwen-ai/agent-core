---
title: 预派 PENDING 任务提交计划被 claim_task 的 CAS 拒绝，任务永久 pending
author: mulderlover
author_email: huyun8@huawei.com
id: B_20261109_1731_preassigned-submit-plan-cas
type: B
modules:
- openjiuwen/agent_teams
files:
- openjiuwen/agent_teams/tools/task_manager.py
- tests/unit_tests/agent_teams/test_task_manager.py
designs: []
design_note: 缺陷修复，无独立设计；按现有 plan gate 状态机补齐已分配 PENDING 分支。
---

# 预派 PENDING 任务提交计划被 claim_task 的 CAS 拒绝，任务永久 pending

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-11-09 |
| 严重程度 | 中：plan_mode 成员无法提交计划，任务永久停在 PENDING |
| 范围 | `openjiuwen/agent_teams/tools/task_manager.py`、`tests/unit_tests/agent_teams/test_task_manager.py` |
| 测试基线 | `tests/unit_tests/agent_teams/test_task_manager.py` 88 PASS；`tests/unit_tests/agent_teams` 4420 PASS（11 个 Windows 本机既有失败，develop 基线同样失败） |
| 关联 spec | `S_17_team-plan.md` |

## 现象

PLAN_MODE 成员对创建时已预派（`assignee=member`）且仍是 `PENDING` 的任务调用 `submit_plan`，稳定返回：

```text
{'success': False, 'task_id': 't1', 'message': 'Failed to reserve task for planning'}
```

任务保持 `PENDING + assignee=member`，无法进入 `PLANNING`，Leader 也收不到计划请求；之后审批、执行、完成都不会发生。

## 根因

`TeamTaskManager.submit_plan()` 在 `task.status == PENDING` 分支只调用 `TaskDao.claim_task(..., to_status=PLANNING)`。`claim_task` 的 CAS 条件是 `assignee IS NULL AND status = pending`，而预派任务由 `add_graph(assignee=...)` 直接落成 `PENDING + assignee=member`，因此 CAS 永远匹配 0 行。

同一文件的 `TeamTaskManager.claim()` 已经为 `assignee == member_name and status == PENDING` 使用 `TaskDao.start_task(..., to_status=IN_PROGRESS)`（CAS 条件为 `assignee = member AND status = pending`）；`submit_plan()` 缺少对应分支。永久失败发生在 autonomous dispatch：autonomous 也允许预分配，依赖解除后的任务同样会回到 `PENDING(assignee)`；scheduled dispatch 的 `PENDING(assignee)` 由调度器启动，成员不应在 `submit_plan` 中自行开工。

## 决策

在 `submit_plan` 的 PENDING 分支按 dispatch mode 和 `assignee` 分流：

- `scheduled`：不启动任务，返回 `status=pending` 的待调度结果，不写计划记录、不发 `TaskPlanRequestEvent`；`PENDING(assignee)` 由调度器 `start_task` 并发送开工交接。
- `autonomous`：
  - `assignee` 为空：保持 `claim_task(..., to_status=PLANNING)`；
  - `assignee == member_name`：调用 `TaskDao.start_task(..., to_status=PLANNING)`，与 `claim()` 的已分配分支一致，不发布 scheduler 的 `TaskStartedEvent`。
- `resume_pending` 是 scheduled 成员子任务恢复标记（F_128），由调度器 `start_task` 直接恢复 `IN_PROGRESS`，不在 `submit_plan` 内处理。

不修改 `claim_task` 的全局契约，避免影响其他 self-claim 调用点；也不新增 DAO 方法。

## 拒绝的方案

- **放宽 `claim_task` 允许 `assignee = member`**：会改变所有调用点（包括公共认领池）的语义，风险大于收益。
- **在 `submit_plan` 前先 `reassign` 或 `reset`**：会把任务短暂放回公共池或改变 owner，产生额外事件与竞态。
- **在 scheduled 模式由 `submit_plan` 启动任务**：会绕过调度器的开工扫描与开工交接，任务可能停在 `PLANNING`/`IN_PROGRESS` 而没有 `scheduler_task_start*`，并绕过一活跃守卫；scheduled 成员应等待调度器开工。
- **只依赖 scheduled 的 scheduler 先 `start_task`**：autonomous 没有 scheduler，预派/依赖解除路径仍会失败。

## 验证

- 新增 `test_plan_mode_submit_plan_starts_preassigned_pending_task`：autonomous 下预派 `PENDING(assignee)` 经 `submit_plan` 进入 `PLANNING`。
- 新增 `test_plan_mode_submit_plan_claims_unassigned_pending_task`：未分配 `PENDING` 仍走 `claim_task` 进入 `PLANNING`。
- 新增 `test_plan_mode_submit_plan_scheduled_waits_for_scheduler`：scheduled 下预派 `PENDING(assignee)` 返回 `status=pending`，任务保持 `PENDING`，不发布 `TASK_PLAN_REQUEST` / `TASK_STARTED`。
- 新增 `test_submit_plan_renders_pending_dispatch_message`：待调度结果渲染为等待调度器开工，不显示为已提交计划。
- `tests/unit_tests/agent_teams/test_task_manager.py`：90 PASS。
- `tests/unit_tests/agent_teams`（排除缺少 `prompt_toolkit` 的 `cli` 用例）：4762 PASS、35 SKIP、2 XFAIL；11 个 Windows 本机符号链接/路径/SQLite 相关失败（如 `WinError 1314`），与本次改动无关。
