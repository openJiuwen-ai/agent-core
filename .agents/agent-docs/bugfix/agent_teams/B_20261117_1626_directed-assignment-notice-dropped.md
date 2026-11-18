---
title: 定向指派/改派的通知被接收端 pending 判据丢弃，新 owner 永不唤醒
author: xiajing
author_email: xiajing22@huawei.com
id: B_20261117_1626_directed-assignment-notice-dropped
type: B
modules:
- openjiuwen/agent_teams
files:
- openjiuwen/agent_teams/agent/coordination/handlers/task_board.py
- tests/unit_tests/agent_teams/agent/test_directed_assignment_notice.py
designs: []
design_note: 缺陷修复只改消费端投递判据，不新增契约与机制；依据见 S_03（TASK_CLAIMED 定向投递）与 jiuwenswarm 问题 #6719 / #6720。
---

# 定向指派/改派的通知被接收端 pending 判据丢弃，新 owner 永不唤醒

## 现象与影响

autonomous（自主）团队里，leader 用 `update_task(assignee=...)` 把任务交给成员后，被指派成员不再醒来：没有新一轮 `task_iteration`、没有 `processing N unread messages`，任务永久停在 `in_progress`，团队轮次再也收不了尾，前端发送按钮一直停在“停止”态，直到会话被删除或 360s 静默看门狗判死。

两条触发路径同根因：

- **定向指派**：leader 指派一个尚未被认领的任务（`TeamTaskManager.assign()`）。
- **改派运行中任务**：leader 把一个已经在跑的任务转交给另一名成员（`TeamTaskManager.reassign()`）。

对应 jiuwenswarm 问题 #6719（指派）与 #6720（改派）。

## 根因

1. `assign()` 在发布 `TASK_CLAIMED` **之前**就把任务 CAS 到 `in_progress`；`reassign()` 保持任务原状态（`TASK_REASSIGNABLE_STATUSES` 含 `IN_PROGRESS`）。
2. 自主模式 `TaskBoardHandler.on_task_claimed` 的自身分支只对 `status == "pending"` 投递通知，其余状态直接 return（只落一行 DEBUG）。
3. 于是事件的唯一接收方拿到的是一个“已进入工作态”的任务，被判据丢弃；旧 owner 的 `TASK_REVOKED` 没有这道判据，能正常收到并停手，形成“旧人停了、新人没被叫醒”的空档。
4. 自主模式没有调度模式那样的邮箱交接补发（那是调度器职责，见 B_06），唯一兜底是成员自身 idle 满 `stale_claim_idle_timeout`（默认 600s）后的 stale 自催，远慢于一轮对话可接受的时延，前端/用例的静默看门狗会先把本轮判死。

## 决策

- 投递判据按任务状态分流，**只放宽到“自己名下的运行中任务”**：
  - `pending`：保持原通知（带标题与正文，指令为 `claim_task(status=claimed)`）。
  - `in_progress` 且 `assignee` 就是收件成员：投递**既有**的 owner-nudge 模板 `dispatcher.stale_claim_self`（“你已认领的任务…请继续推进…”），它不含 claim 指令。
- 投递判据抽成 `_is_announceable()`；`ScheduledTaskBoardHandler` 覆写 `_announces_entered_assignments()` 返回 False。
- `observe_revision` 只对 `pending` 通知与 human-agent 分支记录（owner-nudge 不带正文，成员自行 `view_task` 读取）。
- `pending` 通知只投给任务**当前** `assignee`：`assign()` 先写 assignee 再发事件，事件若输给一次更晚的交接，旧 owner 不能再被唤醒（无 assignee 的行保持原行为）。
- 任务已在执行时，通知以 `use_steer=False` 投递：收件人正在跑一轮对话的话，消息排在它后面，不打断当前轮次。

## 验证

- 新增 `tests/unit_tests/agent_teams/agent/test_directed_assignment_notice.py`（10 例）：改派运行中任务、普通指派、`pending` 不回归、运行态 nudge 不 steer、`pending` 过期指派不投递、`blocked`/终态静默、过期通知（任务已不归自己）不投递、调度模式不投递进入态、调度模式 `pending` 照常投递。
- 回归：`tests/unit_tests/agent_teams/agent/test_mode_handlers.py`、`tests/unit_tests/agent_teams/agent/test_team_scheduler.py` → 97 passed / 0 failed。
