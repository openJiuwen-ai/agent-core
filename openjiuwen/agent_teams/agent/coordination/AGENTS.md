# Coordination — 唤醒循环

`TeamAgent` 的事件驱动唤醒层。把传输层来的 `EventMessage` 与内部 poll 计时器产生的 `InnerEventMessage` 收成统一的 `CoordinationEvent`，按粗筛规则放行后由 `AsyncCallbackFramework` 分发到具体的场景 handler。**自身不做业务决策**——handler 通过三类 narrow protocol（`AgentRoundController` / `TeamLifecycleController` / `PollController`）触发行为：`deliver_input` / `cancel_agent` / `resume_interrupt` / `shutdown_self` 落到 TeamHarness，`pause_polls` / `resume_polls` 直达 EventBus，最终驱动 DeepAgent + team tools。

## 文件地图

| 文件 | 类 | 职责 |
|---|---|---|
| `event_bus.py` | `EventBus` / `InnerEventType` / `InnerEventMessage` | 事件入队 + 周期 poll timer + lifecycle（`start(wake_callback=...)` / `stop`）；天然实现 `PollController`（`pause_polls` / `resume_polls`）。**`HUMAN_AGENT` 角色不启动周期 poll timer**（poll 全程被 dispatch mute，起 timer 纯空转）——`start` / `resume_polls` 共用 `_start_poll_tasks`，按 `_periodic_poll_enabled` 单点门控。`InnerEventType.SCHEDULER_SCAN`（F_62）是 self 事件的调度器扫描回声——无 coordination handler 监听，仅 `TeamScheduler` 消费 |
| `dispatcher.py` | `EventDispatcher` + `AgentRoundController` / `TeamLifecycleController` / `PollController` / `DispatcherHost` | 三类 narrow protocol 划分调用面；触发规则（agent_ready / inner-vs-transport / 角色级粗筛）+ 持有私有 `AsyncCallbackFramework` 实例 + 把 7 个固定场景 handler（+ opt-in 的 `ReliabilityHandler`）的 `get_callbacks()` 注册到 framework；handler 实例作为字段直接暴露（`dispatcher.lifecycle / .member / .message / .task_board / .stale_task / .team_completion / .workflow`，及条件存在的 `.reliability`） |
| `handlers/` | `BaseCoordinationHandler` + 7 个固定场景 handler（+ opt-in `ReliabilityHandler`，定义在 `reliability/handler.py`） | 唤醒层的决策末梢——"该唤醒谁、怎么唤醒、要不要唤醒"全在这里。策略逻辑见 [`handlers/AGENTS.md`](handlers/AGENTS.md)，下文只留索引表 |
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

**各 handler 的策略逻辑（五个决策维度、各自的分支规则、跨 handler fan-out 的分工契约、调度模式变体）见 [`handlers/AGENTS.md`](handlers/AGENTS.md)。** 下表只做索引——监听什么、有没有状态、一句话职责：

| handler | 监听 event | 状态 | 一句话职责 |
|---|---|---|---|
| `AgentLifecycleHandler` | `USER_INPUT`（inner）/ `STANDBY` / `CLEANED` / `TOOL_APPROVAL_RESULT` / `TASK_PLAN_RESPONSE` | 无 | 本地 agent 生命周期信号直通 host：投递用户输入、暂停 poll、非 leader 收 `CLEANED` 时收摊、审批与 plan 裁决恢复中断 |
| `MemberHandler` | `REFRESH_TEAM_CONTEXT`（inner）+ 6 个 `MEMBER_*` | `_team_clean_requested` | leader 侧纯观测成员生命周期（**不再** stale-claim 跨进程 nudge，见 F_53）+ 全员 SHUTDOWN 后自动 `clean_team`；非 leader 只反应自身事件（`MEMBER_CANCELED` 取消 round，`MEMBER_SHUTDOWN` **只处理 `force`**）；名册变动时让外部 CLI 成员的 runtime 公告待发的团队状态（`announce_team_context()`，见 F_70）|
| `MessageHandler` | `MESSAGE` / `BROADCAST` / `POLL_MAILBOX`（inner）+ `MEMBER_SHUTDOWN`（fan-out） | 无 | 三条投递路径统一先过 `_harness_input_blocked` 闸门（`S_03` 机制 18，退场成员的 graceful teardown 实际发生在这里）；投递前经 `_expand` 做 F_63 两阶段模板渲染，再由 `inbound_render` 渲染成 `<team-inbound>` + reply-hint note。leader 额外 ack user-bound 消息 + 通知 human-agent inbound |
| `TaskBoardHandler` | `INITIAL_POLL_TASK`（inner，F_69）+ 14 个 `TASK_*`：4 个定向所有权（`CLAIMED` / `REVOKED` / `CANCELLED` / `UPDATED`）、3 个 verify 闸（`SUBMITTED_FOR_REVIEW` / `VERIFIED` / `REVISION_REQUESTED`）、`PLAN_RESPONSE`（fan-out）、6 个走看板巡视（`CREATED` / `PLAN_REQUEST` / `STARTED` / `COMPLETED` / `UNBLOCKED` / `RELEASED`）；调度子类额外收 `TASK_REVIEW_VOTE` | 无 | 三组策略：定向所有权变更 steer 给受影响成员、verify 闸 steer 给 reviewer/author、看板巡视按角色分化唤醒（leader 见全部，teammate 只被扩大可处理池的三类唤醒）。首启一次性板巡视也在此 |
| `StaleTaskHandler` | `POLL_TASK`（inner） | `_last_stale_nudge` / `_last_pending_nudge` / `_stale_claim_streak` / `_escalated_claims`（均私有） | 两个按 assignee 划分、互不重叠的 sweep：成员自催名下该推的任务（self-only，连续 3 窗口无效自报 leader），leader 自催无主 PENDING。停滞时长读运行时 idle 时钟，非 DB `updated_at` |
| `TeamCompletionHandler` | `POLL_TASK`（inner）/ `TASK_LIST_DRAINED` / `TEAM_COMPLETED` | `_team_completed_emitted` + `_completion_callbacks`（均私有） | leader 且 round 真空闲时评估三条件完成判定，按上升沿发 `TEAM_COMPLETED`；persistent 团队额外结束 leader 流触发 auto-pause。`rearm()` 让每个 run cycle 独立判定 |
| `WorkflowHandler` | `WORKFLOW_PROGRESS` | 无 | leader-only，叙述 swarmflow **中途**里程碑与 human 等待（完成/失败结果不在此，由异步工具框架回灌）。监听独立 event_key，无 fan-out 重叠 |
| `ReliabilityHandler` *(opt-in)* | `ANOMALY_DETECTED` / `MESSAGE` / `BROADCAST` | 持 `RemediationPolicy` + 团队级 `PingPongDetector` | leader-only 的异常处置与团队级 ping-pong 检测。定义在 `reliability/handler.py`（**不在** `handlers/`），仅 `TeamAgentSpec.reliability.enabled` 时由 `EventDispatcher` 在 7 个固定 handler **之后**条件注册（见 [[S_19_reliability-framework]]）|

**停滞检测的计时源是运行时 idle 时钟，不是 DB `updated_at`**（[[F_65_runtime-idle-clock-stall-nudge]]）——这条跨了本目录与 `agent/` 两处，所以留在这里：`TeamAgentState.idle_since`（`time.monotonic()`，进程本地、不持久化）由 `StreamController._map_state` 在 READY/BUSY 边写入（IDLE 打戳 / RUNNING 清空），handler 经 `AgentRoundController.idle_seconds()` 读取（忙时为 `None`，故正在干活的成员绝不会被判停滞），`kernel.start` 调 `TeamAgent.refresh_idle_baseline()` 在 poll 恢复**之前**重置基线，使 pause 窗口不计入。理由：pause 冻结 `updated_at` 而墙钟继续走，长 pause→resume 后 `now - updated_at` 必然报假停滞；成员状态列本就不落时间戳。阈值经 spec 的 `stale_claim_idle_timeout` / `stale_pending_idle_timeout` 可调（各默认 600s）。**调度模式仍走 `updated_at`**，记为 F_65 已知遗留。

sweep 的扫描面、节流与升级规则，以及调度模式两个 handler 变体的组合点，见 [`handlers/AGENTS.md`](handlers/AGENTS.md)（F_53 / F_62 / F_69）。

## 三条铁律

**铁律 1：coordination 不做决策。** loop 只管 wake-up，所有业务行为由内部 DeepAgent + team tools 驱动。新功能想塞进 dispatcher.py / handler 之前先问：是不是应该用一个新工具实现，让 LLM 自己决定调？

**铁律 2：每个 handler 一个业务域，跨域协作走 framework fan-out。** 新事件类型 = 在对应场景 handler 的 `EVENT_METHOD_MAP` 加一行 + 写方法，不需要改 `dispatcher.dispatch()`。`dispatch()` 只承担"是否该 trigger"的粗筛，**不做** event_type → handler 的具体路由——这是 framework 的职责。如果一个事件需要跨域响应（例如 `MEMBER_SHUTDOWN` 既要更新成员状态也要 drain 邮箱），让两个 handler 各自注册同一 event_key，framework 按注册顺序串行 fan-out —— **不要在一个 handler 内部调另一个 handler 的方法**。

**铁律 3：异常语义。** `AsyncCallbackFramework.trigger()` 吞普通 `Exception`（log + continue），仅 `AbortError` 上抛。和原 fail-fast 语义不同——handler 内部的失败不会让 dispatcher 中断、也不会阻断同一 event 上的其它 fan-out callback。handler 必须自己用 `team_logger.error("...", exc_info=True)` 记录关键失败，不依赖 framework swallow 当作隐式错误处理。

## 跨域协作要点

- **fan-out 顺序由注册顺序决定**：`EventDispatcher.__init__` 中 `(lifecycle, member, message, task_board, stale_task, team_completion, workflow)` 元组顺序就是 framework 注册顺序（opt-in 的 `ReliabilityHandler` 追加在 `workflow` 之后；`workflow` 仅监听独立的 `WORKFLOW_PROGRESS`，不参与 POLL_TASK fan-out）。同 priority（默认 0）下 Python `list.sort` 稳定。改这个元组顺序前先评估 fan-out 影响——**三处共享 event_key 的协作及其分工契约见 [`handlers/AGENTS.md`](handlers/AGENTS.md)**（`MEMBER_SHUTDOWN` / `POLL_TASK` / `TASK_PLAN_RESPONSE`），其中 `POLL_TASK` 那处的顺序是正确性必需，不是巧合。
- **`kernel.pause` 停在 iteration 边界、不硬取消**：走 `pause_agent_round()` → `harness.pause()`，round 被保留（`kernel.start` 尾部调 `resume_paused_round()` 原地续跑，否则成员会空等新消息、静默丢掉被暂停的工作）。只有 `stop` / `destroy` 才走 `drain_agent_task()` → `cancel_agent()` → `abort(immediate=True)`；相关 `contextlib.suppress(asyncio.CancelledError, Exception)` 在 `stream_controller.stop()`（await 已 cancel 的 forwarder task），不在 `drain_agent_task`（后者只是 `cancel_agent()` 的别名）—— 改清理路径时检查 `import contextlib` 是否还在（之前漏过一次）。见 [[F_60_native-harness-pause-abort-resume]]。
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
