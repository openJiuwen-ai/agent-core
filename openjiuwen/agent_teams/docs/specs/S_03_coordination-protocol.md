# Coordination Protocol

`agent_teams.agent.coordination` 子系统的设计规约：事件总线 + 分发器 + 5 个场景 handler + kernel 装配。本文描述"系统当前是什么样"，对应 commit `18823271`。

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/agent/coordination/` |
| 最近一次修订 commit | `18823271` |
| 关联 feature | `F_01_coordination-protocol-cleanup.md` |

## 范围 / 边界

**这个规约管：**

- `EventBus` 的事件入队、唤醒回调绑定时机、生命周期与 poll 暂停/恢复语义。
- `EventDispatcher` 的粗筛触发规则（`agent_ready` / inner-vs-transport / 角色级 whitelist）以及 5 个 handler 的注册顺序与 fan-out 顺序。
- `BaseCoordinationHandler` 的注入面（5 类窄依赖）、`EVENT_METHOD_MAP` 协议与 `get_callbacks()` 语义。
- `AgentLifecycleHandler` / `MemberHandler` / `MessageHandler` / `TaskBoardHandler` / `StaleTaskHandler` 各自监听的 event_key、共享状态、关键方法。
- `CoordinationKernel` 的构造装配序、`start` / `pause` / `stop` 路径与 `SessionManager` 的三方法状态机协作。
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
6. **fan-out 顺序由 `EventDispatcher.__init__` 元组顺序决定**：`(lifecycle, member, message, task_board, stale_task)` 即 framework 注册顺序；同 priority（默认 0）下 Python `list.sort` 稳定，因此 `MEMBER_SHUTDOWN` 上 `MemberHandler.on_member_event` 永远先于 `MessageHandler.on_member_shutdown_drain`。
7. **handler 之间不直接互调**。跨域响应必须通过让两个 handler 各自注册同一个 `event_key`、走 framework fan-out 实现。
8. **`AsyncCallbackFramework.trigger` 的异常语义是吞 + 续跑**：普通 `Exception` 被框架 log + continue，仅 `AbortError` 上抛。handler 必须自己用 `team_logger.error("...", exc_info=True)` 记录关键失败，**禁止依赖 framework swallow 当作隐式错误处理**。
9. **`MemberHandler` 与 `StaleTaskHandler` 共享同一个 `dict[str, float]` 引用作为 stale-claim throttle**（在 `EventDispatcher.__init__` 中创建一次、传两个 handler）。同一 task 在一个 stale 窗口内不会被两条路径重复 nudge。
10. **`HUMAN_AGENT` 角色禁止自主轮询与自主响应**：`InnerEventType.POLL_TASK` / `InnerEventType.POLL_MAILBOX` 在 dispatch 入口直接短路；transport 事件仅放行 `TeamEvent.CLEANED` / `TeamEvent.MEMBER_CANCELED` / `TeamEvent.STANDBY` 三类。
11. **dispatch 前置检查 `is_agent_ready()`**：未就绪时直接 return，handler 不会被触发。
12. **`SHUTDOWN` inner 事件是 EventBus 自身的停机信号**，仅由 `EventBus.stop()` 写入；handler 不监听该事件。
13. **session 绑定状态机是三态**：未绑（id=None, ts=None）/ 完整（id=X, ts=session）/ 半绑（id=X, ts=None）。`bind_session(session)` 进完整态、`release_session()` 完整→半绑、`unbind_session()` 任意态→未绑。`kernel.start` 在调用面显式分流：session 非 None 走 bind，否则走 unbind；不依赖被调方分支。

## 接口契约

### 三类窄 Protocol

```python
@runtime_checkable
class AgentRoundController(Protocol):
    """Round-level control surface against the owning agent."""

    def is_agent_ready(self) -> bool: ...
    def is_agent_running(self) -> bool: ...
    def has_in_flight_round(self) -> bool: ...
    def has_pending_interrupt(self) -> bool: ...

    async def cancel_agent(self) -> None: ...
    async def deliver_input(self, content: Any, *, use_steer: bool = True) -> None: ...
    async def resume_interrupt(self, user_input: Any) -> None: ...


@runtime_checkable
class TeamLifecycleController(Protocol):
    """TeamAgent-level lifecycle effects that span multiple managers."""

    async def shutdown_self(self) -> None: ...


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
    ) -> None: ...

    # Public attributes — access in tests / debugging
    lifecycle: AgentLifecycleHandler
    member:    MemberHandler
    message:   MessageHandler
    task_board: TaskBoardHandler
    stale_task: StaleTaskHandler

    async def dispatch(self, event: CoordinationEvent) -> None: ...
```

`dispatch()` 流程：

1. `is_agent_ready()` 否 → 直接 return。
2. `InnerEventMessage` 分支：
   - `HUMAN_AGENT` 角色 + `POLL_TASK / POLL_MAILBOX` → return（防御性短路，机制 1）。
   - 否则 `await framework.trigger(event_type.value, event)`。
3. transport `EventMessage` 分支：
   - `blueprint.member_name` 缺失 → return。
   - `HUMAN_AGENT` 角色 + 不在 `{CLEANED, MEMBER_CANCELED, STANDBY}` 白名单 → return（机制 2）。
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

### 5 个场景 Handler

下表列出每个 handler 的事件订阅、共享状态与关键方法。所有 handler 的 `__init__` 至少接收 `(host, blueprint, infra, poll_ctrl)`；`MemberHandler` 与 `StaleTaskHandler` 额外接收同一个 `stale_claim_throttle: dict[str, float]`。

| handler | 监听 event_key | 共享 / 私有状态 | 关键方法 |
|---|---|---|---|
| `AgentLifecycleHandler` | `InnerEventType.USER_INPUT` / `TeamEvent.STANDBY` / `TeamEvent.CLEANED` / `TeamEvent.TOOL_APPROVAL_RESULT` | 无 | `on_user_input` → `_round.deliver_input`；`on_standby` → `_poll.pause_polls`；`on_cleaned` → `_lifecycle.shutdown_self`（leader 跳过）；`on_tool_approval_result` → 构造 `InteractiveInput` → `_round.resume_interrupt` |
| `MemberHandler` | 6 个 `MEMBER_*`：`SPAWNED` / `RESTARTED` / `STATUS_CHANGED` / `EXECUTION_CHANGED` / `SHUTDOWN` / `CANCELED` | `_last_stale_nudge: dict[str, float]`（与 `StaleTaskHandler` 同一引用） | `on_member_event` 按 role 分流到 `_handle_leader_member_event` / `_handle_teammate_member_event`；leader 在 `MEMBER_STATUS_CHANGED` 且 `new_status ∈ {READY, ERROR}` 时 `_nudge_idle_member_with_stale_claims`；teammate 仅对自身 `MEMBER_CANCELED` 触发 `_round.cancel_agent` |
| `MessageHandler` | `TeamEvent.MESSAGE` / `TeamEvent.BROADCAST` / `InnerEventType.POLL_MAILBOX` / `TeamEvent.MEMBER_SHUTDOWN`（fan-out） | 无 | `on_message_or_broadcast`：leader 在 MESSAGE 上 `_ack_user_bound_message` + `_notify_human_agent_inbound`，所有 role 接着 `_poll.resume_polls` + `_process_unread_messages`；`on_poll_mailbox` 周期 sweep；`on_member_shutdown_drain` 仅 teammate 给自己 drain（`use_steer=True`） |
| `TaskBoardHandler` | `TeamEvent.TASK_CLAIMED` + 5 个 `TASK_*`：`CREATED` / `UPDATED` / `COMPLETED` / `CANCELLED` / `UNBLOCKED` | 无 | `on_task_claimed`：targeted 自身 → `deliver_input`；非自身 → 走 `on_task_board_event` 兜底（防 idle leader 错过 board 变更）。`on_task_board_event` 在 `has_in_flight_round()` 否时 `_nudge_idle_agent`，向 idle agent 喂任务清单；leader 全部任务终结时按 lifecycle 喂 all-done prompt |
| `StaleTaskHandler` | `InnerEventType.POLL_TASK` | `_last_stale_nudge`（共享）+ `_last_pending_nudge: dict[str, float]`（私有，仅 leader 用） | `on_poll_task` → `_check_stale_claimed_tasks` + `_check_stale_pending_tasks`。stale claim：`task.updated_at` 超过 `_STALE_CLAIM_SECONDS` (10min) 触发；自己持有 → `deliver_input`，leader 观察他人 → `message_manager.send_message`。stale pending（仅 leader）：超过 `_STALE_PENDING_SECONDS` (10min) 自喂 prompt、由 LLM 决定如何分派 |

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
    async def enqueue_mailbox_after_first_iteration(self) -> None: ...
    async def wake_mailbox_if_interrupt_cleared(self) -> None: ...

    async def subscribe_transport(self, team_name: str) -> None: ...
    async def unsubscribe_transport(self) -> None: ...

    async def drain_agent_task(self) -> None: ...
    def close_stream(self) -> None: ...
    async def finalize_round(self) -> None: ...
```

构造与启动序约束：

1. `setup(role=...)`：先建 `EventBus(role=role)`，再建 `EventDispatcher(host, blueprint, infra, poll_ctrl=event_bus)`。**此时不绑 wake_callback**——避免引入"构造完但 callback 缺失"的中间态。
2. `start(session=...)`：在子系统初始化（DB、recover、workspace、memory toolkit）之后调用 `event_bus.start(wake_callback=dispatcher.dispatch)`，闭合 EventBus ↔ EventDispatcher 的循环依赖。这是 wake_callback 唯一允许出现的绑定时机。
3. session 绑定状态转换走 `SessionManager` 的三方法，**不在 kernel 里手工写 session_id / contextvar / DB 表初始化 / persist_leader_config**：

   | 调用位点 | 方法 | 状态转换 |
   |---|---|---|
   | `start(session)` 且 `session is not None` | `await sess_mgr.bind_session(session)` | * → 完整态 |
   | `start(session=None)` | `sess_mgr.unbind_session()` | * → 未绑态 |
   | `pause()` / `stop()` | `sess_mgr.release_session()` | 完整 → 半绑 |

4. `pause()` 路径：`drain_agent_task` → `persist_allocator_state` → leader 额外 `_mark_live_teammates_paused` + `cancel_recovery_tasks` + `shutdown_all_handles` + 持久化 lifecycle="paused" → leader 发布 `TEAM_STANDBY` → `unsubscribe_transport` → `event_bus.stop()` → `close_stream` → `release_session`。
5. `stop()` 路径：`drain_agent_task` → `persist_allocator_state` → `unsubscribe_transport` → `cancel_recovery_tasks` + `shutdown_all_handles` → `memory_manager.close()` → `event_bus.stop()` → `close_stream` → `release_session`。
6. `subscribe_transport`：`messager.register_direct_message_handler(event_bus.enqueue)` + 对每个 `TeamTopic` `subscribe(topic_str, _filter_self)`；`_filter_self` 先派发 listener、再过滤 `sender_id == local_member_name` 的自发事件，最后 `event_bus.enqueue(event)`。

### Inner 事件入队工具

| 方法 | 用途 | 角色约束 |
|---|---|---|
| `enqueue_user_input(inputs)` | 把字符串或 `{"query": ...}` dict 包成 `InnerEventMessage(USER_INPUT)` 入队，`invoke()` / `interact()` 共用 | 无 |
| `enqueue_mailbox_after_first_iteration()` | 等 `first_iter_gate` 后投 `POLL_MAILBOX`，让 teammate 第一次邮箱 sweep 不与启动竞争 | 仅 teammate（leader 直接 return） |
| `wake_mailbox_if_interrupt_cleared()` | 在没有 pending interrupt 时投 `POLL_MAILBOX`，避免 interrupt 解除后邮件留底 | 仅 teammate |

### 三铁律

1. **coordination 不做决策。** EventBus / EventDispatcher / handler 都只做 wake-up 与粗筛触发；业务行为由内部 DeepAgent + team tools 驱动。新功能想塞进 `dispatcher.py` 或 handler 之前先问：**是不是应该实现成一个新工具，让 LLM 自己决定调？**
2. **每个 handler 一个业务域，跨域协作走 framework fan-out。** 新事件类型 = 在对应场景 handler 的 `EVENT_METHOD_MAP` 加一行 + 写方法，不需要改 `dispatch()`。同一 event_key 的多 handler 通过注册顺序串行 fan-out；handler 不互相 import、不互相调用。
3. **异常语义。** `AsyncCallbackFramework.trigger()` 吞普通 `Exception`（log + continue），仅 `AbortError` 上抛。handler 必须自己用 `team_logger.error("...", exc_info=True)` 记录关键失败，不允许把 framework swallow 当作隐式错误处理。

## 数据结构

### 共享 throttle 字典

```python
# Created in EventDispatcher.__init__, passed by reference to two handlers.
stale_claim_throttle: dict[str, float] = {}  # task_id -> last-nudge wall-clock seconds

self.member     = MemberHandler(host, blueprint, infra, poll_ctrl, stale_claim_throttle)
self.stale_task = StaleTaskHandler(host, blueprint, infra, poll_ctrl, stale_claim_throttle)
```

- 同一引用，两条触发路径（status-change 与 poll）共看共写；同一 task 在 `_STALE_CLAIM_SECONDS` (10min) 内不会被双方各 nudge 一次。
- GC：`StaleTaskHandler._check_stale_claimed_tasks` 每次 sweep 时基于"当前 relevant claimed task 集"做差集清理，避免无限增长。

`StaleTaskHandler._last_pending_nudge` 是 leader 自用、不共享，因为 stale-pending 仅 leader 观察。

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
    TeamEvent.TASK_CREATED:    "on_task_board_event",
    TeamEvent.TASK_UPDATED:    "on_task_board_event",
    TeamEvent.TASK_COMPLETED:  "on_task_board_event",
    TeamEvent.TASK_CANCELLED:  "on_task_board_event",
    TeamEvent.TASK_UNBLOCKED:  "on_task_board_event",
}

StaleTaskHandler.EVENT_METHOD_MAP = {
    InnerEventType.POLL_TASK.value: "on_poll_task",
}
```

`MEMBER_SHUTDOWN` 同时被 `MemberHandler` 和 `MessageHandler` 注册——这是合规的 fan-out 用法，不是冲突。注册顺序 `(lifecycle, member, message, task_board, stale_task)` 决定了 `member.on_member_event` 先跑、`message.on_member_shutdown_drain` 后跑。

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
- **`S_06_runtime-pool-dispatch`**：跨 team 的对象池、9 路 dispatch truth table、`InteractGate` 等并发门禁归 `runtime/`。本文管单个 TeamAgent 内部的事件循环，不感知 pool。
- **harness（已存在 docs）**：`AgentRoundController` 的 `deliver_input / cancel_agent / resume_interrupt` 最终落到 `TeamHarness` → `DeepAgent`；具体 round 状态机（pre-stream / streaming / finalize）与 HITL interrupt 语义归 harness。本文只规约"handler 通过窄 protocol 调进去，host 自己分流到 start/follow_up/steer"。
- **`S_02_team-agent-architecture`**：TeamAgent 四象限分解（blueprint / state / resources / infra）、`SpawnManager` / `RecoveryManager` / `StreamController` 的职责拆分归该 spec。本文复用 `TeamAgentBlueprint` 与 `TeamInfra` 作为 handler 的注入面，但不规约其内部字段。
