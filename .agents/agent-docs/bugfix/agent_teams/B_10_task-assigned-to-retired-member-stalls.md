---
title: 任务派给已退役的任务级成员后整夜无人执行
author: alan_cheng
author_note: 按 Git 最早可追溯的文档新增提交署名；不代表代码 owner
id: B_10_task-assigned-to-retired-member-stalls
type: B
modules:
- openjiuwen/agent_teams
files: []
designs: null
design_note: 历史设计关联待核实，不等同于没有关联设计
author_email: chengshuo5@huawei.com
---

# 任务派给已退役的任务级成员后整夜无人执行

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-28 |
| 严重程度 | 高：任务停在 `in_progress`、负责人不存在，没有任何检查报警，团队停摆约 10 小时直到用户手动介入 |
| 范围 | `tools/tool_task.py`（指派校验）、`tools/team.py`（`startup` / `wake_member` / `list_retired_members`）、`tools/database/member_dao.py`（`list_shutdown_members`）、`tools/tool_message.py`（直接收件人唤醒、广播）、`agent/coordination/handlers/message.py` / `agent/team_agent.py`（广播拉起）、`agent/scheduling/scheduler.py`（跳过退役负责人 + 升级 leader）、`i18n.py` |
| 测试基线 | `tests/unit_tests/agent_teams/` 4115 passed / 36 skipped / 2 xfailed / 1 failed（既有失败，见「验证」） |
| 关联 spec | `S_22_scheduling-runtime.md`、`S_05_member-spawn-and-stream.md`、`S_08_team-tools-contract.md` |
| 关联 feature | `F_118_task-scoped-member-lifetime.md`、`F_62_scheduled-dispatch-runtime-and-review-voting.md` |

## 现象

2026-10-27 一次调度模式的迁移会话（`dispatch_mode=scheduled`）：

1. 18:21 leader 用 `create_task` 内联声明验收成员 `tv-rd-batch-1`（`lifetime=shutdown`），任务
   `t-rd-batch-verify` 依赖整合任务、停在 `blocked`。
2. 18:22 leader 发现批次范围要调整，取消了两个任务，`change_note` 写着「稍后以同一成员重发」。
3. 18:23:07 leader 给另一成员 `send_message`，`_auto_start_members` → `autostart_unstarted()` 把名册里所有
   `UNSTARTED` 成员都拉了起来，包括已经没有任务的 `tv-rd-batch-1`。它读完取消通知、空跑一轮回到 `READY`。
4. 18:23:34 退场扫描见它 `READY`、名下无未结束任务、无未读消息，按 `lifetime=shutdown` 请求退场，18:23:35
   落到 `shut_down`。
5. 23:08 leader 按原计划 `create_task(assignee="tv-rd-batch-1")`。`_validate_assignees` 只判存在，放行。
6. 23:08:58 调度器 `start_task` 把任务翻成 `in_progress`、往它邮箱发开工交接，`auto_start_member` 里
   `startup_member`（只认 `UNSTARTED`）与 `recover_member`（只认 `ERROR` / `DORMANT`）都不命中，静默返回
   `False`，只留一行 DEBUG 日志。
7. 23:09 → 次日 09:03 任务一直 `in_progress`，没有任何提醒；09:03 用户叫醒 leader，leader 新建
   `tv-rd-batch-1b` 改派后才恢复。

## 根因

1. **指派校验不看成员状态。** `_validate_assignees` 只做 `member_exists`，`SHUTDOWN` 成员照样能接任务；
   `update_task` 改派连这一层都没有。
2. **调度器拉不起负责人时没有后续。** 开工扫描先 `start_task` 再交接，`auto_start_member` 的返回值没人看；
   交接补发扫描只认「邮箱里有没有开工交接」，已经发过就不再管。
3. **调度模式的超时检查只在成员自己身上。** `ScheduledStaleTaskHandler` 跑在负责人自己的事件循环里，负责人
   没有运行时就没有 `POLL_TASK`，也就没有自催；调度模式下没有向 leader 升级的路径，leader 侧也不做存活检查。
4. **撒网拉起会唤醒没活干的任务级成员。** `autostart_unstarted()` 拉起全部 `UNSTARTED`，包括任务已被取消的
   任务级成员。它起来只会空跑一轮然后被退场扫描回收——对 `lifetime=shutdown` 来说是永久回收，正好断了
   leader「同一成员重发」的后路。

## 决策

1. **指派校验拒绝已退役与正在退出的负责人。** `_validate_assignees` 改读 `get_member_status`：
   不存在报 not found；`MEMBER_DEPARTED_STATUSES` 里的报错——`SHUTDOWN` 提示「已退出、无法再拉起，请新建成员
   或改派」，`SHUTDOWN_REQUESTED` 提示「正在退出，退向休眠的等落定后再指派，否则改派」。正在退出的一律拒绝、
   不看 lifetime：leader force 关停休眠类成员同样落 `SHUTDOWN`，lifetime 推断不出终态；代价是退向休眠的成员在
   退出的那几秒里被拒，leader 稍后重试即可。`update_task` 只在真的换人时
   （非取消、非重复写当前负责人）在任何编辑落库之前做同一检查（共用 `_retired_assignee_error`，不顺带引入 leader /
   不存在等其它校验，保持改派路径原有行为）。`DORMANT` 成员仍可指派——开工交接会唤醒它。
2. **调度器跳过退役负责人；意外搁浅的告诉 leader。** 新增 `TeamBackend.list_retired_members()`（DAO
   `list_shutdown_members`，只投影名字）；每次扫描在多遍循环之前取一次。开工扫描与交接补发扫描在各自已有的遍历里经
   `_skip_retired_owner` 跳过这些负责人的任务：`PENDING` 保持 `PENDING`（改派后开工扫描照常接手），活跃任务不补发；
   交接补发扫描先判退役、再查 `_handoff_known`，交接已落邮箱后才退役的负责人同样被接住，结果不随 leader 重启而变。
   是否升级看**是谁让它离开的**：`_reconcile_retirements` 对 `lifetime="shutdown"` 成员的退场请求成功时把它记进
   进程内 `_policy_retired`，只有这些负责人的搁浅任务按 `(task_id, owner)` 向 leader `deliver_input` 一次
   `scheduler.leader_escalation_retired_owner`（文案只推荐始终挂载的 `spawn_teammate`）：退场扫描只在名下清空时
   回收它，所以名下再出现未结束任务一定是离开后才交给它的，没人预期。leader 自己 `shutdown_member` 的成员（常驻，
   或被主动 / force 关停的任务级成员）遗留任务按该工具的约定归 leader 处置，只跳过不升级——临时团队收尾时先关停
   成员再 `clean_team` 不会被提醒打扰。`_stranded_escalated` 每次扫描结束时只保留本次仍遇到的搁浅对。
3. **撒网拉起跳过没有未结束任务的任务级成员。** `TeamBackend.startup()` 对 `options.lifetime` 非空、名下没有未结束
   任务的 `UNSTARTED` 成员不拉起，让它留在 `UNSTARTED`——`UNSTARTED` 不在退场候选里，leader 之后仍可把任务派给
   它，开工交接经 `startup_member` 拉起。广播是例外：它也发给这类成员，所以三条广播路径（leader
   `send_message(to="*")`、`MessageHandler` 对成员广播的代为拉起、interact `@all` 的 `auto_start_all`）传
   `include_idle_task_scoped=True`。
4. **点名的消息照样拉起。** 第 3 条之后，leader 单播 / 多播一个被撒网跳过的成员时，单靠 `autostart_unstarted`
   拉不起它。`SendMessageTool` 的直接收件人处理由 `_recover_members`（只恢复 `ERROR` / `DORMANT`）改为
   `_wake_recipients` → `TeamBackend.wake_member`（`UNSTARTED` 拉起、`ERROR` / `DORMANT` 恢复）。
   `wake_member` 的回调门拆到各自分支：拉起要 spawn 回调，恢复要重启回调，避免只配了重启回调的宿主丢掉原有的
   ERROR 恢复能力；恢复部分抽成 `_recover_from(member_name, current)`，`wake_member` 把已读的状态直接交给它，
   不再在 `recover_member` 里再读一次。

## 拒绝的方案

- **让 `auto_start_member` / `recover_member` 能复活 `SHUTDOWN` 成员。** `SHUTDOWN` 的语义是永久退场，冷恢复
  明确不复活它（S_05 / AGENTS 状态说明）；为一次改派打开复活口子会让 `shutdown_member` 失去意义。要复用成员，
  正确的声明是 `lifetime="dormant"`。
- **取消任务时不发取消通知。** 本例拉起它的不是取消通知，而是撒网拉起；通知本身只是一行邮箱记录。
- **在 `_send_as_leader` 里根据 `auto_start_member` 的返回值升级。** 返回 `False` 也可能是「已在运行」，区分
  不开；spawn 本身失败已有 `_report_member_startup_failure` 通知 leader（B_08）。按状态判断更准确。
- **把调度模式的 stale 检查搬到 leader 侧，按 `updated_at` 判超时。** 只能在十分钟后发现问题，而且负责人
  活着但干得慢时会误报；本例的问题是「负责人不可能被拉起」，状态一读就知道，不需要等超时。
- **按 `lifetime` 推断退场原因（任务级成员一律视为按策略退场）。** 第一版就是这样，但 leader 也可以主动
  `shutdown_member` 一个任务级成员，那时遗留任务本归 leader 处置，却会被误报成「意外搁浅」。改为在调度器发起
  退场的那一刻记下来。
- **把退场原因持久化进成员 `options`（`shutdown_member` 加 `by_policy`、DAO 写字段）。** 重启后仍能区分、继续
  提醒，但要给 `shutdown_member` 与 DAO 加参数和字段；源头校验已挡住绝大多数情况，重启后只跳过不提醒的代价
  可以接受，选了只记在调度器内存里的轻量方案。
- **所有已退役负责人的未结束任务都升级 leader。** 最初的实现就是这样，会把 leader 自己 `shutdown_member`
  留下的遗留任务也逐条报回去，临时团队收尾（先关停成员再 `clean_team`）时提醒排在 `clean_team` 后面，又把 leader
  拉起一轮。按成员行的退场时间区分也不可行：`team_member.updated_at` 在状态变化时不更新，改它会影响名册版本探测。
- **单独加一个搁浅扫描。** 每次唤醒最多 4 遍，每遍多 3 次任务列表查询；开工与交接补发扫描本来就遍历了
  `PENDING` / `PLANNING` / `IN_PROGRESS`，在跳过点顺带判断即可。
- **单播 / 多播不再撒网拉起全名册，只拉起收件人。** 撒网的另一个入口是 leader round-idle 对账（看板有未结束任务
  就 `autostart_unstarted`），本例里看板一直有活，只改消息路径仍会把空闲的任务级成员拉起来，跳过必须落在
  `startup()`。

## 验证

- 新增用例：
  - `tests/unit_tests/agent_teams/tools/test_tool_task.py`：`test_create_task_rejects_shut_down_assignee`、
    `test_create_task_accepts_dormant_assignee`、`test_create_task_rejects_member_settling_into_shutdown`、
    `test_create_task_rejects_dormant_lifetime_member_force_shut_down`、
    `test_update_task_rejects_reassigning_to_shut_down_member`、
    `test_update_task_rejected_reassignment_leaves_the_edit_unapplied`、
    `test_update_task_can_cancel_a_task_whose_owner_shut_down`；
  - `tests/unit_tests/agent_teams/tools/test_tool_message.py`：`test_direct_message_starts_idle_task_scoped_recipient`、
    `test_broadcast_starts_idle_task_scoped_member`；
  - `tests/unit_tests/agent_teams/agent/test_team_scheduler.py`：
    `test_pending_task_of_policy_retired_owner_is_not_started_and_escalated_once`、
    `test_only_lifetime_shutdown_retirements_are_recorded_as_policy`、
    `test_open_tasks_of_a_member_the_leader_shut_down_are_not_escalated`（常驻 / 任务级各一）、
    `test_reassigning_stranded_task_to_a_live_member_starts_it`（含升级记账随改派清除）、
    `test_active_task_whose_handoff_landed_is_escalated_when_its_owner_is_retired`（测试辅助 `_build_scheduler` 的
    假 backend 把 `list_retired_members` 接到真实 DB 的 `list_shutdown_members`）；
  - `tests/unit_tests/agent_teams/test_team.py`：
    `test_autostart_unstarted_skips_task_scoped_member_without_open_task`、
    `test_autostart_unstarted_for_a_broadcast_starts_idle_task_scoped_member`、
    `test_autostart_unstarted_starts_task_scoped_member_with_open_task`、
    `test_wake_member_starts_unstarted_member_skipped_by_autostart`、
    `test_wake_member_recovers_without_spawn_callback`、`test_list_retired_members_returns_only_shutdown`。
- 既有 `test_direct_message_recovers_error_recipient_before_delivery` 不改，验证第 4 条拆分回调门后行为不变；
  `test_member_startup_reconcile.py` 的 backend 桩同步 `startup` 的新关键字参数。
- `pytest tests/unit_tests/agent_teams/` → 4115 passed / 36 skipped / 2 xfailed / 1 failed。唯一失败
  `agent/test_member_activity.py::test_observe_member_status_emits_after_the_debounce` 在未改动的基线上同样稳定失败，
  与本次修改无关。

## 已知遗留

- **leader 重启后不再提醒。** `_policy_retired` 只在调度器内存里，重启后按策略退场者名下的搁浅任务只跳过、不提醒；
  源头校验已挡住正常路径上的指派，剩下的是校验与退场之间的竞态。

- **负责人活着但运行时意外消失（非 `SHUTDOWN`）** 仍依赖崩溃恢复与 B_08 的启动失败通知；本次只覆盖「负责人
  已退役」这一种可由状态直接判定的搁浅。
- **`IN_REVIEW` 任务的负责人退役不升级。** 验收由 reviewer 推进，返工时才需要负责人；返工把任务送回
  `IN_PROGRESS` 后由本次的扫描接住。
- **撒网拉起的跳过只看 `options.lifetime`。** 常驻成员即使没有任务仍会被拉起，这是既有行为（常驻成员不会被
  退场扫描回收），不在本次范围。
