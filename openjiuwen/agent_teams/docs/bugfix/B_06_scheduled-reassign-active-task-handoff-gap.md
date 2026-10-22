# 调度模式：改派一个已开工的任务给尚未启动的成员后，新 owner 永远收不到交接

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-15 |
| 严重程度 | 中：串行任务链整条停住。本例 5 人 5 任务的祝福语团队，第 2 环改派后空转 8 分多钟，直到用户问"2 号怎么了"、leader 手动 `send_message` 催办才恢复；无人干预时要等成员自己的 stale 巡检（10 min）才会动 |
| 范围 | `agent_teams/agent/scheduling/scheduler.py`（新增 `_reconcile_handoffs` / `_owner_has_start_handoff` / `_send_task_start`，`_send_as_leader` 返回投递结果，`_handoff_known` 记账）、`agent_teams/agent/scheduling/render.py`（`is_task_start_meta`）、`agent_teams/agent/coordination/handlers/task_board.py`（`ScheduledTaskBoardHandler.on_initial_task_poll` 注释）、`tests/unit_tests/agent_teams/agent/test_team_scheduler.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/agent/test_team_scheduler.py` → 45 passed（新增 4 例）；`pytest tests/unit_tests/agent_teams/ --ignore=cli --ignore=observability` → 3647 passed / 36 skipped / 3 xfailed（`agent/test_member_activity.py::test_observe_member_status_emits_after_the_debounce` 在未改动的 develop 基线上同样失败，与本修复无关，本次 deselect） |
| 关联 spec | `S_22_scheduling-runtime.md`（不变量 4 扫描数 3→4；不变量 13 记账清单；新增不变量 18；消息面开工模板的第二个使用场景） |

## 现象

swarm 会话 `web_1a13f3fbc7a_e7b22e394495`（`dispatch_mode == "scheduled"`），
用户要求"创建 5 休眠成员团队，建立 5 个串行任务，都是写 1 句祝福语"。leader 的操作序列（`jiuwen.log`）：

| 时刻 | 事件 |
|---|---|
| 19:08:45 | `create_task` 建 `bless-1..5` 并内联 `lifetime=dormant` 的 writer-1/3/4/5；`bless-2` 的初始 assignee 是 writer-1 |
| 19:08:56.6 | leader 调 `spawn_teammate` 登记 writer-2——只写 DB 行，`UNSTARTED`，不启动 |
| 19:08:56.9 | writer-1 完成 `bless-1`；调度器开工扫描把 `bless-2` `PENDING → IN_PROGRESS` 并交接给 writer-1 |
| 19:08:57.7 | leader `update_task(task_id=bless-2, assignee=blessing-writer-2)`；日志 `Task bless-2 reassigned from blessing-writer-1 to blessing-writer-2`、`Published: Task claimed event for bless-2 (to blessing-writer-2)`。writer-1 收到 `TASK_REVOKED`，回复"任务已被转交给其他成员，我已停止" |
| 19:09:05.6 | leader 回合空闲，`round-idle reconcile` 才真正拉起 writer-2 |
| 19:09:06.2 | writer-2 的 `INITIAL_POLL_TASK` 打出 `scheduled dispatch: startup board survey skipped`；此后每 30 s 只有 `poll mailbox` 与 `poll task (scheduled)`，始终没碰 `bless-2` |
| 19:17:53 | 用户问"2 号怎么了"，leader `send_message` 催办，writer-2 这才读到任务、20 s 内完成，链路恢复 |

期间看板上 `bless-2` 一直是 `IN_PROGRESS / assignee=blessing-writer-2`，产物文件只有 1 行；`bless-3..5` 全部 `BLOCKED` 等它。

## 根因

三段各自成立的设计叠加出一个没人负责的窗口：

1. **开工扫描只在"离开 `PENDING`"那一刻发交接。** `TeamScheduler._reconcile_starts` 扫 `PENDING(assignee)`，
   `start_task` CAS 成功后 `_send_as_leader(owner, meta_task_start)`。交接这件事与状态跃迁绑死。
2. **改派保留状态，不再经过那一刻。** `TeamTaskManager.reassign` → `task_dao.reassign_task` 是单条
   `UPDATE ... SET assignee` （`TASK_REASSIGNABLE_STATUSES` 含 `IN_PROGRESS`），状态原地不动——这是 F_54 / F_56
   有意为之（不回弹 `PENDING`，避免 `TASK_RELEASED` 让空闲成员抢进公共池）。它只发两个瞬时事件：
   `TASK_REVOKED`（旧 owner 停手，本例生效）和 `TASK_CLAIMED`（新 owner）。
3. **新 owner 没有任何第二条路得知这件事。** `TASK_CLAIMED` 是内存总线事件，不落邮箱；writer-2 当时还是
   `UNSTARTED`（`spawn_teammate` 只登记，拉起要等 round-idle reconcile 或别人给它发消息），事件发出去无人接收。
   它起来后：
   - `ScheduledTaskBoardHandler.on_initial_task_poll` 是 no-op（F_69 的首启板巡视只给 autonomous，调度模式的理由
     是"调度器的交接会落邮箱、首启邮箱 sweep 会 drain"——这个前提在改派路径上不成立）；
   - `TaskBoardHandler.on_task_claimed` 即使收到，也只通告 `status == "pending"` 的任务，`IN_PROGRESS` 直接
     `not announced`；
   - 调度器后续每次扫描都只看 `PENDING`，`IN_PROGRESS` 的 `bless-2` 对它不可见；
   - `ScheduledStaleTaskHandler` 的兜底阈值 `_STALE_CLAIM_SECONDS` = 10 min，且读的是 `task.updated_at`——改派本身
     刷新了这个时间戳，所以从 19:08:57 起算要到 19:18:57 才会自催。

把三者放在一起：**调度模式的交接通道是"邮箱 + 懒启动"，而改派这条写 assignee 的路径没有走它。**

## 决策

**原则：交接的真相是邮箱，调度器从看板 + 邮箱两张表推导缺口，不消费"改派"事件。** 这与 S_22 不变量 1
（只理解看板）和不变量 2（交接 = 邮箱消息、投递即启动）一致，也让崩溃恢复仍然等于"激活扫描"。

1. **`TeamScheduler._reconcile_handoffs`——第四个幂等扫描**，排在开工扫描之后、验票之前。对每个
   `PLANNING` / `IN_PROGRESS` 且有 assignee 的任务：
   - 查 owner 邮箱里是否有 leader 身份（`from_member_name == message_manager.member_name`）、
     `meta` 为 `scheduler_task_start` / `scheduler_task_start_plan` 且 `refs.task == task_id` 的行；已读未读皆算
     （读过 = 已投递，未读 = 下次 sweep 必投）；
   - 没有就 `_send_as_leader(owner, render.meta_task_start(task))`——与开工扫描**逐字同一条**交接，同样经
     `auto_start_member` 懒启动 owner（`UNSTARTED→STARTING` / `DORMANT→RESTARTING`）。
2. **`_handoff_known: set[(task_id, owner)]` 进程内记账**。本进程发过（开工扫描与补发扫描共用
   `_send_task_start`）或在邮箱里查到过即记，省掉每次 wake 对每个活跃任务的一次邮箱读；重启后记账为空，每对
   至多重新读一次。归入 S_22 不变量 13 的"内存记账不持久化"清单。
3. **`_send_as_leader` 返回邮箱行是否落下**（原来返回 `None`）。落下才记账；`send_message` 返回空或抛异常
   都返回 `False`，留给下次扫描重试。成员启动是 best-effort，不影响返回值。
4. **`render.is_task_start_meta(meta, task_id)`**：开工模板 key 继续只在 `render.py` 里定义，调度器不做字符串匹配。
5. **不动 `reassign` / `update_task`**：改派路径的语义（状态保留、只发两个定向事件）保持原样；修复落在
   调度器一侧，autonomous 模式零改动（它没有调度器，改派后的通知走 F_69 的首启板巡视与 stale 扫描）。
6. `ScheduledTaskBoardHandler.on_initial_task_poll` 的 docstring 补一句：改派到手的活跃任务同样由调度器的
   交接补发覆盖，首启板巡视仍然不需要。

## 拒绝的方案

- **在 `update_task` 工具 / `TeamTaskManager.reassign` 里直接给新 owner 发开工交接。** 工具层不知道
  `dispatch_mode`（S_22 不变量 14：方法体里禁止判模式），且这会让"交接"多出一个调度器之外的发送点——
  邮箱里会出现不是调度器发的 `scheduler_task_start`，S_22 不变量 2 的"所有成员交接都是调度器的 leader 身份
  消息"就不再成立。改派一个 `PENDING` 任务时还会与开工扫描双投递。
- **让调度器消费 `TASK_CLAIMED`（或 `SCHEDULER_SCAN` 带上任务 id）。** 事件只发一次，leader 崩溃在事件与
  发送之间就丢；而且 kernel 的 `_filter_self` 把 leader 自发事件降级成空载荷的 `SCHEDULER_SCAN`，要带信息得改
  回声协议。扫描式设计让激活即恢复，与其余三个扫描同构。
- **调度模式也开首启板巡视（撤掉 F_69 对调度类的 no-op）。** 只覆盖"改派时成员没起来"这一种情形：成员已在线
  但 `TASK_CLAIMED` 在路上丢了、或成员 `DORMANT` 被别的消息唤醒时板上没它的活，都兜不住；且会与调度器邮箱
  交接双投递——F_69 当初给调度类留 no-op 的理由仍然成立。
- **放宽 `on_task_claimed` 对 `pending` 的限制，让 `IN_PROGRESS` 也通告。** 对已在线的新 owner 有效，但
  `UNSTARTED` / `DORMANT` 的 owner 根本收不到事件，本例恰恰是这种。它也不是持久通道。
- **改派时把任务回弹到 `PENDING` 让开工扫描接手。** 正是 F_54 / F_56 否决过的做法：`TASK_RELEASED` 会让空闲
  成员抢进公共池，且丢掉"已开工"这一事实。
- **缩短 `_STALE_CLAIM_SECONDS`。** 只是把 8 分钟改成更短的空转，没有消除缺口；而且该阈值是全局兜底，
  不应为单个路径调参。

## 验证

- 新增用例（`tests/unit_tests/agent_teams/agent/test_team_scheduler.py`）：
  - `test_reassigned_active_task_is_handed_to_its_new_owner`：开工交接给 dev-1 后 `reassign` 到 dev-2，再一次
    `SCHEDULER_SCAN` 即给 dev-2 发 `scheduler_task_start`（`refs.task` 相同）并懒启动 dev-2，任务仍 `IN_PROGRESS`；
  - `test_handoff_scan_sends_each_owner_its_start_once`：连续三次扫描不重发；
  - `test_restarted_scheduler_trusts_the_mailbox_before_re_sending_a_handoff`：新建调度器（记账为空）时，邮箱里已
    有开工行的 owner 不再收、没有的 owner 补收，`get_messages` 以 leader 身份过滤；
  - `test_handoff_scan_recognises_a_plan_gate_start`：`scheduler_task_start_plan` 也算开工交接。
- 既有 41 例不变（测试辅助 `_build_scheduler` 给 mock 邮箱补了 `member_name` 与空的 `get_messages`）。
- `pytest tests/unit_tests/agent_teams/agent/test_team_scheduler.py` → 45 passed。
- `pytest tests/unit_tests/agent_teams/ --ignore=cli --ignore=observability` → 3647 passed / 36 skipped / 3 xfailed（deselect 一例见元信息）。

## 已知遗留

- **改派后状态不随新 owner 的 mode 调整。** `PLANNING` 任务改派给 build-mode 成员仍留在 `PLANNING`，补发的是
  plan 闸模板；反向亦然。状态语义归 F_59 的 plan 闸，本次不碰。
- **自主模式的同款缺口由 F_69 覆盖，但也只覆盖"成员未启动"那一半**：在线成员漏掉 `IN_PROGRESS` 任务的
  `TASK_CLAIMED`（`on_task_claimed` 只通告 `pending`），仍要等 stale 扫描。本次只修调度模式。
- **每个活跃任务重启后一次邮箱全读**（`get_messages(to, from=leader)` 取该 owner 收到的全部 leader 消息再在
  Python 侧匹配 meta）。活跃任务数 = 成员数量级，可接受；若将来 leader→成员消息量很大，可给 DAO 加按
  `meta` 过滤的查询。
- 本例日志里另有两条无关错误顺手记录：会话收尾时 4 条 `Invalid state transition: dormant -> paused`
  （`member_dao.py:489`），以及 worker 侧 `'MonitorEvent' object has no attribute 'target_status'`
  （`team_monitor_handler`）。两者都在 swarm / core 的其它模块，未在本次处理。
