# TeamAgent 架构规约

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 编号 / slug | S_02 / team-agent-architecture |
| 关联模块 | `openjiuwen/agent_teams/agent/` |
| 最近一次修订日期 | 2026-05-14 |
| 关联 feature | F_11_leader-member-status-tracking.md、F_10_temporary-leader-clean-team-stream-end.md |

## 范围 / 边界

### 本规约管什么

`openjiuwen/agent_teams/agent/` 子系统的**总体骨架**：

- 四象限拆分：`TeamAgentBlueprint` / `TeamAgentState` / `PrivateAgentResources` / `TeamInfra` 的字段构成、所有权、生命周期。
- `AgentConfigurator` 把 `TeamAgentSpec` + `TeamRuntimeContext` 物化为四象限对象的装配链与构造顺序约束。
- `TeamAgent` 作为 leader / teammate / human-agent **单一实现**的角色切换契约。
- Manager 拓扑：`SpawnManager` / `StreamController` / `CoordinationKernel` / `SessionManager` / `RecoveryManager` 的归属象限与依赖关系（**只列拓扑**，行为细节交给各自 spec）。
- `payload.py` 在跨进程 wire 契约里的位置。
- Card / Config / Runtime 三层在本子系统的落点。

### 本规约**不**管什么

| 子主题 | 归属 spec |
|---|---|
| coordination/ 内部的事件总线、dispatcher、handler | S_03 coordination |
| SessionManager / RecoveryManager 的状态机与 checkpoint 字段 | S_04 session-recovery |
| SpawnManager 的进程生命周期 / StreamController 的 round 状态机 / pending input 队列语义 | S_05 spawn-stream |
| `runtime/` 的对象池、7 路 dispatch、并发门禁、finalize 决策 | runtime/ 自身 spec |
| `interaction/` 的三视角输入、`@member` 路由、HITT | interaction/ 自身 spec |

跨子系统的字段引用走 `runtime_context.py` 与 `schema/` 的公共类型；本规约只规定 `agent/` 内部如何拆。

## 不变量

任意时刻，`agent/` 子系统必须满足下列断言：

1. **角色不可变**：`TeamAgentBlueprint.role` 在 `AgentConfigurator.setup_infra` 写入后不再变化；不存在"运行中改 role"的合法路径。同一个 `TeamAgent` 实例不会从 LEADER 变成 TEAMMATE。
2. **TeamAgent 单一实现**：LEADER / TEAMMATE / HUMAN_AGENT **共用同一个** `TeamAgent` 类。新增角色行为先考虑配置分支，禁止派生新类。
3. **DeepAgent 不被直接持有**：`PrivateAgentResources.harness: TeamHarness` 是访问底层 DeepAgent 运行时（config / model / workspace / rails / streaming）的**唯一入口**。`PrivateAgentResources` 不增加 `deep_agent` 字段；`TeamAgent` 不旁路 harness 访问 DeepAgent。
4. **四象限互斥**：每个字段恰好属于一个象限——
   - 跨 session 不变 → `TeamAgentBlueprint`（`frozen=True`）
   - 跨 operator 可变、跨 session 可变 → `TeamAgentState`
   - 单实例独占的运行时资源 → `PrivateAgentResources`
   - 单进程内多 member 共享的基础设施 → `TeamInfra`
   字段放错象限是设计错误，不是风格问题。
5. **Operator 私有状态留在 operator 内**：`SpawnManager.spawned_handles`、`CoordinationKernel.subscribed_topics`、`StreamController.pending_inputs` 等不进 `TeamAgentState`。`TeamAgentState` 只接纳**跨 operator 边界**的字段。
6. **Spec → build → Runtime 单向流**：
   - `TeamAgentSpec` / `TeamRuntimeContext` 是**可 JSON 序列化的装配数据**，不持有 `Runner` / `Session` / 文件句柄等运行时资源。
   - `AgentConfigurator.configure(spec, ctx)` 产出运行时对象，运行时对象**不回写** Spec/Context。
   - 热更新走 session + resume，不走 Spec 反向污染。
7. **per-process 共享 ≠ 进程间共享**：`TeamInfra` 的"共享"语义是"同一进程内所有 member 都能看到的资源"。LEADER 和 TEAMMATE 在不同进程，`TeamInfra` 实例**互不引用**。需要跨进程的状态走 `messager` / `team_backend.db`。
8. **TeamMember 句柄受 backend 约束**：`create_member_handle` 在 `TeamInfra.team_backend is None` 时返回 `None`，调用方必须处理这种情况；不允许预设"句柄一定在"。
9. **`payload.build_spawn_payload` 是公共 wire 契约**：输出键集合 = `TeamAgent.from_spawn_payload` 的读取键集合。任何字段增删必须对称改两端，否则跨进程 spawn 直接失败。
10. **Card 不持运行时**：`AgentCard` 是可序列化标识。`Runner` / `Session` / `harness` / `messager` 等不允许塞进 `AgentCard`。`AgentCard` 由 `AgentConfigurator(card)` 构造时一次性绑定，进入 `TeamAgentBlueprint.card`。
11. **构造顺序约束**：`AgentConfigurator.setup_infra` 必须在 `setup_agent` 之前调用——后者依赖前者已经写入的 `_blueprint` / `_spawn_payload_builder` / `team_backend` / `messager`。`configure()` 是 `setup_infra` + `setup_agent` 的唯一对外封装。
12. **LEADER-only 资源不外泄**：`PrivateAgentResources.model_allocator` 只在 `ctx.role == LEADER` 时构造；非 LEADER 路径访问到的 allocator 永远是 `None`。
13. **HUMAN_AGENT 无 first-iteration gate**：`PrivateAgentResources.first_iter_gate` 在 `ctx.role == HUMAN_AGENT` 时必须为 `None`——人类成员没有自主任务循环和 mailbox 轮询，gate 在这条路径上无意义。
14. **Spec 不持运行时引用**：`TeamAgentSpec` / `TeamRuntimeContext` 只承载 dataclass / pydantic 字段；新增字段先判断"装配数据 vs 运行时资源"，运行时资源进 Config/Manager/Runtime。

## 接口契约

### `TeamAgent`

```python
class TeamAgent(BaseAgent):
    def __init__(self, card: AgentCard) -> None: ...

    # --- Quadrant accessors ---
    @property
    def blueprint(self) -> TeamAgentBlueprint | None: ...
    @property
    def state(self) -> TeamAgentState: ...
    @property
    def infra(self) -> TeamInfra: ...
    @property
    def resources(self) -> PrivateAgentResources: ...
    @property
    def harness(self) -> TeamHarness | None: ...   # sole DeepAgent access path

    # --- Identity (read-only after configure) ---
    @property
    def role(self) -> TeamRole: ...
    @property
    def member_name(self) -> str | None: ...
    @property
    def team_name(self) -> str | None: ...
    @property
    def lifecycle(self) -> str: ...                # "temporary" | "persistent"

    # --- Manager handles ---
    @property
    def spawn_manager(self) -> SpawnManager: ...
    @property
    def stream_controller(self) -> StreamController: ...
    @property
    def coordination(self) -> CoordinationKernel: ...
    @property
    def session_manager(self) -> SessionManager: ...
    @property
    def recovery_manager(self) -> RecoveryManager: ...

    # --- BaseAgent abstract methods ---
    def configure(self, spec: TeamAgentSpec, context: TeamRuntimeContext) -> "TeamAgent": ...
    async def invoke(self, inputs, session=None): ...
    async def stream(self, inputs, session=None, stream_modes=None): ...

    # --- Round driver ---
    async def start_agent(self, content: str) -> None: ...
    async def deliver_input(self, content: Any, *, use_steer: bool = True) -> None: ...
    async def steer(self, content: str) -> None: ...
    async def follow_up(self, content: str) -> None: ...
    async def cancel_agent(self) -> None: ...
    async def shutdown_self(self) -> None: ...

    # --- Spawn wire contract ---
    def build_spawn_payload(self, ctx: TeamRuntimeContext, *, initial_message: str | None = None) -> dict[str, Any]: ...
    @classmethod
    async def from_spawn_payload(cls, payload: dict[str, Any]) -> "TeamAgent": ...

    # --- Interact-driven lazy startup ---
    async def auto_start_member(self, member_name: str) -> bool: ...
    async def auto_start_all(self) -> list[str]: ...

    # --- Recovery ---
    @classmethod
    def recover_from_session(cls, session, team_name: str) -> "TeamAgent": ...
    async def recover_for_existing_session(self, session) -> None: ...
    async def resume_for_new_session(self, session) -> None: ...
```

签名要点：

- `configure(spec, context)` 是 BaseAgent 抽象方法的本子系统重载；其它 manager 依赖此调用产生的 `_blueprint`。
- `harness` 是 `Optional`：在 `configure()` 之前为 `None`，调用方必须先 `is_agent_ready()` 或显式判空。
- `from_spawn_payload` 与 `recover_from_session` 是两条独立的 TeamAgent 重建路径——前者跨进程 wire 重建，后者从 session checkpoint 重建——但都最终走 `agent.configure(spec, context)`。

### `AgentConfigurator`

```python
class AgentConfigurator:
    def __init__(self, card: AgentCard) -> None: ...

    @property
    def blueprint(self) -> TeamAgentBlueprint | None: ...
    @property
    def infra(self) -> TeamInfra: ...
    @property
    def resources(self) -> PrivateAgentResources: ...

    def configure(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> TeamHarness:
        """setup_infra then setup_agent. The only legal external entry."""

    def setup_infra(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
        *,
        on_teammate_created: Callable | None = None,
        on_team_cleaned: Callable | None = None,
    ) -> None:
        """Phase 1: blueprint, spawn payload builder, messager, workspace, model allocator, team backend, worktree.

        ``on_team_cleaned`` is threaded down to ``setup_team_backend`` ->
        ``TeamBackend``; it fires on the ``clean_team`` success path so the
        hosting ``TeamAgent`` can latch ``state.team_cleaned``.
        """

    def setup_agent(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> TeamHarness:
        """Phase 2: build TeamHarness (which owns DeepAgent), attach rails, build memory manager."""

    def resolve_agent_spec(
        self,
        spec: TeamAgentSpec,
        role: TeamRole,
        member_name: str | None = None,
    ) -> AgentSpec:
        """Pick the per-member agent spec, falling back: member_name → role → 'teammate' → 'leader'."""

    # Spawn payload delegation
    def build_spawn_payload(self, ctx, *, initial_message=None) -> dict[str, Any]: ...
    def build_member_context(self, member_spec: TeamMemberSpec) -> TeamRuntimeContext: ...
    def build_spawn_config(self, ctx: TeamRuntimeContext) -> SpawnAgentConfig: ...
```

构造顺序契约：

1. `setup_infra` 必须先于 `setup_agent`。
2. `setup_infra` 内部固定顺序：解析 agent spec → 构造 `TeamAgentBlueprint` → 构造 `SpawnPayloadBuilder` → 创建 `messager` → 创建 `workspace_manager`（如启用）→（LEADER）创建 `model_allocator` → 创建 `team_backend` →（非 LEADER 且启用）创建 `worktree_manager`。
3. `setup_agent` 内部固定顺序：mount workspace → 构造 `TeamHarness.build(...)` → 构造 `TeamMemoryManager`（如启用）→ 跑 `agent_customizer`。
4. `model_allocator` 必须在 `team_backend` 之前就绪——`team_backend` 构造时直接读 `self.model_allocator.allocate`。

### 四象限容器

```python
@dataclass(frozen=True, slots=True)
class TeamAgentBlueprint:
    card: AgentCard
    spec: TeamAgentSpec
    ctx: TeamRuntimeContext
    role_policy: str
    language: str

    @property
    def role(self) -> TeamRole: ...
    @property
    def member_name(self) -> str | None: ...
    @property
    def lifecycle(self) -> str: ...
    @property
    def team_spec(self) -> TeamSpec | None: ...


@dataclass
class TeamAgentState:
    team_session: AgentTeamSession | None
    team_member: TeamMember | None
    pending_user_query: str
    event_listeners: list
    team_cleaned: bool


@dataclass
class PrivateAgentResources:
    harness: TeamHarness | None
    worktree_manager: WorktreeManager | None
    memory_manager: TeamMemoryManager | None
    first_iter_gate: FirstIterationGate | None
    model_allocator: ModelAllocator | None   # LEADER only


@dataclass
class TeamInfra:
    messager: Messager | None
    team_backend: TeamBackend | None
    workspace_manager: TeamWorkspaceManager | None
    workspace_initialized: bool
    task_manager: Any
    message_manager: Any
```

### `TeamMember` 与 `create_member_handle`

```python
def create_member_handle(
    *,
    member_name: str,
    blueprint: TeamAgentBlueprint,
    infra: TeamInfra,
    agent_card: AgentCard,
) -> TeamMember | None:
    """Returns None when infra.team_backend is None."""
```

`TeamMember` 是单成员的 DB / 消息总线投影：负责 status / execution_status 的写入 + 状态变更事件发布。`create_member_handle` 是**纯构造函数**——只需已绑定的 `team_backend`，不碰 DB——所以 **LEADER / TEAMMATE / HUMAN_AGENT 一律在 `configure()` → `_setup_agent` 内同步创建**，单一构造点、无角色分支。

leader 自己的 team row 由 `BuildTeamTool` 物化，比 handle 晚——freshly built 的 leader 持有一个"行尚未存在"的 handle。这没问题：`TeamMember` 容忍行缺失（status 读返回 `None`，写静默返回 `False`），所以无需等行落库再构造 handle。

### `SpawnPayloadBuilder`

```python
class SpawnPayloadBuilder:
    def __init__(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> None: ...

    def build_spawn_payload(self, ctx, *, initial_message=None) -> dict[str, Any]:
        """Cross-process wire format. Output keys MUST stay in sync with TeamAgent.from_spawn_payload."""

    def build_member_context(self, member_spec: TeamMemberSpec) -> TeamRuntimeContext: ...
    def build_member_messager_config(self, member_name: str) -> MessagerTransportConfig | None: ...
    def build_spawn_config(self, ctx: TeamRuntimeContext) -> SpawnAgentConfig: ...
```

输出 schema（公共契约）：

```python
{
    "coordination": {
        "team_name": str,
        "display_name": str,
        "leader_member_name": str | None,
        "member_name": str,
        "role": str,                # TeamRole.value
        "persona": str | None,
        "transport": dict | None,   # MessagerTransportConfig.model_dump
    },
    "query": str,
}
```

`SpawnPayloadBuilder` 内部维护 `_member_port_map` / `_teammate_port_counter` 作为单调端口分配器，保证同一 builder 生命周期内同一 `member_name` 拿到稳定端口。

## 数据结构

### 四象限字段表

| 象限 | 字段 | 类型 | 设置时机 | 修改者 |
|---|---|---|---|---|
| Blueprint | `card` | `AgentCard` | `TeamAgent.__init__` 传入 → `setup_infra` 锁定 | 一次性 |
| Blueprint | `spec` | `TeamAgentSpec` | `setup_infra` | 一次性（frozen） |
| Blueprint | `ctx` | `TeamRuntimeContext` | `setup_infra` | 一次性（frozen） |
| Blueprint | `role_policy` | `str` | `setup_infra` 调用 `prompts.role_policy(role, language)` | 一次性 |
| Blueprint | `language` | `str` | `setup_infra` 调用 `_resolve_language(agent_spec.language)` | 一次性 |
| State | `team_session` | `AgentTeamSession | None` | `SessionManager` start / resume | SessionManager |
| State | `team_member` | `TeamMember | None` | `setup_agent`（所有角色同步创建） | TeamAgent |
| State | `pending_user_query` | `str` | `invoke` / `stream` 入口 | TeamAgent |
| State | `event_listeners` | `list` | `add_event_listener` / `remove_event_listener` | 调用方 |
| State | `team_cleaned` | `bool` | `clean_team` 成功回调（`TeamBackend.on_team_cleaned` → `TeamAgent._mark_team_cleaned`） | TeamAgent |
| Resources | `harness` | `TeamHarness | None` | `setup_agent` 调用 `TeamHarness.build(...)` | 一次性 |
| Resources | `worktree_manager` | `WorktreeManager | None` | `setup_infra`（非 LEADER 且启用 worktree） | 一次性 |
| Resources | `memory_manager` | `TeamMemoryManager | None` | `setup_agent`（启用 memory） | 一次性 |
| Resources | `first_iter_gate` | `FirstIterationGate | None` | `setup_agent`（非 HUMAN_AGENT） | 一次性 |
| Resources | `model_allocator` | `ModelAllocator | None` | `setup_infra`（LEADER），可由 `attach_model_allocator` 替换；`update_model_pool` 重建 | LEADER 限定 |
| Infra | `messager` | `Messager | None` | `setup_infra` 调用 `create_messager(transport)` | 一次性 |
| Infra | `team_backend` | `TeamBackend | None` | `setup_infra` 末段构造 | 一次性 |
| Infra | `workspace_manager` | `TeamWorkspaceManager | None` | `setup_infra`（启用 workspace） | 一次性 |
| Infra | `workspace_initialized` | `bool` | runtime 上层 mount 完成后置 True | runtime |
| Infra | `task_manager` | `Any` | `setup_infra` 末段 = `team_backend.task_manager` | 一次性 |
| Infra | `message_manager` | `Any` | `setup_infra` 末段 = `team_backend.message_manager` | 一次性 |

### `Spec → build → Runtime` 数据流

```
TeamAgentSpec  +  TeamRuntimeContext
        │
        ▼
AgentConfigurator(card).configure(spec, ctx)
        │
        ├─ setup_infra(spec, ctx)
        │     • blueprint = TeamAgentBlueprint(card, spec, ctx, role_policy, language)
        │     • spawn_payload_builder = SpawnPayloadBuilder(spec, ctx)
        │     • infra.messager = create_messager(...)
        │     • infra.workspace_manager = create_workspace_manager(...)         (optional)
        │     • resources.model_allocator = build_model_allocator(...)          (LEADER only)
        │     • infra.team_backend = TeamBackend(...)
        │     • infra.task_manager / message_manager <- team_backend.*
        │     • resources.worktree_manager = WorktreeManager(...)               (non-LEADER, optional)
        │
        └─ setup_agent(spec, ctx)
              • mount workspace into agent ws_spec
              • resources.harness = TeamHarness.build(rails=[
                    TeamPolicyRail, TeamToolRail, TeamWorkspaceRail?,
                    FirstIterationGate?, TeamToolApprovalRail?,
                ])
              • resources.memory_manager = TeamMemoryManager(...)               (optional)
              • harness.run_agent_customizer(...)                                (optional)
        │
        ▼
TeamAgent (composition root)
   └─ wraps configurator → blueprint / state / resources / infra
   └─ wraps managers: SpawnManager / StreamController / CoordinationKernel /
                       SessionManager / RecoveryManager
```

### Manager 拓扑（归属象限 + 依赖）

```
                              TeamAgent
                                  │
        ┌─────────────────────────┼──────────────────────────┐
        │                         │                          │
        ▼                         ▼                          ▼
 StreamController         CoordinationKernel          SpawnManager
   reads:                  reads:                     reads:
     • blueprint             • blueprint                • state
     • state                 • state                    • configurator
     • resources             • coordination/*           • TeamAgent (back-ref)
   writes:                  drives:                    drives:
     • state.team_member       • EventBus                 • Runner.spawn_agent
       (via callback)          • dispatcher                • inprocess_spawn
                               • handlers
                               • TeamHarness (round)
                                                         │
                                                         ▼
                                              SessionManager   RecoveryManager
                                                reads:           reads:
                                                  • state          • configurator
                                                  • configurator   • spawn_manager
                                                  • recovery_mgr   uses:
                                                drives:              • team_session
                                                  • Session            persistence
                                                  • checkpoint
```

依赖单向且**不存在环**：

- `SpawnManager → AgentConfigurator + TeamAgent`（用 getter 闭包避免硬引用环）。
- `RecoveryManager → AgentConfigurator + SpawnManager`。
- `SessionManager → state + AgentConfigurator + RecoveryManager`。
- `StreamController → blueprint + state + resources +` 三个回调（status / execution / wake mailbox）。回调由 `TeamAgent` 注入，`StreamController` 不反向持有 `TeamAgent`。`StreamController._run_one_round` 的 round-end finally 把 `state.team_cleaned` 作为**最高优先级终止条件**：置位则 `close_stream()` 入队 `None`，不 restart pending input / interrupt resume——这是临时团队 leader 调用 `clean_team` 后唯一能结束自身 stream 的路径（leader 故意忽略自己的 `TeamCleanedEvent`，见 F_10）。
- `CoordinationKernel → TeamAgent`（构造时持引用，是事件驱动的总线 + dispatcher 容器；细节见 S_03）。

每个 manager 的**内部**状态留在自己里（`SpawnManager.spawned_handles`、`StreamController.pending_inputs` 等），不进 `TeamAgentState`。这是不变量 5 在拓扑层的具体落点。

### TeamHarness 作为 DeepAgent 的唯一壳

`TeamAgent` **不直接持有 DeepAgent**。`PrivateAgentResources.harness` 是访问 DeepAgent 的唯一路径：

- `TeamHarness.build(agent_spec, role, member_name, *rails)` 由 `AgentConfigurator.setup_agent` 调用。
- 所有访问 DeepAgent runtime（model / workspace / sys_operation / rails / streaming）的代码都经 `harness.<attr>`。
- 新代码不得在 `TeamAgent` 上挂 `_deep_agent` 字段；不得绕过 harness 直接 import DeepAgent 实例。

这条不变量保证了"harness 装配链"是单点：rails 注入、prompt builder 装配、stream 控制都汇聚在 `TeamHarness.build` 一处。

## 与其它 spec 的关系

| Spec | 关系 |
|---|---|
| **S_03 coordination** | 本 spec 描述 `CoordinationKernel` 在 TeamAgent 拓扑中的归属与对外契约（构造 / start / stop / 事件入队接口）；S_03 描述 EventBus / EventDispatcher / handlers 的内部结构、触发规则、role-scoped protocol 切分。 |
| **S_04 session-recovery** | 本 spec 列出 `SessionManager` / `RecoveryManager` 的归属象限、对 `TeamAgentState` 的读写边界；S_04 描述 session checkpoint 字段、teams namespace 分桶布局、状态恢复流程。 |
| **S_05 spawn-stream** | 本 spec 固定 `SpawnManager` / `StreamController` 的拓扑位置与 `payload.py` wire 契约；S_05 描述 spawn 的 process / inprocess 分支、teammate 拉起 / 心跳 / 重启细节，以及 round 状态机、pending input / interrupt 队列语义。 |
| `runtime/` 自身 spec | `runtime/` 在 `agent/` 之外管 TeamAgent 对象池、7 路 dispatch、并发门禁、finalize 决策。本 spec 不重复，仅约定：`TeamAgent.configure` 必须经 `runtime/` 的 `manager.activate` 路径写入对象池（leader 入口），spawn 出来的 teammate 不入 pool。 |
| `interaction/` 自身 spec | 三视角输入（GodView / Operator / HumanAgent）通过 `interaction/` 进 `coordination`；本 spec 只规定 `TeamAgent.broadcast` / `human_agent_say` / `interact` 是公共入口，路由解析交给 `interaction/router.py`。 |
| `schema/` | 本 spec 引用 `TeamAgentSpec` / `TeamRuntimeContext` / `TeamSpec` / `TeamRole` / `TeamMemberSpec` 等，不在此重复字段定义；schema 层声明字段引用，实现归 `models/` / `agent/`。 |
