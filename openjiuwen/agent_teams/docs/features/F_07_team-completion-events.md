# Team Completion Events

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-14 |
| 范围 | `openjiuwen/agent_teams/schema/`、`tools/`、`agent/coordination/handlers/` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/test_task_manager.py tests/unit_tests/agent_teams/test_message_manager.py tests/unit_tests/agent_teams/test_team.py tests/unit_tests/agent_teams/agent/test_team_completion_handler.py` |
| Refs | #751 |

## 背景

`agent_teams` 的事件体系此前只有「单个 task」「单个 member」「单条 message」粒度的事件
（`TaskCompletedEvent` / `MemberStatusChangedEvent` / `MessageEvent` 等），缺少「聚合完成」
语义的信号。调用方无法低成本判断两件事：

1. **leader 拆分的整批任务是否都收尾了** —— 只能监听每个 `TASK_COMPLETED` 再自己
   `list_tasks()` 重算（`task_board.py` 里已有这段重复逻辑）。
2. **整个团队是否彻底空转完成** —— 没有任何信号表示「成员都歇了 + 任务都终态 + 消息都读完」。

本次新增两个聚合事件，把这两个判定固化成一等公民事件。

## 数据结构 / 状态机

新增两个 `TeamEvent` 常量与对应的 `BaseEventMessage` 子类（均为 team-scoped，
`member_name` 留默认 `None`）：

- **`TASK_LIST_DRAINED` / `TaskListDrainedEvent(task_count)`** —— task 列表里每个任务都进入
  终态（`completed` / `cancelled`）且至少存在一个任务时触发。路由在 `TeamTopic.TASK`。
- **`TEAM_COMPLETED` / `TeamCompletedEvent(member_count, task_count)`** —— 三个条件同时成立时
  触发，路由在 `TeamTopic.TEAM`：
  1. 全体成员（**含 leader 自己**）状态属于 `MEMBER_SETTLED_STATUSES = {READY, PAUSED,
     STOPPED, SHUTDOWN}`；
  2. 全部任务终态（`TASK_TERMINAL_STATUSES`）且非空；
  3. 所有 direct / broadcast 消息都已被各自读者读完。

辅助类型：`schema/status.py` 新增 `MEMBER_SETTLED_STATUSES` frozenset；`schema/team.py`
新增 `TeamCompletionSnapshot(member_count, task_count)` frozen dataclass，作为
`TeamBackend.is_team_completed()` 的返回值。

`TEAM_COMPLETED` 的发射遵循上升沿语义：`TeamCompletionHandler._team_completed_emitted`
是进程内单 leader 的幂等标志，团队进入完成态时发一次、离开完成态（`is_team_completed()`
返回 `None`）时重新武装。

## 决策

- **Event A 触发点放在 `TeamTaskManager`**：`complete()` / `cancel()` / `cancel_all_tasks()`
  三个终态转移方法末尾调 `_maybe_publish_task_list_drained()`，复用已有 `_publish_task_event`
  helper 与 `TASK_TERMINAL_STATUSES` frozenset。只有这三条路径能把「最后一个」非终态任务推入
  终态，`add* / claim / reset / update_task / approve_plan` 不挂，避免无谓 DB 扫描。
- **Event B 聚合判定放在 `TeamBackend.is_team_completed()`**：`TeamBackend` 已聚合
  `db` + `task_manager` + `message_manager`，是天然的判定宿主。成员名单直接走
  `db.member.get_team_members()`（而非 `list_members()`，后者会排除调用方），保证 leader
  自己也计入。
- **Event B 评估挂在 `POLL_TASK` 内部 tick、只在 leader idle 时跑**：`kernel._filter_self`
  会丢掉 leader 自己发的 `MemberStatusChangedEvent`，leader 观察不到自身 settle，所以
  「监听成员状态变化判完成」对 leader 自身失效。`POLL_TASK` 是已有的、能到达 leader 的周期性
  idle tick（`StaleTaskHandler` 已用它做 stale 清扫）；`EVENT_METHOD_MAP` 允许多 handler
  注册同一 key、framework 串行 fan-out（coordination 铁律 2），所以新 handler 复用该 tick，
  不碰 `StaleTaskHandler`、不动 `stream_controller`。
- **新增 `TeamCompletionHandler` 既驱动又消费**：`on_poll_task` 驱动 Event B 评估 + 发射；
  `on_task_list_drained` 记日志并 fire 已注册的完成回调；`on_team_completed` 消费记日志
  （未来 SDK-facing 通知的挂载点）。注册元组里放在 `stale_task` 之后，保证 stale 清扫
  （可能 `deliver_input` 让 leader 转 busy）先跑、完成判定后跑。
- **`on_task_list_drained` 触发 `TeamSkillRail.notify_team_completed` 走「构造期注册回调」**：
  不在事件到达时临时去查 rail。`TeamCompletionHandler` 持有 `_completion_callbacks` 列表 +
  `register_completion_callback`；`TeamAgent._register_team_completion_callbacks` 在
  `configure` 末尾（deepagent 已建好、`agent_customizer` 已跑、dispatcher 已存在）一次性
  把 `TeamSkillRail.notify_team_completed` 注册进去。提取链路：
  `TeamHarness.find_rails(TeamSkillRail)` → `DeepAgent.find_rails_by_type`（新增公共访问器，
  对标 `strip_rails_by_type`）。`TeamSkillRail` 由用户 `agent_customizer` 选择性挂载，未挂
  则注册表为空、`on_task_list_drained` 不 fire 任何回调 —— 注册表非空本身就是 leader gate。
  这条事件驱动路径与 `TeamSkillRail._on_after_tool_call`（leader 调 `view_task` 看到全完成）
  互补，二者都受 `TeamSkillRail._evolution_in_progress` 去重。
- **「全部消息已读」新增 `MessageDao.has_unread_messages(team_name)`**：此前没有 team 级
  「是否有未读」聚合查询。direct 看 `is_read IS FALSE`；broadcast 用 per-member 高水位线
  （`MessageReadStatus.read_at`）逐 (成员, 广播) 比对，判定逻辑与 `get_broadcast_messages`
  完全一致。`TeamMessageManager.has_unread_messages()` 是薄转发。

## 拒绝的方案

- **Event B 评估改为「仅在 leader 收到 `TASK_LIST_DRAINED` 时触发一次」**：曾被提出以消除
  `POLL_TASK` 带来的 ~30s 延迟。否决原因：`TASK_LIST_DRAINED` 在「最后一个任务进入终态」的
  那一刻由完成该任务的成员发出，但此时该成员通常还在 BUSY（完成任务只是其一轮里的一个工具
  调用），「全体成员 settled」几乎必然不满足；而 `TASK_LIST_DRAINED` 每个 drained 边沿只发
  一次，没有后续重触发点，结果是 `TEAM_COMPLETED` 实际上几乎不会触发。`POLL_TASK` tick 是
  唯一能在「leader 自己也 settle 之后」可靠重评估的 leader-idle 钩子。
- **在 `stream_controller` round-end 处加钩子**：会在每个成员（不只 leader）上跑，且 leader
  自身 `team_member.status` 写 READY 可能尚未提交，还违反 coordination「handler 只反应、
  stream_controller 只管 stream 状态」的边界。
- **给 `mark_message_read` 加 `MESSAGE_READ` 事件**：本次不需要 —— 成员 drain 完邮箱通常紧接
  转 READY，`POLL_TASK` 周期评估已能覆盖；为单一判定新增一类高频事件不划算。

## 验证

- `tests/unit_tests/agent_teams/test_task_manager.py`：完成最后一个任务发 `TASK_LIST_DRAINED`、
  完成非最后任务不发、`cancel_all_tasks` 发一次、空 board 不发。
- `tests/unit_tests/agent_teams/test_message_manager.py`：`has_unread_messages` 的 direct /
  broadcast / 发送者自身豁免等用例。
- `tests/unit_tests/agent_teams/test_team.py`：`is_team_completed()` 的全满足 / 成员 BUSY /
  pending 任务 / 未读消息 / 空任务列表 / leader 自身 BUSY 各分支。
- `tests/unit_tests/agent_teams/agent/test_team_completion_handler.py`：leader+idle+snapshot
  发一次、连续 tick 不重发、下降沿重新武装、非 LEADER 不发、in-flight round 不发。

## 已知遗留

- **`TEAM_COMPLETED` 最多滞后一个 `task_poll_interval`（默认 ~30s）**：挂在 `POLL_TASK` tick
  上的固有延迟，对终态生命周期信号可接受。若未来需要更低延迟，需引入显式 round-end 钩子。
- **部分关停的团队也算「完成」**：若一部分成员 `SHUTDOWN`、其余 `READY`，任务全终态、无未读，
  会发 `TEAM_COMPLETED` —— 这是「成员终态集合含 `SHUTDOWN`」的直接结果。
- **`schema/events.py` typing 风格新旧混用**（`Optional/Dict/Type` 与 `str | None` 并存）：
  本次按最小 diff 贴合现状，未重构；全文件清理另开。
- **`TASK_LIST_DRAINED` 是 at-least-once**：teammate 完成任务、leader cancel 都会触发，多进程
  各自重扫可能重复发；消费侧仅记日志、幂等安全，且 `TEAM_COMPLETED` 真正的闸门是 leader poll
  tick 上的 `is_team_completed()` 重校验。
