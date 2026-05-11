# Member Spawn 与 Stream 控制

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/spawn/`、`openjiuwen/agent_teams/agent/spawn_manager.py`、`openjiuwen/agent_teams/agent/stream_controller.py`、`openjiuwen/agent_teams/agent/payload.py`、`openjiuwen/agent_teams/agent/agent_configurator.py`、`openjiuwen/agent_teams/context.py` |
| 最近一次修订 commit | 18823271 |
| 关联 feature | — |

## 范围 / 边界

本 spec 描述 leader 把 teammate / human-agent 拉起为一个独立运行体的所有路径——
进程派生、协程派生、payload wire 格式、session_id 跨边界传播——以及该运行体
DeepAgent round 的 stream 队列、cooperative cancel、observer fan-out 行为。

**管：**

- `TeamAgent.spawn_member` 这个对外统一入口下，两种 spawn 模式（`process` /
  `inprocess`）的入口、handle 形态、清理顺序。
- 子进程 spawn 的 wire 契约（`SpawnPayloadBuilder` 输出 ↔
  `TeamAgent.from_spawn_payload` 输入）。
- `session_id` contextvar 在两条路径上的传播规则。
- `StreamController` 的 round 生命周期、stream queue 写入、chunk tagging、
  cooperative cancel 两阶段流程、observer fan-out 与失败自分离。
- `SpawnManager` 与 `StreamController` 之间的 chunk forward 钩子（仅 inprocess
  适用）。
- worktree 注入时机：仅非 LEADER 角色才构造 `WorktreeManager`。

**不管：**

- DeepAgent / TeamHarness 内部的 task loop、interrupt 机制——属于 `harness/`，
  本 spec 只到 `harness.run_streaming` / `harness.abort` 的调用契约。
- pool / dispatch / activate 决策——见 `S_06_runtime-pool-dispatch`。
- 消息总线本身的实现——见 `messager/`。
- worktree 后端 / 跨机器策略——见 `worktree_remote.py` 与
  `harness/tools/worktree`。

## 不变量

1. **Spawn 入口唯一性**：对外只有 `TeamAgent.spawn_member`（工具层）→
   `SpawnManager.spawn_teammate` 这一条路径，模式由
   `TeamAgentSpec.spawn_mode` 决定。任何"自己 import 一下 process_manager
   或 inprocess_spawn"的旁路调用都是错的。
2. **Spawned 实例从不进 leader pool**：两种模式的 spawned member 都通过
   `Runner.run_agent_team(..., member=True)` 跳过 activate / dispatch。
   只有 spec 路径上的 leader 才进 pool。
3. **Wire 契约对称**：`SpawnPayloadBuilder.build_spawn_payload` 输出的
   每个键都是 `TeamAgent.from_spawn_payload` 的读取键。改一边必须同改另一边，
   否则跨进程拉起立即崩。当前键集：
   `coordination.{team_name, display_name, leader_member_name, member_name, role, persona, transport}`、`query`。
4. **`spawn_mode='inprocess'` 隐含 in-process transport**：`TeamAgentSpec`
   的 `_default_transport_for_spawn_mode` validator 在 `transport=None` 时
   自动注入 `TransportSpec(type='inprocess')`。`spawn_mode='process'`
   则刻意保留 `None`，强迫调用方显式配 pyzmq。
5. **`session_id` contextvar 是两种模式共享的传播载体**：
   - inprocess：先 `contextvars.copy_context()` 抓快照，再在新 task 内
     `set_session_id(session_id)` 二次确认；
   - process：spawn payload 的 `SpawnAgentConfig.session_id` 由 child
     bootstrap 读取，并通过 `Runner.run_agent_team(..., session=...)`
     最终走到 `SessionManager.bind_session` 里 `set_session_id`。
   两条路径的目的都是让 `get_session_id()` 在子运行体内返回同一个值。
6. **LEADER 不开 worktree**：`AgentConfigurator` 仅在 `ctx.role !=
   LEADER` 且 `spec.worktree.enabled` 时才构造 `WorktreeManager`。LEADER
   角色拿到的 `worktree_manager` 永远是 `None`。这与团队工具 rail 的
   `qualify_ids=spec.spawn_mode == "inprocess"` 是两个独立维度，不要混。
7. **Inprocess chunk forwarder 与 handle 同生命周期**：`SpawnManager` 在
   inprocess 路径上立即 `_wire_inprocess_chunk_forward` 把 teammate
   `StreamController` 的 chunk 转发到 leader `stream_queue`；
   `cleanup_teammate` 必须先 `remove_chunk_observer` 再 `force_kill`，
   否则会有迟到 chunk 落进已废弃的 leader 队列。子进程模式没有这个钩子，
   teammate chunk 只能走 messager（当前 unimplemented，列为 future work）。
8. **Cooperative cancel 两阶段强制顺序**：`StreamController.cooperative_cancel`
   先置 `_cancel_requested=True` + 调 `harness.abort()` 让 task loop 在下一个
   安全检查点退出，再 `wait_for(timeout=2.0)`；超时才 fallback 到
   `task.cancel()`。两阶段不可交换、不可省。
9. **Drain 路径必须吞 `CancelledError + Exception`**：
   `cooperative_cancel`、`cancel_recovery_tasks`、`shutdown_all_handles`
   清理路径里的 `contextlib.suppress(asyncio.CancelledError, Exception)`
   是 invariant——caller 已经表态要拆掉 round，再把异常往外抛会污染上层
   shutdown。重构清理路径时必须保留这个 suppress。
10. **`_cancel_requested` 是当前 round 私有标志**：`_run_one_round` 入口
    处必须重置为 `False`，否则一次 cooperative abort 之后下一个 round
    会被错误地当成"已取消"，restart 路径被吞掉。
11. **Observer 例外自隔离**：`_stream_one_round` fan-out 时单个 observer 抛
    异常**只**会被自动 `remove_chunk_observer`，绝不能让本地 stream 被
    consumer 阻塞或拖垮。新增 observer 实现要做到 idempotent detach。
12. **Round 完成路径的状态机闭合**：cooperative abort 不抛 `CancelledError`，
    所以 `_execute_round` 在 `_cancel_requested=True` 时显式发布
    `ExecutionStatus.CANCELLED`，与 hard-cancel 抛 `CancelledError` 走的
    `except` 分支殊途同归。状态机不允许"流跑完了但既不是 COMPLETED 也不是
    CANCELLED"的中间态。
13. **Spawn 子进程命令固定**：`spawn_process` 用 `sys.executable -m
    openjiuwen.core.runner.spawn.child_process` 启动，不是 `os.fork`。
    Windows / macOS / Linux 全平台使用相同的 `asyncio.create_subprocess_exec`
    路径，依赖 stdin/stdout pipe 通信。**禁止**为了"在 Linux 上更快"切到
    `fork` 启动方式——会破坏跨平台一致性，并且与 macOS 的 spawn 默认冲突。

## 接口契约

### TeamAgent / SpawnManager 顶层

```python
# TeamAgent —— 工具层 spawn_member 的最终落点
async def spawn_teammate(
    self,
    ctx: TeamRuntimeContext,
    *,
    initial_message: Optional[str] = None,
    session: Optional[Any] = None,
    spawn_config: Optional[SpawnConfig] = None,
) -> SpawnedProcessHandle | InProcessSpawnHandle: ...

# 关键内部跳板，spawn_mode 在这里分流
class SpawnManager:
    async def spawn_teammate(
        self,
        ctx: TeamRuntimeContext,
        *,
        initial_message: Optional[str] = None,
        session: Optional[Any] = None,
        spawn_config: Optional[SpawnConfig] = None,
    ) -> SpawnedProcessHandle | InProcessSpawnHandle: ...

    def lookup_inprocess_agent(self, member_name: str) -> Optional[TeamAgent]: ...
    async def cleanup_teammate(self, member_name: str) -> None: ...
    async def restart_teammate(self, member_name: str, max_retries: int = 3) -> bool: ...
    async def on_teammate_unhealthy(self, member_name: str) -> None: ...
    async def shutdown_all_handles(self) -> None: ...
    async def cancel_recovery_tasks(self) -> None: ...
    async def build_context_from_db(self, member_name: str) -> Optional[TeamRuntimeContext]: ...
    async def publish_restart_event(self, member_name: str, restart_count: int) -> None: ...
```

错误语义：

- `cleanup_teammate` 吞所有清理异常并继续走 `force_kill` —— 关停路径不允许抛。
- `restart_teammate` 失败 `max_retries` 次后把成员 DB 状态置 `MemberStatus.ERROR`
  并返回 `False`，不抛。
- `on_teammate_unhealthy` 是 `SpawnedProcessHandle.on_unhealthy` 的固定回调，
  自动登记到 `recovery_tasks` 集合，由 `cancel_recovery_tasks` 在 shutdown 时
  统一 cancel。

### Spawn 模式分流

```python
# inprocess —— 同 event loop 协程
async def inprocess_spawn(
    team_agent: TeamAgent,
    ctx: TeamRuntimeContext,
    *,
    initial_message: Optional[str] = None,
    session_id: Optional[str] = None,
) -> InProcessSpawnHandle: ...

# process —— 子进程
await Runner.spawn_agent(
    spawn_config: SpawnAgentConfig,   # 由 SpawnPayloadBuilder.build_spawn_config 产出
    payload: dict[str, Any],          # 由 SpawnPayloadBuilder.build_spawn_payload 产出
    *,
    session: Optional[Any] = None,
    spawn_config: Optional[SpawnConfig] = None,
) -> SpawnedProcessHandle
```

`SpawnedProcessHandle` 与 `InProcessSpawnHandle` 提供相同的鸭子接口
（`is_alive` / `is_healthy` / `start_health_check` / `stop_health_check` /
`shutdown` / `force_kill` / `wait_for_completion` / `on_unhealthy` 回调），
所以 `SpawnManager.spawned_handles` 字典里两类 handle 可以混合存放。
**新增 spawn 模式必须实现这套接口**，不要在 SpawnManager 里加 `isinstance`
分支。

### SpawnPayloadBuilder

```python
class SpawnPayloadBuilder:
    def __init__(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> None: ...

    def build_spawn_payload(
        self,
        ctx: TeamRuntimeContext,
        *,
        initial_message: Optional[str] = None,
    ) -> dict[str, Any]: ...

    def build_member_context(self, member_spec: TeamMemberSpec) -> TeamRuntimeContext: ...
    def build_member_messager_config(self, member_name: str) -> Optional[MessagerTransportConfig]: ...
    def build_spawn_config(self, ctx: TeamRuntimeContext) -> SpawnAgentConfig: ...
```

- `build_member_messager_config` 内有按 `member_name` 稳定分配的端口表
  （`teammate_base_port` / `teammate_port_offset` 在 `spec.metadata` 上覆盖）。
  同一个 builder 实例对同一个 `member_name` 多次调用必须返回同一个
  `direct_addr`，否则 restart 时端口漂移、订阅会断。
- `build_spawn_config` 把 `Runner.get_config()` 序列化进 payload，子进程
  bootstrap 时由 `child_process.py` 还原；同时改写 file-based log sink target，
  把 teammate 日志落到 `<dir>/teammates/<member_tag>/...`。

### StreamController

```python
class StreamController:
    def __init__(
        self,
        *,
        blueprint_getter: Callable[[], Optional[TeamAgentBlueprint]],
        state: TeamAgentState,
        resources: PrivateAgentResources,
        status_updater: Callable[[MemberStatus], Any],
        execution_updater: Callable[[ExecutionStatus], Any],
        wake_mailbox_callback: Optional[Callable[[], Any]] = None,
    ): ...

    # round 生命周期
    async def start_round(self, content: Any) -> None: ...
    async def steer(self, content: str) -> None: ...
    async def follow_up(self, content: str) -> None: ...
    async def cancel_agent(self) -> None: ...
    async def drain_agent_task(self) -> None: ...
    async def cooperative_cancel(self) -> None: ...
    def close_stream(self) -> None: ...

    # 状态查询
    def is_agent_running(self) -> bool: ...
    def has_in_flight_round(self) -> bool: ...
    def has_pending_interrupt(self) -> bool: ...
    def is_valid_interrupt_resume(self, user_input: Any) -> bool: ...

    # observer fan-out
    def add_chunk_observer(self, cb: ChunkObserver) -> None: ...
    def remove_chunk_observer(self, cb: ChunkObserver) -> None: ...
```

错误语义：

- `cancel_agent` 顺序更新执行状态：`CANCEL_REQUESTED` →（如果有 in-flight）
  `CANCELLING` → `cooperative_cancel`。状态机的可观测序列必须严格按这个先后。
- `cooperative_cancel` 软超时常量 `_COOPERATIVE_ABORT_TIMEOUT_SECONDS = 2.0`。
  调短会切掉模型 flush、状态持久化的窗口；调长会让一个卡死的 task loop 阻塞
  上层 shutdown。改这个值之前先量过两边。
- `drain_agent_task` 在 cooperative cancel 之前清空 `pending_inputs` /
  `pending_interrupt_resumes`，意图明确："我现在就要它停，不要在 finally 里
  起新 round"。
- `_run_one_round` 的 finally 分支只在 `not cancelled and not _cancel_requested`
  时才走 restart 路径（pending interrupt resume / pending inputs / wake
  mailbox）。这是 cooperative abort 不抛 `CancelledError` 还能正确退出的关键。
- `_run_retrying_stream` 仅对 `_RETRYABLE_ERROR_CODES`（当前是 `{181001}`）
  做最多 `_MAX_RETRY_ATTEMPTS=10` 次重试，重试 query 固定为 `_RETRY_QUERY`
  常量。其它错误码 → 立刻 `build_error(StatusCode.AGENT_TEAM_EXECUTION_ERROR)`。

### session_id contextvar 契约

```python
# openjiuwen.agent_teams.context
def set_session_id(session_id: str) -> Token[str]: ...
def get_session_id() -> Optional[str]: ...   # 默认值 ""，不是 None
def reset_session_id(token: Token[str]) -> None: ...
```

- **inprocess 传播**：`inprocess_spawn` 抓 `contextvars.copy_context()`、
  在新 task 入口再次 `set_session_id(session_id)`。两层是必须的——前者保证
  其它 contextvar（log trace_id 等）一并继承，后者保证就算 caller 没设
  contextvar 也能强制注入。
- **process 传播**：`SpawnAgentConfig.session_id` 走 wire；child bootstrap
  里 `Runner.run_agent_team(..., session=...)` 触发
  `SessionManager.bind_session` 调 `set_session_id`。
- **不要绕过 contextvar 直接读 `state.session_id`**：跨成员的事件发布
  （`SpawnManager.publish_restart_event`、`TeamMonitor` 等）默认从
  `get_session_id()` 取值。绕过会让事件 topic 计算错位。

## 数据结构

### `TeamAgentSpec.spawn_mode: Literal["process", "inprocess"]`

| 值 | 行为 | 默认 transport |
|---|---|---|
| `"process"`（默认） | `Runner.spawn_agent` → `asyncio.create_subprocess_exec` 拉起 `python -m openjiuwen.core.runner.spawn.child_process` | `None`（必须显式配 `pyzmq` 等跨进程后端） |
| `"inprocess"` | `inprocess_spawn` → `loop.create_task` 在当前 event loop 跑 | 自动注入 `TransportSpec(type="inprocess")` |

跨平台：spawn 模式两条路径在 Windows / macOS / Linux 表现一致。子进程
路径用 `asyncio.create_subprocess_exec` + stdin/stdout 管道，不依赖
`fork`，所以 macOS 默认 spawn-only 不会出问题；inprocess 路径不涉及任何
进程操作，平台无关。

### `SpawnedProcessHandle` / `InProcessSpawnHandle`

两者公开接口对齐（见上文契约），区别：

- `SpawnedProcessHandle` 持有真实子进程 pid + 消息管道，`is_healthy` 由
  心跳决定；
- `InProcessSpawnHandle` 持有 `_task: asyncio.Task` 与 `agent_ref:
  TeamAgent`（可被 `lookup_inprocess_agent` 拿到，用于 HumanAgent inbox
  直送），`start_health_check` / `stop_health_check` 是 no-op，
  `is_healthy = is_alive and not _shutdown_requested`。
- `_chunk_forward` 字段仅 inprocess 使用，由 `SpawnManager._wire_inprocess_chunk_forward`
  写入、`cleanup_teammate` 读出并 `remove_chunk_observer`。

### `StreamController` 状态字段

| 字段 | 类型 | 生命周期 |
|---|---|---|
| `stream_queue` | `Optional[asyncio.Queue]` | 由 owner（`TeamAgent`）在 round 启动前赋值；`close_stream` 通过 `put_nowait(None)` 标志结束 |
| `agent_task` | `Optional[asyncio.Task]` | `start_round` 创建，`_run_one_round.finally` 重置为 `None`；外部用 `has_in_flight_round` 查 |
| `streaming_active` | `bool` | `_stream_one_round` 进入时置 `True`，退出时置 `False`；`is_agent_running` 读这个值 |
| `pending_interrupt_resumes` | `list[InteractiveInput]` | round 中遇到 interrupt resume 候选；`_dequeue_valid_interrupt_resume` 出栈时校验 `harness.is_pending_interrupt_resume_valid` |
| `pending_inputs` | `list[Any]` | round 进行中收到的额外输入；round 结束后批量合并成下一个 round 的 query（多条用 `\n\n---\n\n` 分隔） |
| `_chunk_observers` | `list[ChunkObserver]` | inprocess teammate fan-out 注入；observer 抛异常自动 `remove_chunk_observer` |
| `_cancel_requested` | `bool` | cooperative cancel 标志；`_run_one_round` 入口必须重置 |

### Round 状态机

```
 [READY]--start_round--> [BUSY]
   |                         |
   |                  _execute_round
   |                  STARTING -> RUNNING -> COMPLETING -> COMPLETED -> IDLE
   |                                 |
   |                       cooperative cancel 路径
   |                                 v
   |                            (cancel_requested=True)
   |                            CANCELLED -> IDLE
   |                                 |
   |                       hard cancel / CancelledError
   |                                 v
   |                            CANCELLED -> IDLE
   |                                 |
   |                       Exception
   |                                 v
   |                            FAILED -> IDLE
   v
 [READY] (除非 SHUTDOWN_REQUESTED 则关 stream)
```

`MemberStatus.READY → BUSY → READY/ERROR` 是成员维度的快照，
`ExecutionStatus.STARTING → RUNNING → ... → IDLE` 是 round 维度的快照，
两个状态机各自维护、各自发布事件；不要在新代码里只更新一个。

## 与其它 spec 的关系

- **runtime / pool / dispatch**：spawn 出来的 teammate / human-agent
  绕开 pool（`member=True`），所以 leader-only 的 pool 不变量是本 spec
  的前提。
- **coordination**：`StreamController.cancel_agent` 的状态发布会被
  `coordination/` 的 `MemberEventHandler` 看到，触发对应的 lifecycle 反应；
  本 spec 只承诺状态发布次序，反应策略落在 coordination spec。
- **interaction (HITT)**：`SpawnManager.lookup_inprocess_agent` 为
  `HumanAgentInbox` 提供旁路通道——把人类用户输入直接喂给 human-agent 的
  DeepAgent，不绕消息总线。这条路只在 inprocess 模式下成立；subprocess
  human-agent 必须走 messager。
- **messager / transport**：spawn 模式与 transport 默认值绑定（不变量 4）；
  subprocess 模式下消息总线是 teammate 唯一对外通道（chunk fan-out 当前
  不可用，是已知 follow-up）。
- **worktree**：本 spec 的 LEADER 不开 worktree 不变量（不变量 6）是
  worktree spec 的输入条件之一；若上层把 worktree manager 接到 leader
  上，会绕过本 spec 的角色检查，破坏隔离。
