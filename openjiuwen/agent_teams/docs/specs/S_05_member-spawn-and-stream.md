# Member Spawn 与 Stream 控制

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/spawn/`、`openjiuwen/agent_teams/agent/spawn_manager.py`、`openjiuwen/agent_teams/agent/stream_controller.py`、`openjiuwen/agent_teams/agent/payload.py`、`openjiuwen/agent_teams/agent/agent_configurator.py`、`openjiuwen/agent_teams/worktree/`、`openjiuwen/agent_teams/context.py` |
| 最近一次修订日期 | 2026-07-14 |
| 关联 feature | F_38_team-teammate-worktree-isolation-agenttool.md、F_28_native-harness-team-adoption.md、F_60_native-harness-pause-abort-resume.md |

## 范围 / 边界

本 spec 描述 leader 把 teammate / human-agent 拉起为一个独立运行体的所有路径——
进程派生、协程派生、payload wire 格式、session_id 跨边界传播——以及该运行体的
输出如何经 `StreamController` 转发成 team 的 stream、其 runtime 事件如何映射成
成员状态机。

**管：**

- 成员拉起的**单一漏斗**（`auto_start_member` / `auto_start_all` →
  `startup_member` / `startup` → CAS → `_on_teammate_created` →
  `SpawnManager.spawn_teammate`）与它的三个触发点；两种 spawn 模式
  （`process` / `inprocess`）的入口、handle 形态、清理顺序。
- 子进程 spawn 的 wire 契约（`SpawnPayloadBuilder` 输出 ↔
  `TeamAgent.from_spawn_payload` 输入）。
- `session_id` contextvar 在两条路径上的传播规则。
- `StreamController` 的职责：把 `runtime.outputs()` 的 chunk 打 tag 后写进
  stream queue 并 fan-out 给 observer；把 runtime 的 phase / round 事件映射成
  `MemberStatus` / `ExecutionStatus`；把 cancel / pause / resume 转发给 runtime。
- `SpawnManager` 与 `StreamController` 之间的 chunk forward 钩子（仅 inprocess
  适用）。
- team worktree 隔离：leader 侧创建 owner-scoped worktree、context 透传、
  teammate workspace 覆盖、finalize 时 clean/keep 判定。

**不管：**

- **round 驱动本身**。自 [[F_28]] 起 runtime（NativeHarness supervisor / 外部
  CLI runtime）独占 round 驱动与输入投递：起轮 / steer / follow-up 一律是
  `TeamAgent.deliver_input` → `harness.send(...)`，**`StreamController` 不再驱动
  round、不再排 pending input、不再自重启**。调用契约见
  [[S_18_harness-interaction-contract]]；harness 内部 task loop / interrupt 属
  `harness/`。
- pool / dispatch / activate 决策——见 `S_06_runtime-pool-dispatch`。
- 成员的 graceful 退场判定（`SHUTDOWN_REQUESTED` + 无 in-flight round → 直接
  收摊）——见 `S_03` 机制 18。本 spec 只承诺 `_on_idle_settled` 在读到
  `SHUTDOWN_REQUESTED` 时 `close_stream()`。
- 消息总线本身的实现——见 `messager/`。
- worktree 后端 / 跨机器策略——见 `worktree_remote.py` 与
  `harness/tools/worktree`。

## 不变量

1. **注册与拉起是两件事；拉起只有一个漏斗**。

   **注册**（写 DB 行，`status=UNSTARTED`，**不启动任何东西**）：`spawn_teammate` /
   `spawn_human_agent` / `spawn_bridge_agent` / `spawn_external_cli` 四个工具，以及
   `build_team` 的 predefined 成员，全部落到 `TeamBackend.spawn_member`（及其
   role 变体）。该方法的 docstring 就是契约：*"Only persists the member data —
   does NOT start the member."* **spawn 工具不拉起 agent**——工具描述里"何时被拉起
   取决于团队的调度模式"说的就是这件事。

   **拉起**（真正起进程 / 协程）只有一条链：

   ```
   TeamAgent.auto_start_member / auto_start_all
     → TeamBackend.startup_member / startup
     → CAS: try_transition_member_status(UNSTARTED → STARTING)   # 并发守卫
     → TeamBackend._spawn_and_publish
     → on_created = TeamAgent._on_teammate_created
     → SpawnManager.spawn_teammate → process | inprocess
   ```

   CAS 守卫是这条链上**唯一**的并发闸：第一个到的路径把 `UNSTARTED→STARTING` 抢
   成功，其余并发路径查到 `STARTING`/`READY` 直接返回 False。spawn 失败回滚
   `STARTING→UNSTARTED` 保证可重试。

   漏斗有**三个触发点**，全都是"消息 / 任务要投给一个还没起来的成员"：

   | 触发点 | 代码位置 |
   |---|---|
   | leader `send_message`（点对点 / 多播 / 广播）自动拉起收件人 | `tools/tool_message.py::_SendMessageBase._auto_start_members` |
   | interact dispatch（`@member` / `@all` / operator 消息）| `runtime/manager.py::_dispatch_payload` |
   | 调度器投递交接消息（F_62）| `agent/scheduling/scheduler.py` |

   模式由 `TeamAgentSpec.spawn_mode` 决定。任何"自己 import 一下 process_manager
   或 inprocess_spawn"的旁路调用都是错的；任何"在 spawn 工具里顺手把 agent 拉起来"
   的改动也是错的——那会绕过 CAS 守卫。
2. **Spawned 实例从不进 leader pool**：两种模式的 spawned member 都通过
   `Runner.run_agent_team(..., member=True)` 跳过 activate / dispatch。
   只有 spec 路径上的 leader 才进 pool。
3. **Wire 契约对称**：`SpawnPayloadBuilder.build_spawn_payload` 输出的
   每个键都是 `TeamAgent.from_spawn_payload` 的读取键。改一边必须同改另一边，
   否则跨进程拉起立即崩。当前键集：
   `coordination.{team_name, display_name, leader_member_name, member_name, role, desc, transport}`、`query`。
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
6. **Worktree 隔离由 leader 侧 spawn 托管，实现落在 `agent_teams/worktree/`**：
   `SpawnManager.build_context_from_db` 委托
   `TeammateWorktreeLifecycle.ensure_member_worktree(teammate, role)`（
   `SpawnManager.worktree_lifecycle`），后者只在
   `TeamMember.options.worktree.isolation == "worktree"` 时给 teammate 建 / 复用
   owner-scoped 隔离工作树；team rail 不再向 teammate 暴露 `enter_worktree` /
   `exit_worktree` 作为手动兜底。命名固定为 `agent-{team}-{member}-{digest}`
   （`worktree/naming.py`）。`TeamMember.options.worktree` 持久化 `isolation/path`；
   旧库 migration 会把 `model_ref_json` backfill 到 `options.model_ref` 后删除旧列。
   完整 worktreeInfo（`worktree_name/worktree_branch/head_commit`）留在 leader 侧
   宿主内存；`TeamRuntimeContext` 只携带 `worktree_path` 作为 teammate 的
   cwd override。
7. **Worktree workspace 不走 cleanup_path**：`AgentConfigurator.setup_agent`
   看到 `ctx.worktree_path` 时把成员 `WorkspaceSpec.root_path` 覆盖为该路径，
   并关闭 stable_base；该路径不能注册到 `TeamBackend.cleanup_path`，否则
   `clean_team` 会绕过 `git worktree remove` 直接删除工作树。
8. **Worktree finalize fail-closed，且不在 `cleanup_teammate` 里**：finalize 由
   `TeammateWorktreeLifecycle` 拥有，两个入口——
   `finalize_non_contributing_member_worktrees()`（leader round 收尾，经
   `TeamAgent.finalize_non_contributing_worktrees`）与
   `finalize_all_member_worktrees_for_team_clean()`（`clean_team` 删 DB 行之前）。
   `cleanup_teammate` **只**负责摘 observer + 停心跳 + `force_kill`，不碰 worktree。

   `_finalize_member_worktree` 的判定是 **fail-closed 的"任何不确定都保留"**，按序：
   worktree 目录已不存在 → 清元数据；`hook_based` → 保留；`count_changes` 返回
   `None`（无法验证）→ 保留；有变更 → 先按 `_TEAM_RUNTIME_FILES` /
   `_TEAM_RUNTIME_DIRS` 白名单剔除团队运行时自产文件（`AGENT.md` / `memory/` /
   `todo/` 等）后重数，仍 > 0 → 保留；repo root 无法解析 → 保留；**只有全部通过才**
   `remove_worktree` 并清空 `options.worktree.path`。保留下来的 worktree 由 leader
   依据 `worktree_path` 与可用 branch 信息处理冲突。
9. **Inprocess chunk forwarder 与 handle 同生命周期**：`SpawnManager` 在
   inprocess 路径上立即 `_wire_inprocess_chunk_forward` 把 teammate
   `StreamController` 的 chunk 转发到 leader `stream_queue`（forwarder 存在 handle 的
   `chunk_forward` 字段上）；`cleanup_teammate` 必须先 `remove_chunk_observer` 再
   `force_kill`，否则会有迟到 chunk 落进已废弃的 leader 队列。子进程模式没有这个钩子，
   teammate chunk 只能走 messager（当前 unimplemented，列为 future work）。
10. **取消 / 暂停一律转发给 runtime，`StreamController` 自己不做超时编排**。
    四个方法各自只有一行，语义全在 `harness` 那边（见 [[F_60]]）：

    | 方法 | 转发 | 语义 |
    |---|---|---|
    | `cancel_agent()` | `harness.abort(immediate=True)` | 硬取消，回滚到上一个边界 |
    | `cooperative_cancel()` | `harness.abort(immediate=False)` | 请求当前 round 优雅收尾，不回滚 |
    | `pause_agent()` | `harness.pause()` | 停在最近的 inner iteration 边界，**round 保留**、可 resume |
    | `resume_agent(query=)` | `harness.resume(query=...)` | 原地续跑；`query` 非空 = 冷恢复（harness 重建、上下文从 checkpoint 还原）|

    `drain_agent_task()` 就是 `cancel_agent()`（`stop` / `destroy` 用；lifecycle
    *pause* 必须走 `pause_agent`，否则 round 被丢弃而不是被保留）。**曾经的
    "`_cancel_requested` + `wait_for(2.0)` + `task.cancel()` 兜底"两阶段编排、
    以及常量 `_COOPERATIVE_ABORT_TIMEOUT_SECONDS` 都已随 [[F_28]] 删除**——超时
    与安全检查点是 harness supervisor 的职责，不要在本层重建。
11. **清理路径吞 `CancelledError + Exception`**：`SpawnManager.cleanup_teammate`
    （`suppress(Exception)`，摘 observer）、`SpawnManager.cancel_recovery_tasks`
    与 `StreamController.stop()`（均 `suppress(asyncio.CancelledError, Exception)`，
    await 已 cancel 的 task）里的 suppress 是 invariant——caller 已经表态要拆掉这
    条链，再把异常往外抛会污染上层 shutdown。重构清理路径时必须保留，并检查
    `import contextlib` 还在（之前漏过一次）。`shutdown_all_handles` 自己不 suppress，
    它逐个走 `cleanup_teammate` 并 `try/except` 记日志后继续。
12. **transient-retry 状态是 per-cycle 私有**：`_retry_attempt` /
    `_swallow_failed_round` 由 `StreamController.start()` 在每个 run cycle 入口重置，
    `_swallow_failed_round` 另在每个 round `started` 事件上清零（`_map_round`）。
    这两个字段绝不能跨 cycle 存活，否则上一个 cycle 耗尽的重试预算会让新 cycle 的
    第一个 transient 错误直接透传给消费者。
13. **Observer 例外自隔离**：`_forward_outputs` fan-out 时单个 observer 抛异常
    **只**会被自动 `remove_chunk_observer`（记 exception 日志后继续），绝不能让本地
    stream 被 consumer 阻塞或拖垮。新增 observer 实现要做到 idempotent detach。
14. **`ExecutionStatus` 只由 runtime 的 round 事件驱动，且每条路径都收敛到 IDLE**：
    `_map_round(kind)` 是唯一发布点，四种 kind 各走一条合法边链——
    `started` → STARTING → RUNNING；`finished` → COMPLETING → COMPLETED → IDLE；
    `aborted` / `paused` → CANCEL_REQUESTED → CANCELLING → CANCELLED → IDLE；
    `failed` → FAILED → IDLE。状态机不允许"round 跑完了但既不是 COMPLETED 也不是
    CANCELLED / FAILED"的中间态。**`StreamController` 不再自己判断"这轮是不是被取消
    了"**——它只翻译 runtime 报上来的 kind。
15. **Spawn 子进程命令固定**：`spawn_process` 用 `sys.executable -m
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
    def has_live_handle(self, member_name: str) -> bool: ...
    async def cleanup_teammate(self, member_name: str) -> None: ...
    async def restart_teammate(self, member_name: str, max_retries: int = 3) -> bool: ...
    async def on_teammate_unhealthy(self, member_name: str) -> None: ...
    async def shutdown_all_handles(self) -> None: ...
    async def cancel_recovery_tasks(self) -> None: ...
    async def build_context_from_db(self, member_name: str) -> Optional[TeamRuntimeContext]: ...
    async def publish_restart_event(self, member_name: str, restart_count: int) -> None: ...

    worktree_lifecycle: TeammateWorktreeLifecycle   # worktree 创建 / finalize 全在这里
```

错误语义：

- `cleanup_teammate` 吞所有清理异常并继续走 `force_kill` —— 关停路径不允许抛。
  它**不做** worktree finalize（见不变量 8）。
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

**`StreamController` 不驱动 round**（[[F_28]]）。它是 runtime 与 team 之间的适配层：
转发 + 打 tag 输出、映射状态、转发 cancel/pause/resume。起轮 / steer / follow-up 全在
`TeamAgent` 上，一律落到 `harness.send(...)`：

```python
# TeamAgent —— round 驱动面（runtime 的单 supervisor 负责序列化，本层不排队、不分支）
async def deliver_input(self, content: Any, *, use_steer: bool = True) -> None: ...  # 统一入口
async def start_agent(self, content: str) -> None: ...          # → harness.send(content)
async def steer(self, content: str) -> None: ...                # → harness.send(content, immediate=True)
async def follow_up(self, content: str) -> None: ...            # → harness.send(content, immediate=False)
```

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
        request_completion_poll_callback: Optional[Callable[[], Awaitable[None]]] = None,
    ): ...

    # cycle 生命周期（由 CoordinationKernel 调，非 round 级）
    async def start(self) -> None: ...   # subscribe(on_state, on_round) + 起 _forward_outputs task
    async def stop(self) -> None: ...    # 停 forwarder task

    # 转发给 runtime
    async def cancel_agent(self) -> None: ...        # harness.abort(immediate=True)
    async def cooperative_cancel(self) -> None: ...  # harness.abort(immediate=False)
    async def pause_agent(self) -> None: ...         # harness.pause()
    async def resume_agent(self, *, query: str | None = None) -> None: ...  # harness.resume(query=)
    async def drain_agent_task(self) -> None: ...    # == cancel_agent()

    # stream 收尾
    def close_stream(self) -> None: ...                                       # put_nowait(None)
    def emit_completion_and_close(self, member_count: int, task_count: int) -> None: ...

    # 状态查询（一律直读 harness.state，本层不缓存）
    def is_agent_running(self) -> bool: ...      # harness.state is RUNNING
    def has_in_flight_round(self) -> bool: ...   # == is_agent_running()
    def has_pending_interrupt(self) -> bool: ...
    def is_valid_interrupt_resume(self, user_input: Any) -> bool: ...

    # observer fan-out
    def add_chunk_observer(self, cb: ChunkObserver) -> None: ...
    def remove_chunk_observer(self, cb: ChunkObserver) -> None: ...
```

错误语义：

- `cancel_agent` / `cooperative_cancel` **自己不发布任何执行状态**。状态序列
  （`CANCEL_REQUESTED → CANCELLING → CANCELLED → IDLE`）由 runtime 随后报上来的
  `aborted` / `paused` round 事件经 `_map_round` 发布——本层只负责翻译，见不变量 14。
- `drain_agent_task` 就是 `cancel_agent`：`stop` / `destroy` 用它把 round 直接丢弃。
  **lifecycle *pause* 绝不能走这里**，必须走 `pause_agent`，否则 round 不是被暂停
  而是被销毁（见 [[F_60]]）。
- **transient retry 在 forwarder 里就地消化，不再 raise**：`_handle_retry` 检出
  `task_failed` chunk（`[code] ...` 前缀，`_ERROR_CODE_PATTERN` 解析）后，若
  `code ∈ _RETRYABLE_ERROR_CODES`（当前 `{181001}`）且 `_retry_attempt <
  _MAX_RETRY_ATTEMPTS`（10），就置 `_swallow_failed_round=True` 吞掉该 round 剩余
  chunk，并 `harness.send(_RETRY_QUERY)` 重驱一轮。**重试耗尽或非重试错误码 →
  返回 False，让 `task_failed` chunk 原样流给消费者**——supervisor 模型下不再有
  "耗尽即 raise `build_error(...)`" 的路径。
- `_forward_outputs` 整体包在 `except Exception` 里记 exception 日志；
  `CancelledError` 是 `BaseException`，不会被捕获，cancel 正常向上传播（`stop()`
  依赖这一点）。

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
- `chunk_forward` 字段仅 inprocess 使用，由 `SpawnManager._wire_inprocess_chunk_forward`
  写入、`cleanup_teammate` 读出并 `remove_chunk_observer` 后置回 `None`。

### `StreamController` 状态字段

**没有 round 级字段**——round 状态一律直读 `harness.state`，本层不缓存
（`is_agent_running()` / `has_in_flight_round()` 都是 `harness.state is RUNNING`）。
曾经的 `agent_task` / `streaming_active` / `pending_inputs` /
`pending_interrupt_resumes` / `_cancel_requested` 随 [[F_28]] 一并删除：
单 supervisor 模型下 runtime 自己序列化输入、自己管 round，本层再存一份必然漂移。

| 字段 | 类型 | 生命周期 |
|---|---|---|
| `stream_queue` | `Optional[asyncio.Queue]` | 由 owner（`TeamAgent`）在 `invoke` / `stream` 入口赋值；`close_stream` 通过 `put_nowait(None)` 标志结束 |
| `_forward_task` | `Optional[asyncio.Task]` | `start()` 创建（幂等：done 才重建），`stop()` cancel + await；每个 run cycle 一个 |
| `_chunk_observers` | `list[ChunkObserver]` | inprocess teammate fan-out 注入；observer 抛异常自动 `remove_chunk_observer` |
| `_retry_attempt` | `int` | transient-retry 计数；`start()` 每个 cycle 重置为 0（见不变量 12）|
| `_swallow_failed_round` | `bool` | 命中可重试错误后吞掉该 round 剩余 chunk；`start()` 与每个 round 的 `started` 事件都清零 |

### 状态机：两个维度，都由 runtime 事件驱动

```
runtime phase (HarnessState)          →  MemberStatus        [_map_state]
  RUNNING                             →  BUSY
  IDLE                                →  READY + _on_idle_settled()
  PAUSING / PAUSED / TERMINATED       →  （不动，交给 lifecycle 层）

runtime round event (kind)            →  ExecutionStatus     [_map_round]
  started                             →  STARTING → RUNNING
  finished                            →  COMPLETING → COMPLETED → IDLE
  aborted | paused                    →  CANCEL_REQUESTED → CANCELLING → CANCELLED → IDLE
  failed                              →  FAILED → IDLE
```

`_on_idle_settled()`（round 链结束、runtime 回到 IDLE 时触发）按优先级判终止：

1. `state.team_cleaned` 置位 → `close_stream()`（临时团队 leader 调完 `clean_team` 后
   结束自身 stream 的**唯一**路径，见 [[F_10]]）；
2. `team_member.status() == SHUTDOWN_REQUESTED` → `close_stream()`（成员跑完末轮后的
   graceful 退场；**终态 `SHUTDOWN` 不在这里写**，由 Runner finally 的
   `manager.finalize_member` 落，见 `S_06` 不变量 13 与 `S_03` 机制 18）；
3. 否则 → `_wake_mailbox_if_interrupt_cleared()` + `request_completion_poll_callback`
   （leader 用它立刻重估团队完成度）。

注意 IDLE 分支里 `_update_status(READY)` 先于自检执行，但对一个
`SHUTDOWN_REQUESTED` 的成员**这笔写入必然失败**——`MEMBER_TRANSITIONS` 不含
`SHUTDOWN_REQUESTED → READY`，DAO 的 CAS 匹配不到合法前驱，rowcount=0，静默返回
False。状态机表本身就是这里的护栏；把 `update_member_status` 改成"写不进去就抛"或
放宽这条边，都会让第 2 条自检瞎掉、成员永远关不掉。

`MemberStatus.READY → BUSY → READY/ERROR` 是成员维度的快照，
`ExecutionStatus.STARTING → RUNNING → ... → IDLE` 是 round 维度的快照，
两个状态机各自维护、各自发布事件；不要在新代码里只更新一个。

## 与其它 spec 的关系

- **runtime / pool / dispatch**：spawn 出来的 teammate / human-agent
  绕开 pool（`member=True`），所以 leader-only 的 pool 不变量是本 spec
  的前提。
- **coordination**：`CoordinationKernel` 拥有 `StreamController` 的 cycle 生命周期
  （`start()` / `stop()`）。`_map_round` 发布的 `ExecutionStatus` 会被 coordination
  的 handler 看到并触发 lifecycle 反应；本 spec 只承诺状态发布次序（不变量 14），
  反应策略落在 `S_03`。成员 graceful 退场的判定（harness-input 闸门）也在 `S_03`
  机制 18，本 spec 只承诺 `_on_idle_settled` 读到 `SHUTDOWN_REQUESTED` 时关流。
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
