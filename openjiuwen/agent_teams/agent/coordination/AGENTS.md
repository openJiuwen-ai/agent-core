# Coordination — 唤醒循环

`TeamAgent` 的事件驱动唤醒层。把传输层来的 `EventMessage` 与内部 poll 计时器产生的 `InnerEventMessage` 收成统一的 `CoordinationEvent`，按粗筛规则放行后由 `AsyncCallbackFramework` 分发到具体的场景 handler。**自身不做业务决策**——handler 通过三类 narrow protocol（`AgentRoundController` / `TeamLifecycleController` / `PollController`）触发行为：`deliver_input` / `cancel_agent` / `resume_interrupt` / `shutdown_self` 落到 TeamHarness，`pause_polls` / `resume_polls` 直达 EventBus，最终驱动 DeepAgent + team tools。

## 文件地图

| 文件 | 类 | 职责 |
|---|---|---|
| `event_bus.py` | `EventBus` / `InnerEventType` / `InnerEventMessage` | 事件入队 + 周期 poll timer + lifecycle（`start(wake_callback=...)` / `stop`）；天然实现 `PollController`（`pause_polls` / `resume_polls`）。**`HUMAN_AGENT` 角色不启动周期 poll timer**（poll 全程被 dispatch mute，起 timer 纯空转）——`start` / `resume_polls` 共用 `_start_poll_tasks`，按 `_periodic_poll_enabled` 单点门控。`InnerEventType.SCHEDULER_SCAN`（F_62）是 self 事件的调度器扫描回声——无 coordination handler 监听，仅 `TeamScheduler` 消费 |
| `dispatcher.py` | `EventDispatcher` + `AgentRoundController` / `TeamLifecycleController` / `PollController` / `DispatcherHost` | 三类 narrow protocol 划分调用面；触发规则（agent_ready / inner-vs-transport / 角色级粗筛）+ 持有私有 `AsyncCallbackFramework` 实例 + 把 7 个固定场景 handler（+ opt-in 的 `ReliabilityHandler`）的 `get_callbacks()` 注册到 framework；handler 实例作为字段直接暴露（`dispatcher.lifecycle / .member / .message / .task_board / .stale_task / .team_completion / .workflow`，及条件存在的 `.reliability`） |
| `handlers/` | `BaseCoordinationHandler` + 7 个固定场景 handler（+ opt-in `ReliabilityHandler`，定义在 `reliability/handler.py`） | 见下文"handler 拆分" |
| `kernel.py` | `CoordinationKernel` | 整体协调 facade：`setup` 时构造 event_bus → 注入到 dispatcher 作 `poll_ctrl`（dispatcher 按静态 `spec.dispatch_mode` 装配；`spec.dispatch_mode == "scheduled"` 的 leader 额外构造休眠态 `TeamScheduler`，F_62）；`start` 时调 `event_bus.start(wake_callback=self._build_wake_callback())` 闭合循环依赖——有调度器时组合 "dispatch → scheduler.on_event"，并在读到已存在 team 行时 `scheduler.activate()`；`notify_team_built()` 是 build_team 成功后的另一激活点；`_filter_self` 丢弃 self `task_*` 事件时改投 `SCHEDULER_SCAN`；start 委托 `SessionManager.bind_session`；pause / resume / drain（pause/stop 入口 `scheduler.deactivate()`） |

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
| `MemberHandler` | 6 个 `MEMBER_*` | 无 | `on_member_event` 分流到 `_handle_leader_member_event` / `_handle_teammate_member_event`；leader 只观测/记录成员生命周期（**不再** stale-claim 跨进程 nudge，见 F_53）；自身 `MEMBER_SHUTDOWN` 时 teammate 不在此 teardown（走 drain → 末轮 → round-end close_stream），**human-agent 走 `_shutdown_human_agent`：有 in-flight round 且非 force 则不打断（靠 round-end close），空闲或 force 才 `_lifecycle.shutdown_self()`** |
| `MessageHandler` | `MESSAGE` / `BROADCAST` / `POLL_MAILBOX` + `MEMBER_SHUTDOWN`（fan-out） | 无 | `on_message_or_broadcast`（leader 额外 ack user-bound + 通知 human-agent inbound）/ `on_poll_mailbox` / `on_member_shutdown_drain`（仅 teammate 给自己 drain）。`_format_message` 经 `inbound_render.render_inbound` 渲染成 `<team-inbound>`（+ `<team-note>`）：human-agent 加 `for="controller"` + `hitt-silence` note，否则带 `reply-hint` note（见 F_46） |
| `TaskBoardHandler` | `TASK_CLAIMED` / `TASK_REVOKED` / 7 个 board `TASK_*`（`CREATED` / `UPDATED` / `STARTED` / `COMPLETED` / `CANCELLED` / `UNBLOCKED` / `RELEASED`） + 3 个 verify 闸 `TASK_*`（`SUBMITTED_FOR_REVIEW` / `VERIFIED` / `REVISION_REQUESTED`）；调度模式子类 `ScheduledTaskBoardHandler` 额外监听 `TASK_REVIEW_VOTE`（纯 board 观测，F_62，见下文「调度模式 handler 变体」） | 无 | `on_task_claimed`（targeted assignment）/ `on_task_revoked` / `on_task_cancelled` / `on_task_updated`（任务被改派 / 取消 / 改内容时，各发带 `member_name` 的 `TASK_REVOKED` / `TASK_CANCELLED` / `TASK_UPDATED` 精准投给受影响成员 → `deliver_input(use_steer=True)`：改派·取消=停手，编辑=重看后继续且任务仍归你。改派经 DAO 原子 CAS 交换 assignee，不发 `TASK_RELEASED`；revoked 非自身忽略，cancelled·updated 非自身 fall-through，见 F_54 / F_56）/ `on_task_board_event` → `_nudge_idle_agent`。**verify 闸（F_59）**：`on_task_submitted_for_review`（payload `reviewer` 列含自己 → steer 去 `verify_task`，否则 board 兜底）/ `on_task_revision_requested`（`member_name`=author，自身 → steer 带 feedback 返工）/ `on_task_verified`（`member_name`=author，自身 → steer 释放去找新活；author 验证期被一活跃卡住）。**leader 收每个 board 事件都巡视全部未完成任务；teammate 只被 `_TEAMMATE_NUDGE_EVENTS`（`CREATED` / `UNBLOCKED` / `RELEASED`——会扩大 claimable 池的三类）唤醒，且 `_nudge_idle_agent` 只渲染 claimable（pending+未指派）任务，别人 in-flight 的任务不喂给 teammate**。`resume_polls` 对每个事件都触发（过滤只砍 nudge、不停 poll）。self 分支经 `inbound_render.render_event(kind="task-assigned")`：human-agent 加 `for="controller"` + `hitt-silence` note（best-effort 查 task title 内联），否则普通 event（见 F_46） |
| `StaleTaskHandler` | `POLL_TASK` | 独占 `_last_stale_nudge` + `_last_pending_nudge` | `on_poll_task` → `_check_stale_claimed_tasks`（**self-only**：只扫本成员自己认领的任务并 `_self_nudge_stale_claim`，`deliver_input(use_steer=False)`；body 只带 task_id + title，见 F_53） + `_check_stale_pending_tasks`（leader 自催 pending，同样 minimal + 非 steer） |
| `TeamCompletionHandler` | `POLL_TASK` / `TASK_LIST_DRAINED` / `TEAM_COMPLETED` | 独占 `_team_completed_emitted` + `_completion_callbacks` | `on_poll_task`（leader idle 时评估 `TeamBackend.is_team_completed()` 并按上升沿发 `TEAM_COMPLETED`；**persistent 团队额外调 `_lifecycle.conclude_completed_round` 结束 leader 流触发 auto-pause，temporary 不调**）/ `rearm`（清上升沿标志，`kernel.start` 每次调，使每个 run cycle 独立判定）/ `on_task_list_drained`（记日志 + fire `_completion_callbacks`，`TeamAgent` 在 deepagent 建好后把 `TeamSkillRail.notify_team_completed` 经 `register_completion_callback` 注册进来）/ `on_team_completed`（消费记日志）|
| `WorkflowHandler` | `WORKFLOW_PROGRESS` | 无 | `on_workflow_progress`（leader-only）：渲染 swarmflow **中途**里程碑（`workflow_started` / `phase`，文案带 `run_id`——多 run 并行下区分局数，`payload.run_id or "?"` 兜底）+ human 等待（`human_prompt` / `human_replied`，**不带 run_id**——人工交互靠 `correlation_id` 路由，设计如此）→ `deliver_input(use_steer=True)`；per-agent 事件不播报（归 4 层 `WorkflowRun`）。**完成 / 失败结果不在此叙述**——由 NativeHarness 异步工具框架（`harness/async_tools.py`）经 `format_*` 闭包 + `harness.send` 回灌（见 `S_20` / `S_21`）。监听独立 event_key，无 fan-out 重叠 |
| `ReliabilityHandler` *(opt-in)* | `ANOMALY_DETECTED` / `MESSAGE` / `BROADCAST` | 持 `RemediationPolicy` + 团队级 `PingPongDetector` | `on_anomaly_detected`（leader-only，按 policy `deliver_input` report / escalate）/ `on_message`（leader-only，跑团队级 ping-pong 检测）/ `handle_local_anomaly`（leader 自监控的本地直投入口，非事件 handler，见 [[F_30_reliability-self-monitor-and-result-aware-repeat]]）。定义在 `reliability/handler.py`（不在 `handlers/`），仅 `TeamAgentSpec.reliability.enabled` 时由 `EventDispatcher` 在 7 个固定 handler **之后**条件注册（见 [[S_19_reliability-framework]]）|

stale-claim nudge 现在是 **self-only**（每个成员 poll 自己的活跃任务——`{PLANNING, IN_PROGRESS, IN_REVIEW}`——并喂自己的 loop），不再有 leader 跨进程催别人的路径，所以也不再需要跨 handler 共享 throttle。`_last_stale_nudge`（活跃任务）与 `_last_pending_nudge`（leader 自催 pending）都是 `StaleTaskHandler` 私有字段，各自节流本进程内的重复 nudge。见 [[F_53_self-only-stale-nudge-minimal-and-append]]。

**调度模式 handler 变体（F_62）**：`task_board.py` / `stale_task.py` 各有一对模式类——`TaskBoardHandler` / `StaleTaskHandler` 是自主模式实现（原样，零模式分支），`ScheduledTaskBoardHandler` / `ScheduledStaleTaskHandler` 是调度模式实现。dispatch_mode 是**静态 spec 配置**：`EventDispatcher` 构造期按 `blueprint.spec.dispatch_mode` 查 `_TASK_BOARD_CLASS` / `_STALE_TASK_CLASS` 字面量表装配（未知值 KeyError），每个角色相同、运行期不变，**绝不在 handler 方法内判模式**。调度类重写的是组合点：`ScheduledTaskBoardHandler` 的 `EVENT_METHOD_MAP` 把 verify 闸三事件与 `TASK_REVIEW_VOTE` 全部路由到 `on_task_board_event`（重写为 resume_polls-only——交接是调度器的 leader 身份邮箱消息，自反应会双投递；leader 感知走调度器摘要/升级），继承 TASK_CLAIMED/REVOKED/CANCELLED/UPDATED 定向 steer（update_task 两模式同义）；`ScheduledStaleTaskHandler.on_poll_task` 只扫自己的 stale 活跃任务（漏投递兜底），不做 leader stale-pending 自催（排队中的 `PENDING(assignee)` 是常态）。见 `S_22_scheduling-runtime`。

## 三条铁律

**铁律 1：coordination 不做决策。** loop 只管 wake-up，所有业务行为由内部 DeepAgent + team tools 驱动。新功能想塞进 dispatcher.py / handler 之前先问：是不是应该用一个新工具实现，让 LLM 自己决定调？

**铁律 2：每个 handler 一个业务域，跨域协作走 framework fan-out。** 新事件类型 = 在对应场景 handler 的 `EVENT_METHOD_MAP` 加一行 + 写方法，不需要改 `dispatcher.dispatch()`。`dispatch()` 只承担"是否该 trigger"的粗筛，**不做** event_type → handler 的具体路由——这是 framework 的职责。如果一个事件需要跨域响应（例如 `MEMBER_SHUTDOWN` 既要更新成员状态也要 drain 邮箱），让两个 handler 各自注册同一 event_key，framework 按注册顺序串行 fan-out —— **不要在一个 handler 内部调另一个 handler 的方法**。

**铁律 3：异常语义。** `AsyncCallbackFramework.trigger()` 吞普通 `Exception`（log + continue），仅 `AbortError` 上抛。和原 fail-fast 语义不同——handler 内部的失败不会让 dispatcher 中断、也不会阻断同一 event 上的其它 fan-out callback。handler 必须自己用 `team_logger.error("...", exc_info=True)` 记录关键失败，不依赖 framework swallow 当作隐式错误处理。

## 跨域协作要点

- **fan-out 顺序由注册顺序决定**：`EventDispatcher.__init__` 中 `(lifecycle, member, message, task_board, stale_task, team_completion, workflow)` 元组顺序就是 framework 注册顺序（opt-in 的 `ReliabilityHandler` 追加在 `workflow` 之后；`workflow` 仅监听独立的 `WORKFLOW_PROGRESS`，不参与 POLL_TASK fan-out）。同 priority（默认 0）下 Python `list.sort` 稳定，故 `MEMBER_SHUTDOWN` 上 `MemberHandler.on_member_event` 先于 `MessageHandler.on_member_shutdown_drain`；`POLL_TASK` 上 stale-task handler 的 `on_poll_task` 先于 `TeamCompletionHandler.on_poll_task`（stale 清扫可能 `deliver_input` 让 leader 转 busy，完成判定要看到这个变化）。改这个元组顺序前先评估 fan-out 影响。
- **`kernel.pause` 停在 iteration 边界、不硬取消**：走 `pause_agent_round()` → `harness.pause()`，round 被保留（`kernel.start` 尾部调 `resume_paused_round()` 原地续跑，否则成员会空等新消息、静默丢掉被暂停的工作）。只有 `stop` / `destroy` 才走 `drain_agent_task()` → `cancel_agent()` → `abort(immediate=True)`；相关 `contextlib.suppress(asyncio.CancelledError, Exception)` 在 `stream_controller.drain_agent_task` —— 改清理路径时检查 `import contextlib` 是否还在（之前漏过一次）。见 [[F_60_native-harness-pause-abort-resume]]。
- **三个 narrow protocol 是 host ↔ handler 的公共契约**：`AgentRoundController` / `TeamLifecycleController` 由 TeamAgent 实现，`PollController` 由 EventBus 实现。新增 handler 依赖前先想清楚：是 round 行为、TeamAgent 级生命周期、还是 poll 控制？把方法加到对应 protocol，不要把所有东西重新塞回 `DispatcherHost`。`start_agent` / `follow_up` / `steer` 故意不在 `AgentRoundController` 上——handler 走 `deliver_input` 让 host 按 round 状态自己分流。
- **构造顺序敏感**：`kernel.setup()` 先建 EventBus，再建 EventDispatcher（注入 `poll_ctrl=event_bus`）；`kernel.start()` 调 `event_bus.start(wake_callback=dispatcher.dispatch)` 闭合循环依赖。EventBus 在 `__init__` 期间不接受 wake_callback——晚到 `start()` 时绑定，避免暴露 setter。
- **session 绑定走 `SessionManager` 的两方法**：
  - `await bind_session(session)`：完整绑定，session 必须非 None。kernel.start 在拿到 session 时调它（落 contextvar 并持 Token、建 per-session DB 表、leader 持久化 config）。
  - `release_session()`：单一 tear-down 路径——pause / stop / session=None 启动全部走这里。reset contextvar Token + 丢 live `AgentTeamSession`。session_id 现在只活在 contextvar 里（参见 [`agent/AGENTS.md`](../AGENTS.md) 四象限），release 后即不可见；resume 路径在重新 `bind_session(new_session)` 时由新 session 提供 id，不再依赖任何缓存。
  在 kernel 里手工再写一遍 contextvar set + DB 表初始化 + persist_leader_config 就是在重复 `_switch_session` 时代的烂代码。

## 跟其它子目录的边界

- `interaction/` 把三视角入口（GodView / Operator / HumanAgent）解析成 `EventMessage` 后才进 dispatcher，不要让 dispatcher / handler 自己解析 mention 字符串——那段已搬到 `interaction/router.py`。
- 真正干活的 LLM 在 `harness/deep_agent.py`，本目录只装配 + 调度。
- 跨 team 的对象池 / 派发 / 并发门禁在 `runtime/`，本目录不感知。
