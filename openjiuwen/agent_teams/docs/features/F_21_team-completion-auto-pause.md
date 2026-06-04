# Team Completion Auto-Pause

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-22 |
| 范围 | `openjiuwen/agent_teams/tools/database/message_dao.py`、`openjiuwen/agent_teams/tools/memory_database.py`、`openjiuwen/agent_teams/tools/message_manager.py`、`openjiuwen/agent_teams/tools/team.py`、`openjiuwen/agent_teams/schema/events.py`、`openjiuwen/agent_teams/agent/stream_controller.py`、`openjiuwen/agent_teams/agent/team_agent.py`、`openjiuwen/agent_teams/agent/coordination/dispatcher.py`、`openjiuwen/agent_teams/agent/coordination/handlers/team_completion.py`、`openjiuwen/agent_teams/agent/coordination/kernel.py`、`openjiuwen/agent_teams/docs/specs/S_03_coordination-protocol.md`、相关 CLAUDE.md、`tests/unit_tests/agent_teams/{test_team,test_message_manager,test_database,test_stream_controller,test_team_agent_coordination}.py` |
| 测试基线 | 受影响文件 297 passed（`test_team` / `test_message_manager` / `test_database` / `test_stream_controller` / `test_team_agent_coordination` / `test_team_agent` / `test_persistent_team` / `test_coordination_lifecycle`）；新增 11 条用例全绿 |
| Refs | `#751` |
| 修订 | 2026-05-24：推翻原"排除广播"决策，改为完成判定含广播（`is_team_completed` 传 `include_broadcast=True`），任何未读消息都阻塞团队结论。理由见"决策"段 |

## 背景

persistent 团队的 leader 跑完一轮 deepagent 后会转 `READY` 并**挂起等待**下一个唤醒事件
（worker 回报、用户 interact 等），dispatcher 仍在调度、pool entry 仍 `RUNNING`
（见 `runtime/CLAUDE.md` 的 "Stream 生命周期 ≠ OuterLoop 单轮"）。即使所有任务已终态、
所有成员已静止、没有未读消息，leader 的流也不会自动收尾，SDK 调用方会一直阻塞在
`Runner.run_agent_team_streaming` 的 `async for` 上。

需求：当 persistent 团队满足"完成"三条件时，自动结束 leader 流并让团队进入 pause，
且调用方能从流里拿到一个明确的"团队已完成"信号（区别于因错误/取消而结束）。

`F_07_team-completion-events` 已经建立了完成检测的骨架——`TeamBackend.is_team_completed()`
评估三条件、`TeamCompletionHandler.on_poll_task` 在 leader idle 时按上升沿发 `TEAM_COMPLETED`
事件——但当时 `TEAM_COMPLETED` 的消费端只记日志（注释自述是 "the hook point for a future
SDK-facing notification"）。本特性把这个已有信号接到"结束 leader 流 → auto-pause"上，
并对齐三处需求差异（消息未读判定、判定顺序、即时触发）。

## 数据结构 / 状态机

完成时的数据流（persistent，leader 进程内）：

```
leader 一轮结束 (_run_one_round finally, 已 await update_status(READY), 无 pending 工作)
  └─ StreamController 经注入的 request_completion_poll_callback
       └─ TeamAgent._request_completion_poll  [leader+persistent gate]
            └─ CoordinationKernel.enqueue(InnerEventMessage(POLL_TASK))
                 └─ EventBus._run_loop 取出 → fan-out (stale_task 先, team_completion 后)
                      └─ TeamCompletionHandler.on_poll_task
                           ├─ is_team_completed(): 任务全终态 → 成员全 settled → 无任何未读消息(含广播)
                           ├─ 上升沿 guard: 发 TEAM_COMPLETED 事件 (保留, 供 teammate / SDK 订阅)
                           └─ [persistent gate] _lifecycle.conclude_completed_round(m, t)
                                └─ StreamController.emit_completion_and_close():
                                     1. put_nowait(team.completed marker chunk)
                                     2. close_stream() → put_nowait(None)
                                          └─ TeamAgent.stream 读到 None 退出
                                               └─ Runner finally → manager.finalize
                                                    └─ persistent → pause_coordination() → PAUSED
```

周期 `POLL_TASK` tick 仍作兜底，覆盖"leader 先 settle、teammate 后 settle"的场景
（leader 收不到自己的 `MemberStatusChangedEvent`，但能等下一个 tick 重评估）。

`team.completed` marker chunk 与 `team.runtime_ready`（`team_runner._build_team_runtime_ready_chunk`）
同构：`TeamOutputSchema(type="message", index=0, payload={"event_type": "team.completed",
"member_count": ..., "task_count": ...}, source_member=<leader>, role=LEADER)`。

`_team_completed_emitted` 是上升沿幂等标志。`kernel.start`（冷启动 / resume / recover）
每次调 `dispatcher.team_completion.rearm()` 重置它，使每个 run cycle 独立判定完成。

## 决策

- **复用现有 `is_team_completed` + `TeamCompletionHandler.on_poll_task` 作为唯一完成评估入口**，
  本特性只"接线"不重写判定逻辑。round-end 只负责"请求即时评估"（enqueue POLL_TASK），
  评估和 conclude 决策仍在 handler。
- **完成判定含广播（任何未读都阻塞）**：`message_dao.has_unread_messages` /
  `message_manager.has_unread_messages` / 内存 DAO 提供 keyword 参数
  `include_broadcast: bool = True`（默认 True），`is_team_completed` 传 `True`。完成判定从严——
  任何未投递消息（直消息或 fan-out 广播）都阻塞团队结论；广播常承载关键通知（任务取消、团队公告），
  成员未消费就 auto-pause 会让其错过。**修订说明**：本特性最初的决策是"排除广播"（`is_team_completed`
  传 `False`，理由是"广播无强制接收者、不应阻塞"），后于 2026-05-24 反转为含广播；`include_broadcast`
  参数本身保留（默认 True），调用方仍可按需排除。
  **顺带补齐了 `InMemoryTeamDatabase.has_unread_messages`**——此前内存后端根本没有这个方法
  （`is_team_completed` 的现有单测全走 SQLite，掩盖了缺口），inprocess 路径调到会 `AttributeError`。
- **判定顺序改为 任务 → 成员 → 消息**（`is_team_completed`）。三条件是 AND，顺序不影响结果，
  仅短路顺序；按需求对齐。
- **即时触发用 round-end enqueue POLL_TASK**：满足"leader 一轮结束转 READY 时触发"的时机要求，
  不必等周期 tick（~30s 延迟）。注入方式镜像现有 `wake_mailbox_callback`——StreamController 加
  可选 `request_completion_poll_callback`，TeamAgent 注入"enqueue POLL_TASK"，teammate 传 None。
- **conclude 走 `TeamLifecycleController.conclude_completed_round`，不绕 `TEAM_COMPLETED` 事件**：
  `kernel._filter_self` 会丢掉 leader 自己发的事件，leader 的 `on_team_completed` 收不到自己的
  完成事件，所以 close 流的动作必须在检测到完成的那一刻、在 leader 进程内同步做。新方法归属
  `TeamLifecycleController`（跨 stream/session/member 的 TeamAgent 级生命周期副作用），与
  `shutdown_self` 并列，符合该协议 docstring 的自述定位。
- **结束流前先发 `team.completed` marker chunk**：`emit_completion_and_close` 保证 marker 严格
  排在 `None` 哨兵之前（FIFO），SDK 调用方据 `payload["event_type"]` 区分"因完成而结束"
  与"因错误/取消而结束"。
- **persistent gate 双层**：注入层（`_request_completion_poll`）gate 即时路径避免无谓 enqueue；
  handler 层（`on_poll_task` 的 `lifecycle == "persistent"`）gate 正确性——周期兜底 tick 不经注入
  回调，必须在 handler 拦住 temporary。temporary 团队靠 leader 调 `clean_team` 收尾，
  auto-close 对它会变成 stop（销毁）而非 pause，语义不符。
- **resume re-arm 放 `kernel.start`**：`kernel.start` 覆盖冷启动 / resume / recover 所有入口，
  一处 re-arm 让"resume 一个已完成的 persistent 团队"也能再次 conclude。

## 拒绝的方案

- **消费 `TEAM_COMPLETED` 事件来 close leader 流**：leader 自发的事件被 `kernel._filter_self`
  丢弃，leader 收不到，无法据此 close 自己的流。改在检测点同步 conclude。
- **在 `stream_controller` round-end 处直接评估完成**：`F_07` 明确拒绝过，理由是"会在每个成员上
  跑 / leader 自身 READY 写可能未提交 / 违反 handler 只反应、stream_controller 只管 stream 的
  边界"。本特性的 **round-end enqueue 变体绕开了这三条**：(1) 只有 leader 注入回调，teammate 传
  None；(2) enqueue 是异步的，handler 取出评估时 round-end 的 `await update_status(READY)` 早已
  提交（解耦了写与读的时序）；(3) 评估和决策仍在 handler 内，stream_controller 只发一个
  "请重新评估"的信号，不做完成判定。
- **直接改 `has_unread_messages` 语义为只看直消息**（而非加参数）：会破坏其他潜在调用方对
  "含广播"的预期；加默认 True 的 keyword 参数是向后兼容的最小改动。
- **对 temporary 团队也 auto-close**：会触发 `manager.finalize` 的 stop+pool.remove（销毁），
  与 temporary 的 `clean_team` 主动收尾路径重叠/冲突，且语义上不是"pause"。
- **只在注入层 gate persistent**：周期兜底 `POLL_TASK` tick 不经注入回调，会让 temporary 团队
  在周期 tick 上误触发 conclude → 错误地 close 流。必须 handler 层也 gate。

## 验证

新增/修改用例（pytest 纯函数风格，`test_logger`）：

- `test_message_manager.py`：`include_broadcast=False` 忽略未读广播 / 直消息在两种设置下都计入。
- `test_database.py`：`InMemoryTeamDatabase.has_unread_messages` 的 direct/broadcast/`include_broadcast` 分支
  （补内存后端缺口）。
- `test_team.py`：`is_team_completed` 未读广播也阻塞完成（仅广播未读仍返回 `None`）；现有"直消息未读阻塞"
  用例不回归。
- `test_stream_controller.py`：`emit_completion_and_close` 的 marker 严格先于 `None` 哨兵 / 无 queue 时
  no-op；round-end 触发 `request_completion_poll_callback`，teammate（callback=None）不报错且不入队。
- `test_team_agent_coordination.py`：persistent 完成 → 入队 marker+sentinel；temporary 完成 → 只发
  `TEAM_COMPLETED` 不 close 流；上升沿只 conclude 一次；`rearm()` 后能再次 conclude。

基线：受影响文件 297 passed，无回归。

## 已知遗留

- **未加 Runner 级端到端 inprocess 用例**串联"完成 → close → finalize → pause"全链。当前由
  handler 测试（验证"完成 → conclude → emit marker + close stream_queue"）+ 现有 `manager.finalize`
  pause-vs-stop 测试（`test_runner_team_runtime.py`）覆盖链路两端；中间的"None 哨兵 → stream loop
  break → finalize"是现有已测机制。后续可补一条 `spawn_mode="inprocess"` + `MemoryDatabaseConfig`
  的端到端用例断言 stream 中出现 `team.completed` chunk 且 pool entry 转 `PAUSED`。
