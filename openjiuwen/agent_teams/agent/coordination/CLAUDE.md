# Coordination — 唤醒循环

`TeamAgent` 的事件驱动唤醒层。把传输层来的 `EventMessage` 与内部 poll 计时器产生的 `InnerEventMessage` 收成统一的 `CoordinationEvent`，按粗筛规则放行后由 `AsyncCallbackFramework` 分发到具体的场景 handler。**自身不做业务决策**——handler 通过三类 narrow protocol（`AgentRoundController` / `TeamLifecycleController` / `PollController`）触发行为：`deliver_input` / `cancel_agent` / `resume_interrupt` / `shutdown_self` 落到 TeamHarness，`pause_polls` / `resume_polls` 直达 EventBus，最终驱动 DeepAgent + team tools。

## 文件地图

| 文件 | 类 | 职责 |
|---|---|---|
| `event_bus.py` | `EventBus` / `InnerEventType` / `InnerEventMessage` | 事件入队 + 周期 poll timer + lifecycle（`start(wake_callback=...)` / `stop`）；天然实现 `PollController`（`pause_polls` / `resume_polls`）。**`HUMAN_AGENT` 角色不启动周期 poll timer**（poll 全程被 dispatch mute，起 timer 纯空转）——`start` / `resume_polls` 共用 `_start_poll_tasks`，按 `_periodic_poll_enabled` 单点门控 |
| `dispatcher.py` | `EventDispatcher` + `AgentRoundController` / `TeamLifecycleController` / `PollController` / `DispatcherHost` | 三类 narrow protocol 划分调用面；触发规则（agent_ready / inner-vs-transport / 角色级粗筛）+ 持有私有 `AsyncCallbackFramework` 实例 + 把 6 个场景 handler 的 `get_callbacks()` 注册到 framework；handler 实例作为字段直接暴露（`dispatcher.lifecycle / .member / .message / .task_board / .stale_task / .team_completion`） |
| `handlers/` | `BaseCoordinationHandler` + 6 个场景 handler | 见下文"handler 拆分" |
| `kernel.py` | `CoordinationKernel` | 整体协调 facade：`setup` 时构造 event_bus → 注入到 dispatcher 作 `poll_ctrl`；`start` 时调 `event_bus.start(wake_callback=dispatcher.dispatch)` 闭合循环依赖；start 委托 `SessionManager.bind_session`；pause / resume / drain |

## handler 拆分

`handlers/` 下一个 handler 一个业务域，每个类自己声明 `EVENT_METHOD_MAP: ClassVar[dict[str, str]]` 与对应 `async` 方法，从 `BaseCoordinationHandler.get_callbacks()` 输出 `event_key → bound method` 注册给 framework。沿用 `core/single_agent/rail/base.py:AgentRail` 的 rails 约定。

`BaseCoordinationHandler.__init__` 一次性接收 5 类依赖，子类用 narrow 字段访问对应职责：

| 字段 | 类型 | 用途 |
|---|---|---|
| `self._blueprint` | `TeamAgentBlueprint` | 静态身份（role / member_name / lifecycle / spec）|
| `self._infra` | `TeamInfra` | per-process 容器（task_manager / message_manager / team_backend / messager / workspace_manager）|
| `self._round` | `AgentRoundController` | 跟 TeamHarness 打交道（deliver_input / cancel_agent / resume_interrupt / has_in_flight_round / has_pending_interrupt / is_agent_running）|
| `self._lifecycle` | `TeamLifecycleController` | TeamAgent 级副作用（`shutdown_self` / `conclude_completed_round`）|
| `self._poll` | `PollController` | EventBus 自身的 poll 控制（pause_polls / resume_polls）|

handler **不持有原始 host 引用**。新增依赖一律通过 narrow protocol 字段或显式构造参数注入，避免重新滑回上帝接口。

| handler | 监听 event | 状态 | 关键方法 |
|---|---|---|---|
| `AgentLifecycleHandler` | `USER_INPUT` / `STANDBY` / `CLEANED` / `TOOL_APPROVAL_RESULT` | 无 | `on_user_input` / `on_standby` / `on_cleaned` / `on_tool_approval_result` |
| `MemberHandler` | 6 个 `MEMBER_*` | 共享 `stale_claim_throttle` | `on_member_event` 分流到 `_handle_leader_member_event` / `_handle_teammate_member_event`；`MEMBER_STATUS_CHANGED → READY/ERROR` 时 `_nudge_idle_member_with_stale_claims`；自身 `MEMBER_SHUTDOWN` 时 teammate 不在此 teardown（走 drain → 末轮 → round-end close_stream），**human-agent 走 `_shutdown_human_agent`：有 in-flight round 且非 force 则不打断（靠 round-end close），空闲或 force 才 `_lifecycle.shutdown_self()`** |
| `MessageHandler` | `MESSAGE` / `BROADCAST` / `POLL_MAILBOX` + `MEMBER_SHUTDOWN`（fan-out） | 无 | `on_message_or_broadcast`（leader 额外 ack user-bound + 通知 human-agent inbound）/ `on_poll_mailbox` / `on_member_shutdown_drain`（仅 teammate 给自己 drain）。`_format_message` 是 role-aware：是 human-agent 时走 `hitt.msg_received_for_human` 前缀 `[转发给控制者的…]`，否则走 `dispatcher.msg_received` |
| `TaskBoardHandler` | `TASK_CLAIMED` / 5 个 `TASK_*` | 无 | `on_task_claimed`（targeted assignment）/ `on_task_board_event` → `_nudge_idle_agent`。self 分支 role-aware：是 human-agent 时走 `hitt.task_assigned_to_self_human` 前缀 `[任务指派给控制者]`（best-effort 查 task title 内联），否则走 `dispatcher.task_assigned_to_self` |
| `StaleTaskHandler` | `POLL_TASK` | 共享 `stale_claim_throttle` + 独占 `_last_pending_nudge` | `on_poll_task` → `_check_stale_claimed_tasks` + `_check_stale_pending_tasks` |
| `TeamCompletionHandler` | `POLL_TASK` / `TASK_LIST_DRAINED` / `TEAM_COMPLETED` | 独占 `_team_completed_emitted` + `_completion_callbacks` | `on_poll_task`（leader idle 时评估 `TeamBackend.is_team_completed()` 并按上升沿发 `TEAM_COMPLETED`；**persistent 团队额外调 `_lifecycle.conclude_completed_round` 结束 leader 流触发 auto-pause，temporary 不调**）/ `rearm`（清上升沿标志，`kernel.start` 每次调，使每个 run cycle 独立判定）/ `on_task_list_drained`（记日志 + fire `_completion_callbacks`，`TeamAgent` 在 deepagent 建好后把 `TeamSkillRail.notify_team_completed` 经 `register_completion_callback` 注册进来）/ `on_team_completed`（消费记日志）|

`MemberHandler` 与 `StaleTaskHandler` 在 `EventDispatcher.__init__` 拿到同一个 `dict[str, float]` 引用（`stale_claim_throttle`），保证"成员状态变化 nudge"和"poll 周期 nudge"两条路径不会在同一 stale 窗口内对同一 task 重复 nudge。`_last_pending_nudge` 仅 leader 自己用，留在 `StaleTaskHandler` 私有字段。

## 三条铁律

**铁律 1：coordination 不做决策。** loop 只管 wake-up，所有业务行为由内部 DeepAgent + team tools 驱动。新功能想塞进 dispatcher.py / handler 之前先问：是不是应该用一个新工具实现，让 LLM 自己决定调？

**铁律 2：每个 handler 一个业务域，跨域协作走 framework fan-out。** 新事件类型 = 在对应场景 handler 的 `EVENT_METHOD_MAP` 加一行 + 写方法，不需要改 `dispatcher.dispatch()`。`dispatch()` 只承担"是否该 trigger"的粗筛，**不做** event_type → handler 的具体路由——这是 framework 的职责。如果一个事件需要跨域响应（例如 `MEMBER_SHUTDOWN` 既要更新成员状态也要 drain 邮箱），让两个 handler 各自注册同一 event_key，framework 按注册顺序串行 fan-out —— **不要在一个 handler 内部调另一个 handler 的方法**。

**铁律 3：异常语义。** `AsyncCallbackFramework.trigger()` 吞普通 `Exception`（log + continue），仅 `AbortError` 上抛。和原 fail-fast 语义不同——handler 内部的失败不会让 dispatcher 中断、也不会阻断同一 event 上的其它 fan-out callback。handler 必须自己用 `team_logger.error("...", exc_info=True)` 记录关键失败，不依赖 framework swallow 当作隐式错误处理。

## 跨域协作要点

- **fan-out 顺序由注册顺序决定**：`EventDispatcher.__init__` 中 `(lifecycle, member, message, task_board, stale_task, team_completion)` 元组顺序就是 framework 注册顺序。同 priority（默认 0）下 Python `list.sort` 稳定，故 `MEMBER_SHUTDOWN` 上 `MemberHandler.on_member_event` 先于 `MessageHandler.on_member_shutdown_drain`；`POLL_TASK` 上 `StaleTaskHandler.on_poll_task` 先于 `TeamCompletionHandler.on_poll_task`（stale 清扫可能 `deliver_input` 让 leader 转 busy，完成判定要看到这个变化）。改这个元组顺序前先评估 fan-out 影响。
- **`kernel.pause` 会等 in-flight round drain**：相关 `contextlib.suppress(asyncio.CancelledError, Exception)` 在 `stream_controller.drain_agent_task` —— 改清理路径时检查 `import contextlib` 是否还在（之前漏过一次）。
- **三个 narrow protocol 是 host ↔ handler 的公共契约**：`AgentRoundController` / `TeamLifecycleController` 由 TeamAgent 实现，`PollController` 由 EventBus 实现。新增 handler 依赖前先想清楚：是 round 行为、TeamAgent 级生命周期、还是 poll 控制？把方法加到对应 protocol，不要把所有东西重新塞回 `DispatcherHost`。`start_agent` / `follow_up` / `steer` 故意不在 `AgentRoundController` 上——handler 走 `deliver_input` 让 host 按 round 状态自己分流。
- **构造顺序敏感**：`kernel.setup()` 先建 EventBus，再建 EventDispatcher（注入 `poll_ctrl=event_bus`）；`kernel.start()` 调 `event_bus.start(wake_callback=dispatcher.dispatch)` 闭合循环依赖。EventBus 在 `__init__` 期间不接受 wake_callback——晚到 `start()` 时绑定，避免暴露 setter。
- **session 绑定走 `SessionManager` 的两方法**：
  - `await bind_session(session)`：完整绑定，session 必须非 None。kernel.start 在拿到 session 时调它（落 contextvar 并持 Token、建 per-session DB 表、leader 持久化 config）。
  - `release_session()`：单一 tear-down 路径——pause / stop / session=None 启动全部走这里。reset contextvar Token + 丢 live `AgentTeamSession`。session_id 现在只活在 contextvar 里（参见 [`agent/CLAUDE.md`](../CLAUDE.md) 四象限），release 后即不可见；resume 路径在重新 `bind_session(new_session)` 时由新 session 提供 id，不再依赖任何缓存。
  在 kernel 里手工再写一遍 contextvar set + DB 表初始化 + persist_leader_config 就是在重复 `_switch_session` 时代的烂代码。

## 跟其它子目录的边界

- `interaction/` 把三视角入口（GodView / Operator / HumanAgent）解析成 `EventMessage` 后才进 dispatcher，不要让 dispatcher / handler 自己解析 mention 字符串——那段已搬到 `interaction/router.py`。
- 真正干活的 LLM 在 `harness/deep_agent.py`，本目录只装配 + 调度。
- 跨 team 的对象池 / 派发 / 并发门禁在 `runtime/`，本目录不感知。
