# Coordination Protocol

`agent_teams.agent.coordination` 子系统的设计规约：事件总线 + 分发器 + 6 个场景 handler + kernel 装配。本文描述"系统当前是什么样"。

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/agent/coordination/` |
| 最近一次修订日期 | 2026-07-16 |
| 关联 feature | `F_01_coordination-protocol-cleanup.md`、`F_05_lifecycle-finalize-relocation.md`、`F_07_team-completion-events.md`、`F_14_human-agent-team-event-rendering.md`、`F_65_runtime-idle-clock-stall-nudge.md` |

## 范围 / 边界

**这个规约管：**

- `EventBus` 的事件入队、唤醒回调绑定时机、生命周期与 poll 暂停/恢复语义。
- `EventDispatcher` 的粗筛触发规则（`agent_ready` / inner-vs-transport / 角色级 whitelist）以及 6 个 handler 的注册顺序与 fan-out 顺序。
- `BaseCoordinationHandler` 的注入面（5 类窄依赖）、`EVENT_METHOD_MAP` 协议与 `get_callbacks()` 语义。
- `AgentLifecycleHandler` / `MemberHandler` / `MessageHandler` / `TaskBoardHandler` / `StaleTaskHandler` / `TeamCompletionHandler` 各自监听的 event_key、共享状态、关键方法。
- `CoordinationKernel` 的构造装配序、内部 lifecycle state machine（`idle → running → paused → stopped`，pause/stop 幂等）、`start` / `pause` / `stop` / `finalize_round` 路径与 `SessionManager` 的三方法状态机协作。
- 三类窄 protocol（`AgentRoundController` / `TeamLifecycleController` / `PollController`）在 host ↔ handler 之间的契约。
- 异常语义：`AsyncCallbackFramework.trigger` 吞 `Exception`、放行 `AbortError`。

**这个规约不管：**

- mention 字符串解析、`@<member>` 路由——归 `interaction/router.py`。
- 跨 team 的对象池、派发表与并发门禁——归 `runtime/`。
- DeepAgent 内部的 LLM 调度、工具调用、HITL interrupt 语义——归 `harness/`。
- session checkpoint 的字段布局与 namespace 分桶——归 S_04（session-state）。
- TeamAgent 的四象限分解、`SpawnManager` / `StreamController` / `RecoveryManager` 等 manager——归 `S_02_team-agent-architecture` / `S_04_session-and-recovery` / `S_05_member-spawn-and-stream`。

## 不变量

下列断言在系统运行的任意时刻必须为真。任何破坏其中一条的改动都需要先废止本规约。

1. **`EventBus.__init__` 不接受 `wake_callback`**。回调只在 `start(wake_callback=...)` 处绑定；不存在公开 setter。
2. **`EventDispatcher` 构造时必须接收 `poll_ctrl: PollController`**，由 kernel 注入同一个 `EventBus` 实例；handler 不允许通过 host 间接拿 poll 控制。
3. **每个 `BaseCoordinationHandler` 子类必须声明 `EVENT_METHOD_MAP: ClassVar[dict[str, str]]`**，并提供同名 `async` 方法；`get_callbacks()` 输出的 `event_key → bound method` 是 framework 注册的唯一来源。
4. **handler 不持有原始 host 引用**。`BaseCoordinationHandler.__init__` 把 host 同时绑成 `self._round`（`AgentRoundController`）与 `self._lifecycle`（`TeamLifecycleController`），子类只准通过这两个窄字段 + `self._poll` / `self._blueprint` / `self._infra` 访问外部状态。
5. **`EventDispatcher` 不做 event_type → handler 路由**。`dispatch()` 只承担"是否该 trigger"的粗筛，具体 event_key → callback 由 `AsyncCallbackFramework` 解决；同一 event_key 上的多个 handler 走稳定排序的 fan-out。
6. **fan-out 顺序由 `EventDispatcher.__init__` 元组顺序决定**：`(lifecycle, member, message, task_board, stale_task, team_completion, workflow[, reliability])` 即 framework 注册顺序；同 priority（默认 0）下 Python `list.sort` 稳定，因此 `MEMBER_SHUTDOWN` 上 `MemberHandler.on_member_event` 永远先于 `MessageHandler.on_member_shutdown_drain`，`POLL_TASK` 上 stale-task handler 的 `on_poll_task` 永远先于 `TeamCompletionHandler.on_poll_task`。task_board / stale_task 的**类**按构造参数 `dispatch_mode` 查字面量表选定（F_62：`TaskBoardHandler` / `StaleTaskHandler` 为自主模式，`ScheduledTaskBoardHandler` / `ScheduledStaleTaskHandler` 为调度模式，未知值 KeyError）——模式是静态 spec 配置，选择只发生在构造期，handler 方法内无模式分支。
7. **handler 之间不直接互调**。跨域响应必须通过让两个 handler 各自注册同一个 `event_key`、走 framework fan-out 实现。
8. **`AsyncCallbackFramework.trigger` 的异常语义是吞 + 续跑**：普通 `Exception` 被框架 log + continue，仅 `AbortError` 上抛。handler 必须自己用 `team_logger.error("...", exc_info=True)` 记录关键失败，**禁止依赖 framework swallow 当作隐式错误处理**。
9. **stale-claim nudge 是 self-only**（F_53）：每个成员在 `POLL_TASK` 上只扫自己认领的任务并喂自己的 loop（`deliver_input(use_steer=False)`，body 只带 task_id + title），leader 不跨进程催他人。自催连续 `_STALE_CLAIM_ESCALATE_STREAK`（3）个窗口无效时，由**停滞成员自己** `send_message` 上报 leader（成员→leader，与 F_53 砍掉的 leader→成员方向相反，故不违反其设计），leader 据此问询 / 改派 / 换人（F_65）。因此不再有跨 handler 共享的 throttle——`_last_stale_nudge` / `_last_pending_nudge` / `_stale_claim_streak` / `_escalated_claims` 都是 `StaleTaskHandler` 私有，各自节流本进程内同一 task 的重复 nudge。
10. **`HUMAN_AGENT` 角色禁止自主轮询与自主任务板巡视，但接收针对自己的团队事件（渲染成给控制者的通知）**：human-agent 的 `EventBus` 自构造起就不启动周期 poll timer（`EventBus._start_poll_tasks` 按 `_periodic_poll_enabled = role != HUMAN_AGENT` 跳过），所以 `InnerEventType.POLL_TASK` / `InnerEventType.POLL_MAILBOX` 对 human-agent 根本不会被产生；dispatch 入口对这两个 inner event 的 human-agent 短路是双保险（防御性）。transport 事件白名单分两组：**生命周期组** `TeamEvent.CLEANED` / `TeamEvent.MEMBER_SHUTDOWN` / `TeamEvent.MEMBER_CANCELED` / `TeamEvent.STANDBY`（驱动 avatar 自身收摊 / standby，不涉自主 round）；**F_14 团队事件组** `TeamEvent.MESSAGE` / `TeamEvent.BROADCAST` / `TeamEvent.TASK_CLAIMED`（进 avatar harness 后由 `MessageHandler._format_message` / `TaskBoardHandler.on_task_claimed` 的 role-aware 分支渲染成 `hitt.msg_received_for_human` / `hitt.task_assigned_to_self_human`，即"给控制者的通知"，avatar prompt 严格禁止自主行动）。其余事件保持静音——尤其是任务板巡视事件（`TASK_CREATED` / `TASK_UPDATED` / ... → `_nudge_idle_agent`）和 stale-claim self-nudge（human-agent 不轮询，自然也不会产生），它们会驱使 avatar 自主扫描任务板找活。配套：`TASK_CLAIMED` 里"指派给别人"的认领对 human-agent **不** fall-through 到任务板 nudge（`TaskBoardHandler.on_task_claimed` 在 `is_self_human` 时早退），只有指派给 avatar 自己的认领才 deliver。其中 `MEMBER_SHUTDOWN` 放行是因为 avatar 要靠它排空邮箱并收摊（收摊规则见机制 18，与 teammate 同一套，**不再有 human-agent 专属分支**）。
11. **dispatch 前置检查 `is_agent_ready()`**：未就绪时直接 return，handler 不会被触发。
12. **`SHUTDOWN` inner 事件是 EventBus 自身的停机信号**，仅由 `EventBus.stop()` 写入；handler 不监听该事件。
13. **session 绑定状态机是三态**：未绑（id=None, ts=None）/ 完整（id=X, ts=session）/ 半绑（id=X, ts=None）。`bind_session(session)` 进完整态、`release_session()` 完整→半绑、`unbind_session()` 任意态→未绑。`kernel.start` 在调用面显式分流：session 非 None 走 bind，否则走 unbind；不依赖被调方分支。
14. **kernel lifecycle 单调推进**：`CoordinationKernel._lifecycle_state` 是显式状态机 `idle → running → paused → stopped`：`start()` 把状态推到 `running`，`pause()` 把 `running → paused`，`stop()` 把 `running | paused → stopped`，`stopped`/`idle` 上调 `pause()` 是 no-op，`idle`/`stopped` 上调 `stop()` 是 no-op。这保证 Runner-level finally（`manager.finalize`）即使在外部 `stop_coordination` 已经发生后再调 `pause_coordination`，也只是空转，不会把"已经 stop"的状态机硬倒回 paused。
15. **finalize_round 不做团队去向决策**：`finalize_round` 只负责 memory extract + 释放 stream_queue 引用——这是**纯**的 round-end cleanup hook。pause vs stop 的决策由 Runner finally 路径上的 `TeamRuntimeManager.finalize` / `finalize_member` 拥有（详见 `S_06_runtime-pool-dispatch`），coordination 不可在此处偷偷 pause/stop kernel。
16. **leader stop 也 mark teammate 持久状态**：`kernel.stop()`（leader 角色）在 tear down spawn handles 之前调 `_mark_live_teammates(STOPPED)`，与 pause 路径调 `_mark_live_teammates(PAUSED)` 对称。区分点是**为什么** teammate runtime 不在了——团队仍 live、由外部 stop_team 触发就是 `STOPPED`；自然 round 间 idle 就是 `PAUSED`。该写入是 `_mark_live_teammates` 唯一调用方，禁止散落 `team_member.update_status(STOPPED)` 字面量。
17. **teammate `stop_coordination` 不写持久状态**：teammate 侧 kernel 的 `stop()` 不调 `team_member.update_status(...)`——`team_member` 的持久态由 `TeamRuntimeManager.finalize_member` 在 Runner finally 中决定（详见 `S_06`），让 kernel 只关心进程内 runtime。理由：leader 主动 stop 团队时已经把每个 teammate 的 `team_member.status` 写成 `STOPPED`，若 teammate 自己 kernel.stop 再回写一次 `SHUTDOWN`，会触发 `kernel.start` 启动期的"所有成员都 SHUTDOWN → clean_team"自保，把一个本应可恢复的团队误删。
18. **喂 harness 之前先过 `MessageHandler._harness_input_blocked` 闸门；成员退场是「状态驱动」而非「事件驱动」，且对所有非 leader 角色同一套**。handler 层任何把消息喂进本成员 harness 的路径（`on_message_or_broadcast` / `on_poll_mailbox` / `on_member_shutdown_drain`，统一经 `_process_unread_messages`）都先读**本成员在 DB 里的 status** 决定三选一：

    | 本成员 status | round 在飞 | 动作 |
    |---|---|---|
    | `SHUTDOWN` | — | **丢弃投递**。成员已彻底退场，绝不唤醒一个已死的 harness |
    | `SHUTDOWN_REQUESTED` | 否 | **不唤醒，直接 `shutdown_self()` 落 `SHUTDOWN`**。没有 round 可搭便车，为了让 LLM 读一句"你要被关了"而起一整轮纯属烧 token 说告别 |
    | `SHUTDOWN_REQUESTED` | 是 | **放行**。消息 steer 进在飞的那一轮，该轮 round-end 自检 `close_stream`（teammate 一直走的老路），不打断成员当前回合 |

    **必须读 DB status，不能读 `MEMBER_SHUTDOWN` 事件**：`TeamBackend.shutdown_member` 的顺序是「写 `SHUTDOWN_REQUESTED` → 发 shutdown 通知消息 → 发 `MEMBER_SHUTDOWN` 事件」，所以那条通知是作为**普通 MESSAGE 先到**的。若闸门 key 在事件上，这条消息早就把空闲 harness 唤醒跑完一轮了，事件再到已然太晚——这正是 `_shutdown_human_agent` 时代 human-agent 的"空闲直接收摊"分支几乎从不生效的原因。

    **`MemberHandler` 只保留 `force`**（立即 `shutdown_self()`，不给成员收尾的 round，用于卡死 / 无响应成员），且同样角色无关；graceful 退场全部由上面的闸门决定。fan-out 顺序 `MemberHandler` → `MessageHandler` 保证 force 先落 `SHUTDOWN`，随后的 drain 读到 `SHUTDOWN` 直接丢弃投递，不会重复 teardown、也不会往死 harness 里塞消息。

    结果是 **`MemberHandler._shutdown_human_agent` 这个 human-agent 专属分支被删除**，`on_member_shutdown_drain` 的 `role == TEAMMATE` 限制也一并删除（BRIDGE_AGENT 随之纳入同一条路径）。
19. **给真实人类的输出与喂 avatar harness 是两条独立的路，且前者不受后者影响**。leader 侧 `MessageHandler._notify_human_agent_inbound` 把团队消息推给控制者回调，这条路**不经过** avatar 的 harness，因此机制 18 跳过 harness 唤醒**永远不会**让真人少收一条消息。其收件人按**可达**（`TeamBackend.reachable_human_agent_names` / `is_reachable_human_agent`，即 status ∉ `MEMBER_UNREACHABLE_STATUSES` = {`SHUTDOWN`}）筛选，**不是**裸 role：已彻底退场的人类，其控制者不该再收团队广播。**但 `SHUTDOWN_REQUESTED` 必须留在收件人里**——`shutdown_member` 先写状态再发通知，若在 request 处就切断投递，唯一送不到的恰恰是"你已被移出团队"这条通知。这与任务锁的阈值（`MEMBER_DEPARTED_STATUSES` = {`SHUTDOWN_REQUESTED`, `SHUTDOWN`}，见 `S_07` 运行约束 3）**故意不同**：锁在「leader 已放手」时就该松开，投递要到「人真的没了」才该停。两个阈值定义在 `schema/status.py`，改动前先读那里的注释。

20. **自主模式的停滞计时一律读运行时 idle 时钟，绝不读 DB `task.updated_at`**（F_65）。`TeamAgentState.idle_since`（`time.monotonic()`，**进程本地、内存态、不持久化**）由 `StreamController._map_state` 在 READY/BUSY 边写入（`IDLE →` 打戳、`RUNNING →` 清空），handler 经 `AgentRoundController.idle_seconds()` 读取，`kernel.start` 调 `TeamAgent.refresh_idle_baseline()` 在每个 run cycle 入口、event bus 恢复 `POLL_TASK` **之前**重置基线。三条理由：(a) pause 冻结 `updated_at` 而墙钟继续走，`now - updated_at` 在长 pause→resume 后必然报出假停滞；(b) 成员状态列本就不落时间戳（`TeamMember.updated_at` 只随名册变更 bump），DB 层无从回答"READY 了多久"；(c) `idle_seconds()` 在忙时为 `None`，使"正在干活的成员被判停滞"在类型层面不可表达。**仅 `refresh_idle_baseline` 覆盖 idle-across-pause 这一路**：pause 时正忙的成员 `idle_since` 本就是 `None`，其 round 续跑后自然重新打戳。**调度模式仍读 `updated_at`**（F_65 范围外；同款 pause 缺陷记为已知遗留），二者的隔离靠 `ScheduledStaleTaskHandler` 覆写 `_check_stale_claimed_tasks` / `_self_nudge_stale_claim` 钉住旧实现，自主基类内不留 `updated_at` 痕迹。

## 接口契约

### 三类窄 Protocol

```python
@runtime_checkable
class AgentRoundController(Protocol):
    """Round-level control surface against the owning agent."""

    def is_agent_ready(self) -> bool: ...
    def is_agent_running(self) -> bool: ...
    def idle_seconds(self) -> float | None: ...   # F_65；None = 忙 / 从未 settle
    def has_in_flight_round(self) -> bool: ...
    def has_pending_interrupt(self) -> bool: ...

    async def cancel_agent(self) -> None: ...
    async def deliver_input(self, content: Any, *, use_steer: bool = True) -> None: ...
    async def resume_interrupt(self, user_input: Any) -> None: ...


@runtime_checkable
class TeamLifecycleController(Protocol):
    """TeamAgent-level lifecycle effects that span multiple managers."""

    async def shutdown_self(self) -> None: ...
    async def conclude_completed_round(self, member_count: int, task_count: int) -> None: ...


@runtime_checkable
class PollController(Protocol):
    """Periodic-poll control surface owned by the coordination event bus."""

    async def pause_polls(self) -> None: ...
    async def resume_polls(self) -> None: ...


@runtime_checkable
class DispatcherHost(AgentRoundController, TeamLifecycleController, Protocol):
    """Composite host contract: round + team-lifecycle, no poll control."""
```

实现者：

| Protocol | 实现者 | 注入位置 |
|---|---|---|
| `AgentRoundController` | `TeamAgent`（间接通过 `TeamHarness`） | handler `self._round`、dispatcher `self._round` |
| `TeamLifecycleController` | `TeamAgent` | handler `self._lifecycle` |
| `PollController` | `EventBus` | dispatcher `poll_ctrl`、handler `self._poll` |
| `DispatcherHost` | `TeamAgent`（同时实现前两个） | `EventDispatcher.__init__(host=...)`、`BaseCoordinationHandler.__init__(host=...)` |

`AgentRoundController` 故意不暴露 `start_agent` / `follow_up` / `steer`——handler 走 `deliver_input`，由 host 按 round 状态自己分流到这三种原语之一。新增 round 行为前先问：能不能由 host 自己分流？不能再加方法。

### EventBus

```python
class EventBus:
    def __init__(
        self,
        *,
        role: TeamRole,
        mailbox_poll_interval: float = 30.0,
        task_poll_interval: float = 30.0,
    ) -> None: ...

    @property
    def role(self) -> TeamRole: ...
    @property
    def is_running(self) -> bool: ...
    @property
    def polls_paused(self) -> bool: ...

    async def start(self, *, wake_callback: WakeCallback | None = None) -> None: ...
    async def stop(self) -> None: ...
    async def pause_polls(self) -> None: ...   # PollController
    async def resume_polls(self) -> None: ...  # PollController
    async def enqueue(self, event: CoordinationEvent) -> None: ...
```

- 构造期不接 `wake_callback`，唯一绑定时机是 `start()`。`start(wake_callback=None)` 保留前一次的回调（或 `None`，仅生命周期测试用）。
- `stop()` 在改 running 状态前先把 `_polls_paused` 复位，保证下次 `start()` 不会继承"上次 pause 留下的 paused=True"。
- `pause_polls()` 只 cancel 两个 poll task，主事件循环继续接收 transport 事件；`resume_polls()` 在 `_running=True` 且 `_polls_paused=True` 时重建 poll task。
- `_run_loop` 看到 `InnerEventMessage(event_type=SHUTDOWN)` 是退出信号；其它事件原样递给 `wake_callback`。回调内部异常被 `_run_loop` log + 续跑（与 framework swallow 行为一致）。

### Inner 事件

```python
class InnerEventType(str, Enum):
    USER_INPUT = "user_input"
    POLL_MAILBOX = "coordination_poll_mailbox"
    POLL_TASK = "coordination_poll_task"
    SCHEDULER_SCAN = "scheduler_scan"   # F_62：self 事件的调度器扫描回声，无 handler 监听
    SHUTDOWN = "shutdown"


class InnerEventMessage(BaseModel):
    event_type: InnerEventType
    payload: dict[str, Any]


CoordinationEvent = InnerEventMessage | EventMessage
```

`InnerEventMessage` 是 coordination 层私有的进程内事件类型，与跨进程的 `EventMessage` 隔离。dispatch 只看 `event.event_type`：`InnerEventMessage` 走 `event.event_type.value`（str-Enum），`EventMessage` 直接用 `event.event_type`（`TeamEvent.*` 是裸 str 常量）。

### EventDispatcher

```python
class EventDispatcher:
    def __init__(
        self,
        host: DispatcherHost,
        blueprint: TeamAgentBlueprint,
        infra: TeamInfra,
        poll_ctrl: PollController,
        *,
        dispatch_mode: str = "autonomous",
    ) -> None: ...

    # Public attributes — access in tests / debugging
    lifecycle: AgentLifecycleHandler
    member:    MemberHandler
    message:   MessageHandler
    task_board: TaskBoardHandler        # scheduled 模式下是 ScheduledTaskBoardHandler
    stale_task: StaleTaskHandler        # scheduled 模式下是 ScheduledStaleTaskHandler
    team_completion: TeamCompletionHandler

    @property
    def dispatch_mode(self) -> str: ...

    async def dispatch(self, event: CoordinationEvent) -> None: ...
```

`dispatch_mode` 是**静态 spec 配置**（kernel 传入 `blueprint.spec.dispatch_mode`，每个角色
相同），决定 task_board / stale_task 的**类**（`_TASK_BOARD_CLASS` / `_STALE_TASK_CLASS`
字面量表，未知值 KeyError）。全部 handler 在构造期一次装配注册，运行期不变（F_62：模式
差异属于类，选择属于装配点，handler 方法内无模式分支——调度类的行为差异见 `S_22` 不变量 14）。

`dispatch()` 流程：

1. `is_agent_ready()` 否 → 直接 return。
2. `InnerEventMessage` 分支：
   - `HUMAN_AGENT` 角色 + `POLL_TASK / POLL_MAILBOX` → return（防御性短路，机制 1）。
   - 否则 `await framework.trigger(event_type.value, event)`。
3. transport `EventMessage` 分支：
   - `blueprint.member_name` 缺失 → return。
   - `HUMAN_AGENT` 角色 + 不在 `{CLEANED, MEMBER_SHUTDOWN, MEMBER_CANCELED, STANDBY, MESSAGE, BROADCAST, TASK_CLAIMED}` 白名单 → return（机制 2；前四类是生命周期事件，后三类是 F_14 渲染成"给控制者通知"的团队事件）。
   - 否则 `await framework.trigger(event.event_type, event)`。

handler 实例作为公共属性暴露（`dispatcher.lifecycle / .member / ...`），用于测试断言与调试访问。

### BaseCoordinationHandler

```python
class BaseCoordinationHandler:
    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {}

    def __init__(
        self,
        host: DispatcherHost,
        blueprint: TeamAgentBlueprint,
        infra: TeamInfra,
        poll_ctrl: PollController,
    ) -> None:
        self._round: AgentRoundController = host
        self._lifecycle: TeamLifecycleController = host
        self._poll = poll_ctrl
        self._blueprint = blueprint
        self._infra = infra

    def get_callbacks(self) -> dict[str, EventCallback]: ...
```

子类必须：

- 声明 `EVENT_METHOD_MAP`，列出本 handler 监听的 `event_key`。inner event 用 `InnerEventType.X.value`；transport event 用 `TeamEvent.X` 裸串。
- 实现表中所有 `method_name` 对应的 `async def`，签名为 `async def m(self, event: <EventMessage | InnerEventMessage>) -> None`。
- 不通过 `host` 间接访问外部状态——只走 `_round` / `_lifecycle` / `_poll` / `_blueprint` / `_infra` 五个窄字段。
- 新增依赖时不允许重新塞回 `DispatcherHost`：是 round 行为加到 `AgentRoundController`，是 TeamAgent 级生命周期加到 `TeamLifecycleController`，是 poll 控制加到 `PollController`，否则通过显式构造参数注入。

### 6 个场景 Handler

下表列出每个 handler 的事件订阅、共享状态与关键方法。所有 handler 的 `__init__` 只接收 `(host, blueprint, infra, poll_ctrl)`——stale-claim nudge 已收敛为 self-only（见 F_53），不再有跨 handler 共享的 throttle。

| handler | 监听 event_key | 共享 / 私有状态 | 关键方法 |
|---|---|---|---|
| `AgentLifecycleHandler` | `InnerEventType.USER_INPUT` / `TeamEvent.STANDBY` / `TeamEvent.CLEANED` / `TeamEvent.TOOL_APPROVAL_RESULT` | 无 | `on_user_input` → `_round.deliver_input`；`on_standby` → `_poll.pause_polls`；`on_cleaned` → `_lifecycle.shutdown_self`（leader 跳过）；`on_tool_approval_result` → 构造 `InteractiveInput` → `_round.resume_interrupt` |
| `MemberHandler` | 6 个 `MEMBER_*`：`SPAWNED` / `RESTARTED` / `STATUS_CHANGED` / `EXECUTION_CHANGED` / `SHUTDOWN` / `CANCELED` | 无 | `on_member_event` 按 role 分流到 `_handle_leader_member_event` / `_handle_teammate_member_event`；leader 只观测/记录成员生命周期，**不再** stale-claim 跨进程 nudge（F_53 把 stale-claim 收敛为 self-only）；非 leader 仅对自身事件反应：`MEMBER_CANCELED` 触发 `_round.cancel_agent`，`MEMBER_SHUTDOWN` **仅在 `force` 时** `_lifecycle.shutdown_self()`（角色无关）。graceful shutdown 不在此 teardown——走 `MessageHandler` 的 drain + harness-input 闸门（机制 18） |
| `MessageHandler` | `TeamEvent.MESSAGE` / `TeamEvent.BROADCAST` / `InnerEventType.POLL_MAILBOX` / `TeamEvent.MEMBER_SHUTDOWN`（fan-out） | 无 | `on_message_or_broadcast`：leader 在 MESSAGE 上 `_ack_user_bound_message` + `_notify_human_agent_inbound`（收件人按**可达**筛选，见机制 19），所有 role 接着 `_poll.resume_polls` + `_process_unread_messages`；`on_poll_mailbox` 周期 sweep；`on_member_shutdown_drain` 非 leader 给自己 drain（`use_steer=True`，不再限 teammate）。**三条路统一先过 `_harness_input_blocked` 闸门**（机制 18）：已 `SHUTDOWN` 丢弃投递，`SHUTDOWN_REQUESTED` 且无 in-flight round 则直接 `shutdown_self()` 不唤醒 harness |
| `TaskBoardHandler` | `TeamEvent.TASK_CLAIMED` / `TASK_REVOKED` / `TASK_CANCELLED` / `TASK_UPDATED` + 4 个 board `TASK_*`：`CREATED` / `COMPLETED` / `UNBLOCKED` / `RELEASED` | 无 | `on_task_claimed`：targeted 自身 → `deliver_input`；非自身 → 走 `on_task_board_event` 兜底（防 idle leader 错过 board 变更；teammate 在下一句的过滤下被挡掉，实际只 nudge leader）。`on_task_board_event` → `_nudge_idle_agent`：**leader 每个 board 事件都巡视全部未完成任务；teammate 只被 `_TEAMMATE_NUDGE_EVENTS`（`CREATED` / `UNBLOCKED` / `RELEASED`——会扩大 claimable 池的三类）唤醒，且只渲染 claimable（pending+未指派）任务，别人 in-flight 的 claimed 任务不喂给 teammate**；`resume_polls` 对每个事件都触发（过滤只砍 nudge、不停 poll）。leader 全部任务终结时按 lifecycle 喂 all-done prompt。`TASK_RELEASED` 由 `TeamTaskManager.reset`（成员清理 / leader 改指派）发布，把被释放回 pending 的任务重新播给 idle teammate。`on_task_revoked` / `on_task_cancelled` / `on_task_updated`（F_54 / F_56）：任务被从某成员手里动了（改派 / 取消 / 改内容）时，`TeamTaskManager` 发对应事件并带 `member_name`（受影响成员），精准投给该成员 → self 分支 `deliver_input(use_steer=True)`（撤回=改派走了，停手；取消=任务没了，停手；变更=view_task 重看后继续、任务仍归你）。改派经 `TeamTaskManager.reassign` 的 DAO 原子 CAS 交换 assignee（任务全程 CLAIMED），**不发 `TASK_RELEASED`**（F_56，消除对空闲 teammate 的 spurious 唤醒 + reset→assign 抢占窗口）；`on_task_revoked` 非自身忽略，`on_task_cancelled` / `on_task_updated` 非自身 fall-through 到 `on_task_board_event`（leader 巡视兜底，与 `on_task_claimed` 一致） |
| `StaleTaskHandler` | `InnerEventType.POLL_TASK` | `_last_stale_nudge` / `_last_pending_nudge: dict[str, float]` + `_stale_claim_streak: dict[str, int]` + `_escalated_claims: set[str]`（均私有） | `on_poll_task` → `_check_stale_claimed_tasks` + `_check_stale_pending_tasks`；两者的停滞时长都取自 `self._round.idle_seconds()`（不变量 20，**非** `task.updated_at`），阈值取自 spec 的 `stale_claim_idle_timeout` / `stale_pending_idle_timeout`（各默认 600s）。stale claim（**self-only**）：本成员 idle 超阈值且仍持有 `{PLANNING, IN_PROGRESS}` 任务（**排除 `IN_REVIEW`**——author 等 reviewer 裁决是正常 idle）→ `_self_nudge_idle_claim` → `deliver_input(use_steer=False)`，body 只带 task_id + title + idle 分钟；连续 `_STALE_CLAIM_ESCALATE_STREAK`(3) 个窗口自催无效 → `_escalate_stale_claim` 由该成员 `send_message` 上报 leader（每任务只报一次，记账随任务离开 owned-active 集 GC）。stale pending（仅 leader）：leader 自己 idle 超阈值 **且** 存在**无 assignee** 的 PENDING **且** roster 中至少一个非 leader 成员 READY（全员忙 = 正常排队，不催）→ 自喂 minimal prompt（id+title、非 steer）、由 LLM 决定如何分派。见 F_53 / F_65 |
| `TeamCompletionHandler` | `InnerEventType.POLL_TASK` / `TeamEvent.TASK_LIST_DRAINED` / `TeamEvent.TEAM_COMPLETED` | `_team_completed_emitted: bool` + `_completion_callbacks: list`（私有） | `on_poll_task`：leader 且 idle 时评估 `TeamBackend.is_team_completed()`（任务全终态 + 成员全 settled + 无任何未读消息，含广播），按上升沿发 `TEAM_COMPLETED`、下降沿重新武装；**persistent 团队额外调 `self._lifecycle.conclude_completed_round(...)` 结束 leader 流触发 auto-pause（temporary 不调，靠 clean_team 收尾）**。`rearm()`：清上升沿幂等标志，`kernel.start`（冷启动 / resume / recover）每次调一次，使每个 run cycle 独立判定完成。`on_task_list_drained`：记日志 + fire `_completion_callbacks`（`TeamAgent` 在 deepagent 建好后经 `register_completion_callback` 把 `TeamSkillRail.notify_team_completed` 注册进来，注册表非空即 gate）。`on_team_completed`：消费记日志。`POLL_TASK` 与 `StaleTaskHandler` 共享 event_key，注册靠后 → fan-out 在其之后 |

### CoordinationKernel

```python
class CoordinationKernel:
    def __init__(self, host: TeamAgent) -> None: ...

    @property
    def event_bus(self) -> EventBus | None: ...
    @property
    def dispatcher(self) -> EventDispatcher | None: ...
    @property
    def is_running(self) -> bool: ...

    def setup(self, *, role: TeamRole) -> None: ...
    async def start(self, session: Any = None) -> None: ...
    async def pause(self) -> None: ...
    async def stop(self) -> None: ...

    async def enqueue(self, event: Any) -> None: ...
    async def enqueue_user_input(self, inputs: Any) -> None: ...
    async def enqueue_initial_mailbox_poll(self) -> None: ...
    async def wake_mailbox_if_interrupt_cleared(self) -> None: ...

    async def subscribe_transport(self, team_name: str) -> None: ...
    async def unsubscribe_transport(self) -> None: ...

    async def drain_agent_task(self) -> None: ...
    def close_stream(self) -> None: ...
    async def finalize_round(self) -> None: ...
```

构造与启动序约束：

1. `setup(role=...)`：先建 `EventBus(role=role)`，再建 `EventDispatcher(host, blueprint, infra, poll_ctrl=event_bus)`。**此时不绑 wake_callback**——避免引入"构造完但 callback 缺失"的中间态。
2. `start(session=...)`：在子系统初始化（DB、recover、workspace、memory toolkit）之后调用 `event_bus.start(wake_callback=self._build_wake_callback())`，闭合 EventBus ↔ EventDispatcher 的循环依赖。这是 wake_callback 唯一允许出现的绑定时机。存在调度器（F_62：仅 `spec.dispatch_mode == "scheduled"` 的 leader kernel 在 setup 构造）时，wake 组合为"coordination dispatch → scheduler.on_event"顺序执行，其余保持裸 `dispatcher.dispatch`；leader 分支读到已存在的 team 行且调度器存在时 `scheduler.activate()`——激活扫描即恢复。`start` 末尾把 `_lifecycle_state` 推进到 `"running"`。
3. session 绑定状态转换走 `SessionManager` 的三方法，**不在 kernel 里手工写 session_id / contextvar / DB 表初始化 / persist_leader_config**：

   | 调用位点 | 方法 | 状态转换 |
   |---|---|---|
   | `start(session)` 且 `session is not None` | `await sess_mgr.bind_session(session)` | * → 完整态 |
   | `start(session=None)` | `sess_mgr.unbind_session()` | * → 未绑态 |
   | `pause()` / `stop()` | `sess_mgr.release_session()` | 完整 → 半绑 |

4. `pause()` 路径（仅 `_lifecycle_state == "running"` 时执行；否则 no-op）：`drain_agent_task` → `persist_allocator_state` → leader 额外 `_mark_live_teammates(PAUSED)` + `cancel_recovery_tasks` + `shutdown_all_handles` + 持久化 lifecycle="paused" → leader 发布 `TEAM_STANDBY` → `unsubscribe_transport` → `event_bus.stop()` → `close_stream` → `release_session` → `_lifecycle_state = "paused"`。`team_member` 持久态不在此处改（持久状态归 `manager.finalize_member`）。
5. `stop()` 路径（`_lifecycle_state ∈ {"idle", "stopped"}` 时 no-op；`"paused"` 也允许进入，跑剩下的资源清理）：`drain_agent_task` → `persist_allocator_state` → leader 额外 `_mark_live_teammates(STOPPED)`（与 pause 路径对称，写"为什么 runtime 不在了"）→ `unsubscribe_transport` → `cancel_recovery_tasks` + `shutdown_all_handles` → `memory_manager.close()` → `event_bus.stop()` → `close_stream` → `release_session` → `_lifecycle_state = "stopped"`。teammate kernel 的 `stop()` 不写 `team_member.status`（让 `manager.finalize_member` 决定该写 SHUTDOWN 还是保留外部已写的 STOPPED）。
6. `finalize_round()`：纯 round-end hook，仅做两件事——`memory_manager.extract_after_round()` + `stream_controller.stream_queue = None`。**不调** `pause()` / `stop()`、不读 `lifecycle` / `shutdown_requested`、不写 `team_member.status`。pause vs stop 决策已上移到 Runner finally 路径的 `manager.finalize` / `finalize_member`（见 `S_06`）。
7. `subscribe_transport`：`messager.register_direct_message_handler(event_bus.enqueue)` + 对每个 `TeamTopic` `subscribe(topic_str, _filter_self)`；`_filter_self` 先派发 listener、再过滤 `sender_id == local_member_name` 的自发事件，最后 `event_bus.enqueue(event)`。**F_62 例外**：丢弃 self 事件时，若调度器激活且事件类型以 `task_` 开头，改投一个 `InnerEventMessage(SCHEDULER_SCAN)`——coordination 不重放 self 事件，但 leader 侧调度器仍能观察到 leader 进程自己造成的看板变化（详见 `S_22_scheduling-runtime`）。
8. `notify_team_built()`（F_62）：`TeamAgent._mark_team_built` 在 build_team 成功后调用；存在调度器（scheduled 团队的 leader）则 `scheduler.activate()`，先于任何 teammate spawn / create_task。`pause()` / `stop()` 入口处对称 `scheduler.deactivate()`。

### `_mark_live_teammates(target_status)`

```python
async def _mark_live_teammates(self, target_status: MemberStatus) -> None: ...
```

leader-only。遍历 spawn_manager 持有的 live teammate handle，跳过 `UNSTARTED` /
`SHUTDOWN` 等"runtime 未起" 或 "已永久退场"的成员，把剩下成员的 `team_member.status`
写成 `target_status`：

- `pause()` 传 `MemberStatus.PAUSED` ——自然 round-end，下一次 `recover_team` 把它们当 live teammate 接着拉起。
- `stop()` 传 `MemberStatus.STOPPED` ——外部 stop_team，team 未解散，`recover_team` 也能从这里恢复（`MEMBER_TRANSITIONS` 允许 `STOPPED → RESTARTING`）。

调用顺序刻意放在 `shutdown_all_handles` 之前：进程内 task 一旦 cancel，messager
事件可能立即吐出（teammate 退出广播 `MEMBER_SHUTDOWN`），如果先 cancel 再写持久
状态，会与 teammate 自己写下的状态产生 race。

### Inner 事件入队工具

| 方法 | 用途 | 角色约束 |
|---|---|---|
| `enqueue_user_input(inputs)` | 把字符串或 `{"query": ...}` dict 包成 `InnerEventMessage(USER_INPUT)` 入队，`invoke()` / `interact()` 共用 | 无 |
| `enqueue_initial_mailbox_poll()` | 直接投首个 `POLL_MAILBOX`，让 teammate 起来后做第一次邮箱 sweep（成员 runtime 已由 `start` 先行启动，单 supervisor 模型下 `FirstIterationGate` 已删，不再等门控） | 仅 teammate（leader 直接 return） |
| `wake_mailbox_if_interrupt_cleared()` | 在没有 pending interrupt 时投 `POLL_MAILBOX`，避免 interrupt 解除后邮件留底 | 仅 teammate |

### 四铁律

1. **coordination 不做决策。** EventBus / EventDispatcher / handler 都只做 wake-up 与粗筛触发；业务行为由内部 DeepAgent + team tools 驱动。新功能想塞进 `dispatcher.py` 或 handler 之前先问：**是不是应该实现成一个新工具，让 LLM 自己决定调？**
2. **每个 handler 一个业务域，跨域协作走 framework fan-out。** 新事件类型 = 在对应场景 handler 的 `EVENT_METHOD_MAP` 加一行 + 写方法，不需要改 `dispatch()`。同一 event_key 的多 handler 通过注册顺序串行 fan-out；handler 不互相 import、不互相调用。
3. **异常语义。** `AsyncCallbackFramework.trigger()` 吞普通 `Exception`（log + continue），仅 `AbortError` 上抛。handler 必须自己用 `team_logger.error("...", exc_info=True)` 记录关键失败，不允许把 framework swallow 当作隐式错误处理。
4. **kernel 不决策团队去向。** `pause()` / `stop()` 是 idempotent 的 runtime tear-down 原语（含 leader 写 PAUSED/STOPPED 标记），但**它们何时被调用、能不能被调用**由 Runner finally 上的 `manager.finalize` / `finalize_member` 决定。kernel 自己的 `finalize_round` 仅做 memory extract + stream_queue 释放，**禁止**在那里隐式 pause/stop——否则外部 `stop_team` 会被 finally 偷偷盖回 paused。

## 数据结构

### 运行时 idle 时钟（F_65）

```python
# agent/state.py — TeamAgentState（跨 operator 象限）
idle_since: Optional[float] = None   # time.monotonic()；None = 忙 / 从未 settle
```

- **进程本地、内存态、不持久化**，也**不来自 DB**：见不变量 20 的三条理由。
- 写：`StreamController._map_state`（`IDLE →` 打戳 / `RUNNING →` 清空）。读：`TeamAgent.idle_seconds()`，经 `AgentRoundController` 暴露给 handler。重置：`TeamAgent.refresh_idle_baseline()`，由 `kernel.start` 在 event bus 恢复 poll **之前**调用。
- 生命周期与 round 绑定，不跨 run cycle 携带语义：冷启动天然为 `None`；热 resume 由 `refresh_idle_baseline` 把仍 idle 的成员重新起算。

### stale-nudge 记账字典

```python
# All private to StaleTaskHandler — stale-claim nudge is self-only (F_53),
# so there is no cross-handler shared throttle any more.
self._last_stale_nudge: dict[str, float] = {}    # claimed:  task_id -> last-nudge wall-clock seconds
self._last_pending_nudge: dict[str, float] = {}  # pending (leader only): same
self._stale_claim_streak: dict[str, int] = {}    # task_id -> consecutive fruitless stale windows
self._escalated_claims: set[str] = set()         # task_ids already reported to the leader
```

- 只有 `POLL_TASK` 一条触发路径（成员扫自己的活跃任务），同一 task 在 `stale_claim_idle_timeout`（默认 10min）内只 nudge 一次。**节流用墙钟秒（`time.time`），停滞判定用 idle 时钟**——两者单位与用途不同，不要混。
- `_stale_claim_streak` 累计的是"该任务被催了几个窗口"，成员中途忙一下不清零；只在任务离开 owned-active 集时 GC（否则偶尔动一下就永远升级不了 leader）。`_escalated_claims` 保证同一停滞只上报一次。
- GC：`_check_stale_claimed_tasks` 每次 sweep 基于"当前本成员 relevant active task 集"对四个字典/集合做差集清理，避免无限增长。

`_last_pending_nudge` 是 leader 自用，因为 stale-pending 仅 leader 观察。

### `EVENT_METHOD_MAP`（按 handler 归类）

```python
AgentLifecycleHandler.EVENT_METHOD_MAP = {
    InnerEventType.USER_INPUT.value:    "on_user_input",
    TeamEvent.STANDBY:                  "on_standby",
    TeamEvent.CLEANED:                  "on_cleaned",
    TeamEvent.TOOL_APPROVAL_RESULT:     "on_tool_approval_result",
}

MemberHandler.EVENT_METHOD_MAP = {
    TeamEvent.MEMBER_SPAWNED:           "on_member_event",
    TeamEvent.MEMBER_RESTARTED:         "on_member_event",
    TeamEvent.MEMBER_STATUS_CHANGED:    "on_member_event",
    TeamEvent.MEMBER_EXECUTION_CHANGED: "on_member_event",
    TeamEvent.MEMBER_SHUTDOWN:          "on_member_event",
    TeamEvent.MEMBER_CANCELED:          "on_member_event",
}

MessageHandler.EVENT_METHOD_MAP = {
    TeamEvent.MESSAGE:                  "on_message_or_broadcast",
    TeamEvent.BROADCAST:                "on_message_or_broadcast",
    InnerEventType.POLL_MAILBOX.value:  "on_poll_mailbox",
    TeamEvent.MEMBER_SHUTDOWN:          "on_member_shutdown_drain",   # fan-out
}

TaskBoardHandler.EVENT_METHOD_MAP = {
    TeamEvent.TASK_CLAIMED:    "on_task_claimed",
    TeamEvent.TASK_REVOKED:    "on_task_revoked",
    TeamEvent.TASK_CANCELLED:  "on_task_cancelled",
    TeamEvent.TASK_UPDATED:    "on_task_updated",
    TeamEvent.TASK_CREATED:    "on_task_board_event",
    TeamEvent.TASK_COMPLETED:  "on_task_board_event",
    TeamEvent.TASK_UNBLOCKED:  "on_task_board_event",
    TeamEvent.TASK_RELEASED:   "on_task_board_event",
}

StaleTaskHandler.EVENT_METHOD_MAP = {
    InnerEventType.POLL_TASK.value: "on_poll_task",
}

TeamCompletionHandler.EVENT_METHOD_MAP = {
    InnerEventType.POLL_TASK.value: "on_poll_task",
    TeamEvent.TASK_LIST_DRAINED:    "on_task_list_drained",
    TeamEvent.TEAM_COMPLETED:       "on_team_completed",
}
```

`MEMBER_SHUTDOWN` 同时被 `MemberHandler` 和 `MessageHandler` 注册、`POLL_TASK` 同时被 stale-task handler 和 `TeamCompletionHandler` 注册——这是合规的 fan-out 用法，不是冲突。注册顺序 `(lifecycle, member, message, task_board, stale_task, team_completion, workflow)` 决定了 `member.on_member_event` 先跑、`message.on_member_shutdown_drain` 后跑，以及 `stale_task.on_poll_task` 先跑、`team_completion.on_poll_task` 后跑。

### handler 实例字段

| 字段 | 类型 | 持有者 | 生命周期 |
|---|---|---|---|
| `_round` | `AgentRoundController` | 所有 handler、dispatcher | dispatcher 生命周期内不可重绑 |
| `_lifecycle` | `TeamLifecycleController` | 所有 handler | 同上 |
| `_poll` | `PollController` | 所有 handler、dispatcher 经 `poll_ctrl` 注入 | 同上 |
| `_blueprint` | `TeamAgentBlueprint` | 所有 handler、dispatcher | 同上（`TeamAgentBlueprint` 自身 frozen） |
| `_infra` | `TeamInfra` | 所有 handler、dispatcher | 同上（per-process 容器） |
| `_last_stale_nudge` | `dict[str, float]` | `MemberHandler` + `StaleTaskHandler` 共享同一引用 | dispatcher 生命周期内 |
| `_last_pending_nudge` | `dict[str, float]` | `StaleTaskHandler` 私有 | 同上 |
| `_team_completed_emitted` | `bool` | `TeamCompletionHandler` 私有 | 同上（上升沿发射 / 下降沿重新武装的进程内幂等标志）|
| `_completion_callbacks` | `list[Callable[[], Awaitable[None]]]` | `TeamCompletionHandler` 私有 | 同上（`register_completion_callback` 在 deepagent 建好后一次性注册，`on_task_list_drained` fire）|

CoordinationKernel 自身的 lifecycle state：

| 字段 | 类型 | 含义 |
|---|---|---|
| `_lifecycle_state` | `Literal["idle", "running", "paused", "stopped"]` | kernel 内部状态机；`setup` 后 `idle`，`start` 推到 `running`，`pause` 把 `running → paused`，`stop` 把 `running \| paused → stopped`。`stopped` / `idle` 上的 `pause` / `stop` 是 no-op。Runner finally 重复调用安全。 |

合法转移：

```
idle    --start()--> running
running --pause()--> paused
running --stop()--> stopped
paused  --stop()--> stopped     (清剩下的资源)
paused  --pause()--> paused     (no-op)
stopped --pause()--> stopped    (no-op)
stopped --stop()--> stopped     (no-op)
idle    --pause()--> idle       (no-op)
idle    --stop()--> idle        (no-op)
```

`stopped` 是该 kernel 实例的终态。重新启动需要重建 kernel（`setup` 之后从 `idle` 起步）。

EventBus 自身的运行态：

| 字段 | 类型 | 含义 |
|---|---|---|
| `_role` | `TeamRole` | 创建时确定，`role` 属性只读 |
| `_wake_callback` | `Optional[WakeCallback]` | `start(wake_callback=...)` 绑定，`__init__` 阶段为 `None` |
| `_running` | `bool` | 主 loop 是否激活 |
| `_polls_paused` | `bool` | poll task 是否暂停；`stop()` 进入时复位 |
| `_event_queue` | `asyncio.Queue[CoordinationEvent]` | 单生产/单消费的事件队列 |
| `_loop_task / _mailbox_poll_task / _task_poll_task` | `asyncio.Task | None` | 三条后台 task；`stop()` 与 `pause_polls()` 各自负责回收 |

## 与其它 spec 的关系

- **`S_04_session-and-recovery`**：本文规约 `kernel.start / pause / stop` 调 `SessionManager.bind_session / release_session / unbind_session` 的语义与时机；session checkpoint 的字段布局、namespace 分桶（`teams` 子命名空间）、`metadata.read_team_namespace / merge_team_namespace` 的契约归 S_04。本文只断言"kernel 不在调用面手写 session_id / contextvar / DB 表初始化 / persist_leader_config"。
- **`S_07_interaction-views-and-hitt`**：三视角入口（GodView / Operator / HumanAgent）解析为 `EventMessage` 的过程归 `interaction/`；`@<member>` 路由用 `interaction/router.py` 的 `parse_mention`。本文只断言"事件到达 EventBus 时已经是结构化对象，dispatcher / handler 不解析字符串"。
- **`S_06_runtime-pool-dispatch`**：跨 team 的对象池、7 路 dispatch truth table、`InteractGate` 等并发门禁归 `runtime/`。本文管单个 TeamAgent 内部的事件循环，不感知 pool。**pause vs stop 决策**也在该 spec：`manager.finalize` / `finalize_member` 在 Runner finally 路径决定是否调本文规约的 `pause()` / `stop()`，并写入 leader pool entry 状态与 teammate `team_member` 持久状态。
- **harness（已存在 docs）**：`AgentRoundController` 的 `deliver_input / cancel_agent / resume_interrupt` 最终落到 `TeamHarness` → `DeepAgent`；具体 round 状态机（pre-stream / streaming / finalize）与 HITL interrupt 语义归 harness。本文只规约"handler 通过窄 protocol 调进去，host 自己分流到 start/follow_up/steer"。
- **`S_02_team-agent-architecture`**：TeamAgent 四象限分解（blueprint / state / resources / infra）、`SpawnManager` / `RecoveryManager` / `StreamController` 的职责拆分归该 spec。本文复用 `TeamAgentBlueprint` 与 `TeamInfra` 作为 handler 的注入面，但不规约其内部字段。
- **`S_22_scheduling-runtime`**（F_62）：leader 侧调度决策引擎。kernel 只做四件事——setup 构造（仅 `spec.dispatch_mode == "scheduled"` 的 leader）、start 组合 wake + 团队已存在时激活、`notify_team_built` 激活、pause/stop 失活；决策语义（开工/验票/升级）与 `SCHEDULER_SCAN` 的消费全部归该 spec。task_board / stale_task 的调度模式类（`ScheduledTaskBoardHandler` / `ScheduledStaleTaskHandler`）在构造期按 spec 模式装配（不变量 6）；handler 方法内不出现 dispatch_mode 分支，调度类的行为差异见 S_22 不变量 14。
