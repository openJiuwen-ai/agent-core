# S_01 公开 API 表面与 Spec 流

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/__init__.py`、`openjiuwen/agent_teams/schema/blueprint.py`、`openjiuwen/agent_teams/schema/team.py`、`openjiuwen/agent_teams/runtime/manager.py`、`openjiuwen/core/runner/team_runner.py`、`openjiuwen/core/runner/runner.py` |
| 最近一次修订 commit | 18823271 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义两件事，仅此两件事：

1. `agent_teams` 子系统对外暴露的**公开 API 表面**——哪些符号是公共契约、哪些是内部实现。
2. `Spec → build() → Runtime` 单向流的**形态约束**——Spec 携带什么、不携带什么；build 在哪里发生；Runtime 是否回写 Spec。

不在本规约范围内（落到其它 spec）：
- 运行时对象池、7 路 dispatch 决策表、`InteractGate` 并发门禁、`finalize` / `finalize_member` 决策——见 `S_06_runtime-pool-dispatch`。
- 三视角交互 inbox、HITT 启用与人类成员路由——见 `S_07_interaction-views-and-hitt`。
- TeamAgent 内部装配链（spawn / coordination / stream / session manager）——见 `S_02_team-agent-architecture` / `S_03_coordination-protocol` / `S_04_session-and-recovery` / `S_05_member-spawn-and-stream`。
- Prompt 模板 / 工具描述 / i18n 文本归属——见 `S_08_team-tools-contract` / `S_09_prompts-and-rails` / `S_16_top-level-utilities`。

## 不变量

公开表面：

1. `openjiuwen/agent_teams/__init__.py` 的 `__all__` 是公开 API 的**完整集合**；任何不在 `__all__` 里的导入路径都是内部实现，不承担兼容性保证。
2. 装配 leader 的唯一公共路径是 `TeamAgentSpec(...).build()`；不存在与之并行的 factory wrapper（曾经的 `create_agent_team` / `resume_persistent_team` / `recover_agent_team` 已删除）。
3. 所有新增装配配置必须落到 `TeamAgentSpec` 字段；任何 wrapper / 顶层入口都不得通过 `**kwargs` 或平铺参数承接装配选项——形态上根除"扩参数列表"路径。
4. lifecycle 操作（cold recover / warm recover / 切 session / 续接持久团队）一律经 `Runner.run_agent_team[_streaming](agent_team=spec, session=...)` 触发，由 `runtime` 子系统的 dispatch 表自动选择分支；`agent_teams` 包未导出独立的恢复函数。

Spec 形态：

5. `TeamAgentSpec`、`LeaderSpec`、`TransportSpec`、`StorageSpec`、`DeepAgentSpec`、`TeamSpec`、`TeamMemberSpec`、`TeamRuntimeContext` 是 `pydantic.BaseModel`，可经 `model_dump()` 完整 JSON 序列化。
6. Spec 上禁止持有运行时引用：不持有 `Runner`、`Session`、文件句柄、socket、进程句柄、`asyncio` 对象、已构造的 `Messager` / `TeamAgent` 实例等。
7. `TeamAgentSpec.agent_customizer` 是唯一允许的 `Callable` 字段；其 `Field(exclude=True)` 标记保证它**不进入** `model_dump()`，且只能用于 `spawn_mode='inprocess'`，不能跨进程传递。
8. `TransportSpec.build()` 与 `StorageSpec.build()` 是 spec → 具体 Config 实例的**唯一桥**，必须经由 `_TRANSPORT_REGISTRY` / `_STORAGE_REGISTRY` 注册表派发；不允许调用方直接 import 具体 Config 类。

build 流向：

9. `TeamAgentSpec.build()` 是**单向**的：消费 spec、产出 `TeamAgent` 运行时实例；运行时实例不回写 spec、不修改入参 spec 的字段值。
10. `build()` 在返回前必须完成全部 spec 校验——保留名冲突、HITT 一致性、`model_pool ↔ model_router` 互斥、leader 模型可解析。任何校验失败必须抛 `ValueError` 或 `AGENT_TEAM_CONFIG_INVALID`，**不允许**返回半构造的 `TeamAgent`。
11. `build()` 调用前后，spec 的 `model_dump()` 输出在用户可见字段上保持稳定。framework 内部的字段补全只发生在两处：`@model_validator` 阶段（如 `transport` 的 inprocess 默认值）和 `build()` 阶段（router → pool 展开），且都在**同一次 build 内**完成，不延迟到运行时。

Runner 入口：

12. `Runner.run_agent_team` / `run_agent_team_streaming` 是公共团队入口；`base` / `member` 两个关键字标志是**互斥的路径开关**：默认（`base=False, member=False`）= agent_teams TeamAgent 路径；`base=True` = `multi_agent.BaseTeam` 路径；`member=True` = 已构造的 teammate / human-agent `BaseAgent` spawn-only 路径。
13. 默认路径合法入参为 `str | TeamAgentSpec`：`TeamAgentSpec` 走 `TeamRuntimeManager.activate`（cold/warm/recover dispatch + 对象池写入）；`str` 是 `team_name`，要求该 team 已被 spec 路径激活、对象池有 entry。已 `build()` 出来的 `TeamAgent` 实例**不再**是合法入参。
14. `member=True` 路径不进对象池、不走 dispatch，是 `inprocess_spawn` / `child_process` 给已构造 teammate / human-agent 用的内部入口。leader-only pool 不变量在此保留。
15. 团队 lifecycle facade（`pause_agent_team` / `stop_agent_team` / `delete_agent_team` / `register_human_agent_inbound` / `get_agent_team_monitor` / `list_active_teams` / `interact_agent_team`）一律按 `(team_name, session_id)` 寻址；这两个键是 SDK 暴露给外部的运行时身份。
16. `Runner.release(session_id, *, force=False)` 对 team session 自动回收动态表（tasks / messages 等）+ checkpoint；非 team session 走单纯 checkpoint 释放。`force` 决定遇到仍在跑的 team 是同步停掉（`True`）还是抛 `AGENT_TEAM_BUSY_INVALID`（`False`）。

## 接口契约

### 顶层装配

唯一公共入口：`TeamAgentSpec(...).build()`。

```python
spec = TeamAgentSpec(
    agents={"leader": DeepAgentSpec(...)},
    team_name="...",
    lifecycle="...",          # "temporary" 或 "persistent"
    teammate_mode="...",      # "build_mode" 或 "plan_mode"
    spawn_mode="...",         # "process" 或 "inprocess"
    leader=LeaderSpec(...),
    predefined_members=[...],
    transport=TransportSpec(...),
    storage=StorageSpec(...),
    worktree=WorktreeConfig(...),
    metadata={...},
    # 其他字段见 schema.blueprint.TeamAgentSpec
)
leader = spec.build()
```

- `agents["leader"]` 必填，缺失时 `build()` 抛 `ValueError("agents dict must contain a 'leader' key")`。
- `agents["teammate"]` 可选，缺失时 teammate 派生自 leader 的模型与默认 persona。
- `spawn_mode="inprocess"` 时 `transport=None` 会被 spec 验证器自动填为 `TransportSpec(type="inprocess")`；`"process"` 不做隐式默认（强制用户显式选 `pyzmq` 等跨进程后端）。
- 新增装配维度直接在 `TeamAgentSpec` 上加字段——package 不暴露任何"参数列表型"工厂函数承接配置。

恢复 / 续接：

- 不存在独立的恢复 API。把同一个 `spec` 与目标 `session_id` 交给 `Runner.run_agent_team[_streaming]`，由 runtime 自动识别 cold/warm/switch（详见 S_06 与 S_04）。
- 低层入口 `TeamAgent.recover_from_session(session, team_name, runtime_spec=spec)` + `await agent.recover_team()` 仅供运维脚本绕开 Runner 直接拿 leader 实例时使用，不是公共 API。

### TeamAgentSpec 核心契约

```python
class TeamAgentSpec(BaseModel):
    agents: dict[str, DeepAgentSpec]
    team_name: str = "agent_team"
    lifecycle: str = TeamLifecycle.TEMPORARY
    teammate_mode: str = "build_mode"
    spawn_mode: str = "process"
    leader: LeaderSpec = LeaderSpec()
    predefined_members: list[TeamMemberSpec] = []
    model_pool: list[ModelPoolEntry] = []
    model_router: Optional[ModelRouterConfig] = None
    model_pool_strategy: Literal["round_robin", "by_model_name", "router"] = "round_robin"
    team_mode: Literal["default", "predefined", "hybrid"] | None = None
    transport: Optional[TransportSpec] = None
    storage: Optional[StorageSpec] = None
    worktree: Optional[WorktreeConfig] = None
    workspace: Optional[TeamWorkspaceConfig] = None
    metadata: dict[str, Any] = {}
    enable_hitt: bool = False
    language: Optional[str] = None
    agent_customizer: Optional[Callable[..., None]] = Field(default=None, exclude=True)
    memory: Optional[TeamMemoryConfig] = None

    def build(self) -> TeamAgent: ...
    def resolve_db_config(self) -> DatabaseConfig: ...
```

校验失败语义：

- `model_pool` 和 `model_router` 同时设置 → `ValueError("model_pool and model_router are mutually exclusive; ...")`。
- `agents` 缺少 `"leader"` key → `ValueError("agents dict must contain a 'leader' key")`。
- `LeaderSpec.member_name` 命中 `RESERVED_MEMBER_NAMES - {DEFAULT_LEADER_MEMBER_NAME}`（即 `user` / `human_agent`） → `ValueError`。
- `predefined_members` 中非 HUMAN_AGENT 角色使用任何保留名 → `ValueError`。
- `enable_hitt=False` 且 `predefined_members` 含 HUMAN_AGENT → `AGENT_TEAM_CONFIG_INVALID`（`StatusCode` = 132004）。
- 配置了 `model_pool` 但无法为 leader 解析出模型 → `AGENT_TEAM_CONFIG_INVALID`，错误消息列出可选的修复路径。
- 未知 `transport.type` / `storage.type` → `ValueError("Unknown transport/storage type ...")`。

`build()` 内部步骤（外部可观察，**契约的一部分**）：

1. 校验 `agents['leader']` 存在；
2. 校验保留名；
3. 校验 HITT 一致性；
4. `resolve_language()` 解析语言并下发到每个未指定 language 的 `DeepAgentSpec`；
5. 若设置了 `model_router`，展开成 `model_pool` 并把策略钉为 `"router"`；
6. 构造 `TeamSpec`、`TransportSpec.build()` → messager_config、`resolve_db_config()` → db_config；
7. 通过 `build_model_allocator` 构造 allocator，预分配 leader 的 endpoint；
8. 构造 `TeamRuntimeContext(role=LEADER, ...)`；
9. 构造 `TeamAgent`、`attach_model_allocator`、`configure(self, context)`；
10. 返回。

### Runner 团队入口

```python
@classmethod
async def run_agent_team(
    cls,
    agent_team: Union[str, TeamAgentSpec, BaseTeam, BaseAgent],
    inputs: Any,
    *,
    base: bool = False,
    member: bool = False,
    session: Optional[Union[str, AgentTeamSession]] = None,
    context: Optional[ModelContext] = None,
    envs: Optional[dict[str, Any]] = None,
) -> Any
```

```python
@classmethod
async def run_agent_team_streaming(
    cls,
    agent_team: Union[str, TeamAgentSpec, BaseTeam, BaseAgent],
    inputs: Any,
    *,
    base: bool = False,
    member: bool = False,
    session: Optional[Union[str, AgentTeamSession]] = None,
    context: Optional[ModelContext] = None,
    stream_modes: Optional[list[BaseStreamMode]] = None,
    envs: Optional[dict[str, Any]] = None,
) -> AsyncIterator[Any]
```

输入路由：

| `base` | `member` | 合法 `agent_team` | 路径 |
|---|---|---|---|
| `False` | `False` | `TeamAgentSpec` | `TeamRuntimeManager.activate` → 对象池写入 → `agent.invoke/stream` |
| `False` | `False` | `str` (team_name) | 命中已激活 pool entry，复用其 spec；首次必须传 spec |
| `True` | `False` | `BaseTeam` 或 `str` (team_id) | `multi_agent.BaseTeam` 路径，team_id 经 `resource_mgr` 解析 |
| `False` | `True` | 已构造 teammate / human-agent `BaseAgent` | spawn-only 入口，绕过 activate / 对象池 |
| 其它组合 | — | — | `AGENT_TEAM_CONFIG_INVALID` |

错误语义：

- 默认路径传入既不是 `str` 也不是 `TeamAgentSpec` 的对象（包括已 build 的 `TeamAgent`） → `AGENT_TEAM_CONFIG_INVALID`。
- 默认路径传入 `str` 但池中无对应 entry → `AGENT_TEAM_CONFIG_INVALID`，提示首次必须传 spec。
- `base=True` 传入既不是 `str` 也不是 `BaseTeam` → `AGENT_TEAM_CONFIG_INVALID`。
- `activate` 返回 `RunActionKind.REJECT_RUNNING` / `REJECT_ORPHANED` / `REJECT_INCONSISTENT` 的 `RunAction` → 非流式返回 `None`、流式静默结束；这两种情况都会 `logger.warning` 记录并按规则关 `InteractGate` + `session.post_run()`。

### 团队 lifecycle facade

```python
@classmethod
async def interact_agent_team(
    cls, payload: Any, *, team_name: Optional[str] = None, session_id: Optional[str] = None,
) -> DeliverResult

@classmethod
async def register_human_agent_inbound(
    cls, *, team_name: str, session_id: str, member_name: str, callback: object,
) -> bool

@classmethod
async def pause_agent_team(
    cls, *, team_name: Optional[str] = None, session_id: Optional[str] = None,
) -> bool

@classmethod
async def stop_agent_team(cls, *, team_name: str, session_id: str) -> bool

@classmethod
async def get_agent_team_monitor(
    cls, *, team_name: str, session_id: str,
) -> Optional[TeamMonitor]

@classmethod
async def list_active_teams(cls) -> list[ActiveTeamInfo]

@classmethod
async def delete_agent_team(
    cls, *, team_name: str, session_ids: list[str], force: bool = False,
) -> bool
```

错误 / 返回语义：

- `interact_agent_team(team_name=None | session_id=None)` → `DeliverResult.failure("missing_target")`，**不**抛异常。
- `interact_agent_team` 接受 `InteractPayload` 子类（`GodViewMessage` / `OperatorMessage` / `HumanAgentMessage`）或裸 `str`；`str` 等价于 `GodViewMessage(body=...)`。
- `register_human_agent_inbound`：无匹配 runtime → 返回 `False`；`member_name` 不是已注册 human-agent → 抛 `KeyError`。`callback=None` 表示清除。
- `pause_agent_team` / `stop_agent_team` / `get_agent_team_monitor`：无匹配 runtime 时分别返回 `False` / `False` / `None`，不抛异常。
- `list_active_teams` 返回只读 `ActiveTeamInfo`（`team_name` / `current_session_id` / `state` / `gate_closed`）；不暴露 `TeamAgent` / `InteractGate` 实例。
- `delete_agent_team(force=False)` 遇活 runtime → 抛 `AGENT_TEAM_BUSY_INVALID`（`StatusCode` = 132005）；`force=True` 在内部先 `stop_team` 再删除。

```python
async def Runner.release(self, session_id: str, *, force: bool = False) -> None
```

- team session：自动清动态表 + checkpoint；非 team session：仅 checkpoint。
- `force` 仅作用于 team session：`False` 遇活 team 抛 `AGENT_TEAM_BUSY_INVALID`，`True` 同步 stop 后再清。

## 数据结构

### TeamAgentSpec 字段生命周期

| 字段 | 设置时机 | 清空时机 | 备注 |
|---|---|---|---|
| `agents` | 构造时 | 不清 | `build()` 后只读消费，不修改 |
| `transport` | 构造时（用户）或 `_default_transport_for_spawn_mode` 验证器（`spawn_mode='inprocess'` 时自动填 `TransportSpec(type="inprocess")`） | 不清 | 写回到 spec 自身——dumped spec 保持自描述 |
| `model_pool` / `model_router` / `model_pool_strategy` | 构造时 | 不清 | `build()` 期间根据 `model_router` 派生 `team_pool` / `team_strategy` 局部变量并写入 `TeamSpec`；**不**修改 `TeamAgentSpec` 自身的这三个字段 |
| `agents[*].language` | 构造时（用户）或 `build()` 中 `resolve_language()` 下发 | 不清 | 用户未指定时由 spec language 下发，已指定的不覆盖；属于 spec 内部一致性补全 |
| `agent_customizer` | 构造时 | 不清 | `Field(exclude=True)`，不进入 `model_dump()`；仅 `inprocess` spawn 可用 |
| `predefined_members` | 构造时 | 不清 | 直接进入 `TeamSpec` 的成员预声明 |

### TeamRuntimeContext 字段生命周期

| 字段 | 设置时机 | 清空时机 | 备注 |
|---|---|---|---|
| `role` | `build()` 构造 leader context 时设为 `LEADER`；teammate context 由 spawn 路径生成时设为 `TEAMMATE` / `HUMAN_AGENT` | 整个生命周期不变 | spec → runtime 的边界 |
| `team_spec` | `build()` 构造 | 不清 | runtime-only 视图，可携带运行时派生字段（如展开后的 pool） |
| `messager_config` | `TransportSpec.build()` 产物 | runtime 销毁 | 跨进程 spawn payload 的一部分 |
| `db_config` | `resolve_db_config()` 产物（sqlite 时补默认 `connection_string`） | runtime 销毁 | spec.storage 是不可变描述；这里是它的物化产物 |
| `member_model` | `model_allocator.allocate(...)` → `to_team_model_config()` 产物 | runtime 销毁 | leader 在 build 时预分配；teammate 在 spawn 时分配 |

### Runner pool 身份

| 标识 | 谁分配 | 作用 |
|---|---|---|
| `team_name` | 用户在 `TeamAgentSpec.team_name` 上声明 | 对象池 key + lifecycle facade 寻址主键 |
| `session_id` | `Runner` 入口接收 `session: str | AgentTeamSession`，未提供则 `create_agent_team_session()` 自动生成 | 对象池 entry 上的 `current_session_id`，HITT inbox 路由键，release 入口键 |
| `member_name` | spec 声明（leader / predefined）或 spawn 时分配 | 团队内成员寻址 + interact `@member` 路由 |

## 与其它 spec 的关系

- 本 spec 描述**入口形态**与 **Spec → build 单向流**；进入 build 后 `TeamRuntimeManager` 内部的 7 路 dispatch、对象池写入、并发门禁、finalize 决策、session 状态机等留给后续 runtime spec。
- `RESERVED_MEMBER_NAMES` 的具体语义、`@member` 路由的解析规则属于 interaction spec，不在本规约展开。
- `TeamAgentSpec` 上的 `model_pool` / `model_router` 字段在本规约只承诺"互斥校验 + build 时由 router 展开为 pool"；具体分配策略（round_robin / by_model_name / router）的语义、`inherit_pool_ids` 的行为属于模型分配 spec。
- `TransportSpec` / `StorageSpec` 的注册表机制是本规约的不变量；**具体内置后端**（inprocess / pyzmq / sqlite / memory）的协议属于 messager / 存储后端的子模块规约。
- 错误码 `AGENT_TEAM_CONFIG_INVALID`（132004）、`AGENT_TEAM_BUSY_INVALID`（132005）的码段归属与命名规则见 `.claude/rules/error-codes.md`，本规约只锚定它们出现的接口契约。
