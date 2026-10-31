---
title: 看板清空的收尾提示让 leader 停止建任务，且之后再清空不再提示
author: alan_cheng
author_note: 按 Git 最早可追溯的文档新增提交署名；不代表代码 owner
id: B_09_all-done-prompt-stalls-remaining-work
type: B
modules:
- openjiuwen/agent_teams
files: []
designs: null
design_note: 历史设计关联待核实，不等同于没有关联设计
author_email: chengshuo5@huawei.com
---

# 看板清空的收尾提示让 leader 停止建任务，且之后再清空不再提示

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-27 |
| 严重程度 | 中：剩余工作停在 leader 口头承诺里，需用户提醒才继续 |
| 范围 | `i18n.py` 的 `scheduler.leader_all_done` / `dispatcher.all_done_*` 文案；`TeamScheduler` 的 `_all_done_announced` |
| 测试基线 | `test_team_scheduler.py` + `test_completion_handoff_wording.py` + `test_mode_handlers.py` + `test_task_manager.py` + `test_member_subtasks.py` + `test_task_revisions.py` 262 PASS |
| 关联 spec | `S_22_scheduling-runtime.md` |
| 关联 feature | `F_120_autonomous-completion-summary-and-note.md`、`F_128_scheduled-member-subtasks.md` |

## 现象

2026-10-27 一次调度模式的迁移会话：15:47:47 最后一个未终结任务（首轮代码检视）完成，看板 31 个任务全部
终结，调度器给 leader 投了收尾提示「任务看板已全部终结……请汇总团队执行结果，向用户交付最终结论」。
但检视刚判出一个阻断项，工作并没有完成。随后 leader：

- 用 `send_message` 让实现者「同一任务内提交，不新开任务」修阻断项，而那个实现任务早已 completed；
- 用 `spawn_expert` 拉起第二轮检视者与验证者，并回复「已开新一轮独立检视/验证」，但没有 `create_task`。

调度模式下只有看板任务会被派发，新成员一直空闲。直到 15:55 用户提示「任务好像没有创建？」，leader 才建出
第二轮的两个任务。

## 根因

1. **文案把「看板清空」说成「工作结束」。** 三条收尾文案都只给出「汇总、交付」（或关闭成员）一条出路，没有
   提醒 leader 先对照用户目标检查剩余工作，也没有说剩余工作要以任务形式出现在看板上。leader 读到「交付最终
   结论」后改走消息和拉起成员，绕开了看板。
2. **收尾只发一次。** `_all_done_announced` 只在 `activate()` 复位。S_22 原先论证「收尾后不会再出现任务」只
   考虑了成员子任务（F_128），漏掉了 leader 在收尾之后自己建的任务：这些任务全部完成时 leader 收不到第二次
   收尾，团队会停在没有人汇总的状态。

## 决策

- **改写三条收尾文案**（中英两份）：先说明「看板上已没有未终结任务，这不代表目标已经达成」，再要求对照用户
  目标检查返工、新一轮检视或验证、下一阶段等剩余工作，有就 `create_task`；调度模式那条另写明 send_message 或
  拉起成员不会让工作进入看板：调度器看不到它、不派发验收、完成后也不提醒，没有任务的成员会一直空闲。不写成
  「消息不会让工作开始」——消息确实会拉起成员干活（本次会话里实现者就靠一条消息交出了新候选），这种说法
  与 leader 观察到的事实相反，反而削弱整段提示。确认目标达成后才汇总交付（临时团队再关闭成员）。原有
  「无需发送收尾或待命通知」一句保留。
- **看板重新出现未终结任务时复位**：`_scan` 开头调 `_rearm_all_done`，在 `_all_done_announced` 为真时经
  `TeamTaskManager.has_open_tasks()`（复用 DAO 的 `count_tasks_terminality`，一次聚合 COUNT，与看板清空事件、
  team-idle 判定同一口径）探一次看板，有未终结任务就把标志清回 False。之后
  这批任务完成、看板再次清空时，终态摘要（或 `TASK_LIST_DRAINED`）照常再发一次收尾。标志为假时不探看板；
  收尾后看板一直清空时，每次扫描多一条 COUNT 查询，不加载任务行。
- **晚到的清空事件不发收尾**：`_announce_all_done` 发送前同样经 `has_open_tasks()` 复查看板。清空事件可能在
  leader 已经建了新任务之后才被事件循环处理，此时不复查就会告诉 leader「看板上已没有未终结的任务」，而看板上
  明明有任务；复位之后「收尾后再开新任务」成了预期路径，这个时序也更常见。复查排在标志检查之前：重开之后、
  下一次扫描复位之前，标志可能仍为真，这个窗口里的晚到事件同样不能触发只跑一次的团队终态 review feedback。
- **看板判定只留一个口径**：`TeamTaskManager.board_counts()` 返回 `(total, non_terminal)`，`has_open_tasks()`
  基于它实现；`_digest_task_done` 也改用它取总数与剩余数，不再 `list_tasks` 逐行计数、不再自带终态元组。终态
  集合以后有变化时，三处判定（终态摘要、清空事件、复位）不会分叉。

## 拒绝的方案

- **按任务数去重**（记下上次收尾时的任务总数，变大就重发）：取消一个旧任务再建一个新任务时总数不变，会漏发；
  看板是否还有未终结任务才是正确的条件。
- **在 `TASK_CREATED` 事件上复位**：leader 自己建任务的事件会被自过滤，只经 `SCHEDULER_SCAN` 回声可见；放进
  扫描就能覆盖所有触发源，也符合「调度器只理解看板」（S_22 不变量 4）。
- **去掉收尾提示**：leader 仍需要一个「看板已清空」的时刻来决定汇总还是继续，问题出在文案和去重条件，不在
  提示本身。

## 验证

- `test_team_scheduler.py::test_board_reopened_after_wrap_up_announces_again`：收尾后新建任务并完成，第二次
  收尾带新的任务总数；去掉 `_rearm_all_done` 调用后该用例失败。
- `test_team_scheduler.py::test_scan_on_a_drained_board_keeps_the_wrap_up_once`：看板保持清空时，再扫描或再收到
  `TASK_LIST_DRAINED` 都不会重复收尾。
- `test_team_scheduler.py::test_late_drained_event_on_a_reopened_board_sends_no_wrap_up`：清空事件到达时看板已
  有新任务，不发收尾，且该任务完成后正好收尾一次（晚到事件没有吞掉真正的收尾）；去掉复查后该用例失败。
- `test_task_manager.py::test_has_open_tasks_counts_only_non_terminal_tasks`：空看板、只剩 completed / cancelled
  为 False，有 pending / blocked / in_review 任务为 True，`board_counts()` 计数正确。
- `test_completion_handoff_wording.py::test_all_done_prompts_ask_for_remaining_work_before_wrapping_up`：三条
  文案中英两份都带「不代表目标已达成」和 `create_task`。
- `test_team_scheduler.py` / `test_completion_handoff_wording.py` / `test_mode_handlers.py` / `test_task_manager.py` /
  `test_member_subtasks.py` / `test_task_revisions.py` 共 262 PASS。

## 已知遗留

- 文案只能降低误判，不能保证 leader 一定建任务；用真实模型验证是否还会在收尾后改走消息，需要另跑会话观察。
- `_team_review_feedback_dispatched`（团队终态 review feedback）仍然只在 `activate()` 复位，看板重新清空时不会
  再跑一次团队汇总演进。这是有意保留的边界，本次不改。
- `_rearm_all_done` 的 COUNT 查询出错时整次扫描作废，与其余扫描出错的处理一致（`on_event` 吞异常、下次唤醒重试），
  不单独兜底。
