# Runtime Pool & Dispatch

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/runtime/` |
| 最近一次修订 commit | 18823271 |
| 关联 feature | — |

## 范围 / 边界

### 管的事

- 进程内活跃 `TeamAgent` 实例的容器（`TeamRuntimePool`）：池条目结构、键空间、生命周期。
- `Runner.run_agent_team*` 公共入口（`base=False, member=False`）触达此层时的派发决策（9 路 truth table），输入是 `(spec, session, pool, checkpoint)` 四元组的快照，输出是 `RunAction`。
- 同一 team 同一 cycle 内 run 与 interact 的并发门禁（`InteractGate`）。
- session checkpoint 中 `state["teams"][team_name]` 命名空间的读写桥（`metadata.py`），把 dispatch 决策需要的"team 是否在 session 里"问题翻译成 checkpoint 查询。
- `Runner` 暴露给 SDK / CLI 的 lifecycle facade 在本层的实现：`activate / pause / interact / register_human_agent_inbound / stop_team / get_monitor / list_active_teams / delete_team / release_session`。

### 不管的事

- TeamAgent 内部的协程编排、消息总线、coordinator 调度——属于 `agent/` 与 `messager/`。
- session 状态的写入语义本身（`spec` / `context` / `model_allocator_state` / `lifecycle` 字段如何序列化、如何 round-trip）——归 S_04。本 spec 只声明 dispatch 读这条桥。
- 通过 `member=True` 入口启动的 teammate / human-agent 子进程实例。它们既不入 pool 也不走 dispatch，只复用 `Runner.run_agent_team*` 的代码路径而已。
- `base=True` 走 `multi_agent` 体系的 `BaseTeam`：facade 直接转 instance method `_RunnerImpl._run_base_team*`，与本 spec 的 pool / manager 完全无关。
- 跨进程 / 跨节点的 team 状态共享：本 spec 的 pool 是**进程内**对象，重启进程即丢；跨进程持久化由 session checkpoint + DB 承担。

## 不变量

每条都可以用观察 pool / manager 的 API 直接验证。

1. **pool key 唯一**：`TeamRuntimePool._teams` 以 `team_name` 为唯一键，同一 `team_name` 在内存中至多对应一个 `ActiveTeam` 实例。多 session 共用同一 team_name 不会产生两份 entry，切 session 走 `WARM_RECOVER` 原地改写 `current_session_id`。
2. **pool 只持 leader**：`base=False, member=False` 的 spec 路径是 pool 写入的**唯一**入口（`manager.activate` → `_apply_action` → `_pool.add`）。`member=True` 的 spawn 路径与 `base=True` 的 BaseTeam 路径都不写 pool。已删除的 `register_instance` 之类"已 build 实例 → pool"快捷入口不会回归。
3. **派发决策无副作用**：`decide_run_action` 是纯函数，只读入参，不动 factory / pool / session / DB。任何副作用必须发生在 `_apply_action`。
4. **派发决策完备**：truth table 9 个分支覆盖 `(team_in_db, team_in_session, pool_entry?, same_session?)` 在合法约束下的所有组合，无 `else: raise UnhandledCase`。具体见下文表。
5. **REJECT 不入 pool / 不动 pool**：三类 REJECT 返回 `TeamRuntimeActivation` 时不增删 pool entry，已存在的 entry 状态不变（只读快照）。
6. **session 一致性**：`_resolve_entry(team_name, session_id)` 命中要求 `entry.current_session_id == session_id`。session 不一致直接返回 `None`，相当于"该 team 当前不在你以为的 session 上"。
7. **静止前置（busy precondition）**：
   - `delete_team(force=False)`：pool 里有 entry 直接报 `AGENT_TEAM_BUSY_INVALID`。
   - `release_session(force=False)`：该 session 上有任何 entry 直接报 `AGENT_TEAM_BUSY_INVALID`。
   - `force=True` 等价于"先 stop_team 再做"，没有第三种语义。
8. **gate 与 run cycle 对齐**：每个 `ActiveTeam` 自带一个 `InteractGate`；`run_agent_team*` 退出 `finally` 调 `_close_team_interact_gate`（manager 暴露 pool 让 Runner 直接拿 gate 调 `close_and_drain`）；warm 类 action（`WARM_RECOVER` / `RESUME_FROM_PAUSE` / `NEW_TEAM_IN_SESSION_WARM`）在 `_apply_action` 中调 `gate.reset()` 让下个 cycle 重新放行。
9. **gate 状态机单调**：`InteractGate` 在一个 cycle 内只能 `OPEN → CLOSING → DRAINED`；`reset()` 只在新 cycle 开始时调用，调用前必须确保上一 cycle 的 ticket 不再被持有。
10. **interact 必经 gate**：`manager.interact` 始终先 `admit` 拿 ticket，后 `consume_done` 释放；ticket 与 gate 的引用绑定，跨 gate 的 ticket 静默忽略，避免误释放。
11. **db_config 单一来源**：`release_session` / `delete_team` 走 `resolve_team_session_release_info` 从 session checkpoint 的任一 team bucket 解析出 `db_config`；不依赖外部传入，避免调用方传错 DB。
12. **lazy import**：`Runner._get_team_runtime_manager` 通过函数内 import 拉取 `TeamRuntimeManager`，避免子进程 bootstrap（`spawn` 路径）拉链 agent_teams 模块树。

## 接口契约

### `RuntimeState`（pool.py）

```python
class RuntimeState(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
```

ActiveTeam 在 pool 中的运行时状态。与 `schema.team.TeamLifecycle`（`temporary` / `persistent`，描述静态团队类型）正交：一个 `persistent` 团队的 entry 完全可以处于 `PAUSED`。

### `ActiveTeam`（pool.py）

```python
@dataclass(slots=True)
class ActiveTeam:
    team_name: str
    agent: TeamAgent
    current_session_id: str
    state: RuntimeState = RuntimeState.RUNNING
    interact_gate: InteractGate = field(default_factory=InteractGate)
```

可变 dataclass，pool 内部使用。`agent` 是 leader 的 `TeamAgent` 实例（不持 teammate）。

### `ActiveTeamInfo`（pool.py）

```python
@dataclass(frozen=True, slots=True)
class ActiveTeamInfo:
    team_name: str
    current_session_id: str
    state: RuntimeState
    gate_closed: bool
```

`list_all_info` 返回的只读快照。**不含** `agent` / `interact_gate` 引用——SDK / CLI 拿到 info 后无法绕过 manager 直接动 runtime state。

### `TeamRuntimePool`（pool.py）

| 方法 | 语义 |
|---|---|
| `get(team_name) -> ActiveTeam \| None` | 取条目，存在性检查与读取的统一入口 |
| `has_active(team_name) -> bool` | 仅判断存在 |
| `add(entry) -> None` | 写入；同名 entry 直接覆盖（不报错，但实际只在 cold 路径调用，见不变量 1） |
| `remove(team_name) -> ActiveTeam \| None` | 删除并返回旧条目；不存在返回 `None` |
| `list_team_names() -> list[str]` | 名字快照 |
| `teams_for_session(session_id) -> list[ActiveTeam]` | 反查同 session 的所有 entry（多 team 共享 session 用） |
| `list_all_info() -> list[ActiveTeamInfo]` | 全量只读快照 |

所有方法都串行化在 `asyncio.Lock` 上。

### `RunActionKind` / `RunAction`（dispatch.py）

```python
class RunActionKind(str, Enum):
    CREATE                    = "create"
    NEW_TEAM_IN_SESSION       = "new_team_in_session"
    NEW_TEAM_IN_SESSION_WARM  = "new_team_in_session_warm"
    COLD_RECOVER              = "cold_recover"
    WARM_RECOVER              = "warm_recover"
    RESUME_FROM_PAUSE         = "resume_from_pause"
    REJECT_RUNNING            = "reject_running"
    REJECT_ORPHANED           = "reject_orphaned"
    REJECT_INCONSISTENT       = "reject_inconsistent"

@dataclass(frozen=True, slots=True)
class RunAction:
    kind: RunActionKind
    require_spec: bool
    reason: Optional[str] = None
```

`require_spec=True` 仅 `CREATE`：调用方此时必须能拿到 `TeamAgentSpec`，否则上层应直接拒绝（首次必须传 spec 而非 team_name shorthand）。

### `decide_run_action(...)`（dispatch.py）

```python
def decide_run_action(
    *,
    team_in_db: bool,
    team_in_session: bool,
    pool_entry: ActiveTeam | None,
    target_session_id: str,
    target_team_name: str,
) -> RunAction
```

纯函数，无副作用。输入是 manager 已经从 DB / session checkpoint / pool 收集好的快照，输出是 9 路决策中的一条。

### 9 路 dispatch truth table

输入维度：

- `team_in_db`：静态 team 表里有这条 row 吗（`db.team.team_exists`）。
- `team_in_session`：当前 session 的 checkpoint `state["teams"][team_name]` 这个 bucket 存在吗（`read_team_namespace`）。
- `pool_entry`：内存 pool 里有这个 team 的 ActiveTeam 实例吗。
- `same_session`：仅当 `pool_entry` 非 None 时有意义——`entry.current_session_id == target_session_id` 吗。
- `pool_state`：仅当 `pool_entry` 非 None 时有意义——`entry.state` 是 RUNNING 还是 PAUSED。

输出维度：`RunActionKind` + `_apply_action` 实际执行的副作用。

| # | team_in_db | team_in_session | pool_entry | same_session | pool_state | RunActionKind | `_apply_action` 副作用 |
|---|---|---|---|---|---|---|---|
| 1 | F | F | None | — | — | `CREATE` | `pre_run(inputs)` → `spec.build()` → 新 entry 入 pool |
| 2 | F | F | present | — | — | `REJECT_INCONSISTENT` | 不动 pool；返回 `reason` |
| 3 | F | T | * | — | — | `REJECT_ORPHANED` | 不动 pool；返回 `reason` |
| 4 | T | F | None | — | — | `NEW_TEAM_IN_SESSION` | `pre_run(inputs)` → `spec.build()` → `agent.resume_for_new_session(session)` → 入 pool |
| 5 | T | T | None | — | — | `COLD_RECOVER` | `recover_agent_team(session, team_name=...)` → 入 pool |
| 6 | T | T | present | yes | RUNNING | `REJECT_RUNNING` | 不动 pool；引导 caller 改用 `interact` |
| 7 | T | T | present | yes | PAUSED | `RESUME_FROM_PAUSE` | `pre_run(inputs)` → `entry.state = RUNNING` → `gate.reset()` |
| 8 | T | T | present | no  | * | `WARM_RECOVER` | `recover_for_existing_session(entry.agent, session)` → 改写 `current_session_id` → `state = RUNNING` → `gate.reset()` |
| 9 | T | F | present | * | * | `NEW_TEAM_IN_SESSION_WARM` | `pre_run(inputs)` → `resume_persistent_team(entry.agent, session)` → 改写 `current_session_id` → `state = RUNNING` → `gate.reset()` |

注：

- `same_session` 与 `pool_state` 在 `pool_entry is None` 时不参与判定（标 `—`）；标 `*` 表示该维度不影响判定。
- 行 2 是"内存说有，DB 说无"——本应不可能出现，一旦出现说明状态机已腐化，直接 reject 拒绝继续，不要试图自愈。
- 行 9 是"DB 有 row，但 session checkpoint 没 bucket，pool 又有 entry"——典型场景：persistent 团队在新 session 中 resume，且 leader 实例已被前一个 session 留在 pool 里。
- 行 6 的语义不是"出错"，而是"这个 team 在这个 session 上正常运行中——别再 run 了，去 interact"。

### `TeamRuntimeManager` 公共方法

```python
class TeamRuntimeManager:
    @property
    def pool(self) -> TeamRuntimePool: ...

    async def activate(
        self,
        spec: TeamAgentSpec,
        session: str | AgentTeamSession | None,
        inputs: object = None,
    ) -> TeamRuntimeActivation: ...

    async def pause(self, *, team_name: str, session_id: str) -> bool: ...

    async def interact(
        self,
        payload: InteractPayload | str,
        *,
        team_name: str,
        session_id: str,
    ) -> DeliverResult: ...

    async def register_human_agent_inbound(
        self,
        *,
        team_name: str,
        session_id: str,
        member_name: str,
        callback: object,
    ) -> bool: ...

    async def stop_team(self, *, team_name: str, session_id: str) -> bool: ...

    async def get_monitor(
        self, *, team_name: str, session_id: str,
    ) -> TeamMonitor | None: ...

    async def list_active_teams(self) -> list[ActiveTeamInfo]: ...

    async def delete_team(
        self,
        team_name: str,
        session_ids: list[str],
        *,
        force: bool = False,
    ) -> bool: ...

    async def release_session(
        self, session_id: str, *, force: bool = False,
    ) -> None: ...

    @staticmethod
    async def resolve_team_session_release_info(
        session_id: str,
    ) -> TeamSessionReleaseInfo | None: ...
```

错误语义：

- `activate` 返回 `TeamRuntimeActivation`；REJECT 类 action 不抛异常，由调用方据 `action.kind` 决定是否对外报错。
- `pause` / `stop_team` / `register_human_agent_inbound` / `interact`：未命中 `(team_name, session_id)` 的 entry → 返回 `False` / `DeliverResult.failure("not_active")`，不抛。
- `interact` 在 gate closed 时返回 `DeliverResult.failure("gate_closed")`。
- `delete_team(force=False)` / `release_session(force=False)` 在 pool 仍有相关 entry 时 `raise_error(StatusCode.AGENT_TEAM_BUSY_INVALID, ...)`；`force=True` 在内部调 `stop_team` 后续做。
- `resolve_team_session_release_info` 找不到任何 team bucket 时返回 `None`；找到 bucket 但 `db_config` 解析全部失败时 `raise RuntimeError`（这种状态属于 checkpoint 损坏，不是业务错）。

### `InteractGate`（gate.py）

```python
class InteractGate:
    @property
    def closed(self) -> bool: ...
    @property
    def inflight(self) -> int: ...

    async def admit(self) -> AdmissionTicket | None: ...
    async def consume_done(self, ticket: AdmissionTicket) -> None: ...
    async def close_and_drain(self) -> None: ...
    async def reset(self) -> None: ...

@dataclass(frozen=True, slots=True)
class AdmissionTicket:
    gate: InteractGate
```

状态转换：

```text
OPEN    --admit()------>      OPEN, inflight++
OPEN    --close_and_drain()-> CLOSING --(inflight==0)--> DRAINED
CLOSING --admit()------>      None  (rejected)
*       --consume_done(t)-->  inflight--; signal drained when zero
*       --reset()------>      OPEN, inflight=0  (only between cycles)
```

约束：

- 单 event-loop 拥有，所有方法 `await` 同一把 `_lock`。
- ticket 与 gate 实例绑定；跨 gate 的 ticket 在 `consume_done` 中静默忽略（防误释放）。
- `reset()` 仅在 warm 类 action 重新放行下一 cycle 时调用；调用方有责任不让上一 cycle 的 ticket 跨 cycle 存活。

### `metadata.py`：session checkpoint 桥

```python
TEAMS_KEY = "teams"

def read_teams_bucket(session) -> dict[str, dict[str, Any]]: ...
def read_team_namespace(session, team_name: str) -> dict[str, Any] | None: ...
def read_team_names_in_session(session) -> list[str]: ...
def write_team_namespace(session, team_name: str, payload: dict[str, Any]) -> None: ...
def merge_team_namespace(session, team_name: str, partial: dict[str, Any]) -> None: ...
def remove_team_namespace(session, team_name: str) -> bool: ...
```

行为契约（实现细节归 S_04）：

- 以 `state["teams"][team_name]` 为命名空间分桶，bucket 内部字段：`spec` / `context` / `model_allocator_state` / `lifecycle`。
- `session.update_state` 是顶层浅 merge，所以本模块的写入语义统一是"读全 teams 字典 → 改 → 整张写回"，避免漏掉同 session 其他 team 的 bucket。
- `read_team_namespace` 是 dispatch 决策表中 `team_in_session` 维度的唯一来源——dispatch 不直接读 `state` dict。

## 数据结构

### pool entry 字段生命周期

| 字段 | 写入时机 | 读取时机 | 重写时机 |
|---|---|---|---|
| `team_name` | entry 构造（不变量 1：唯一） | 全部命中查找 | 永不 |
| `agent` | cold 类 action 构造（CREATE / NEW_TEAM_IN_SESSION / COLD_RECOVER） | interact / pause / stop / monitor / register_human_agent_inbound | 永不（warm 复用） |
| `current_session_id` | cold 类 action 构造 | `_resolve_entry` 一致性校验、`teams_for_session` | warm 类 action（WARM_RECOVER / NEW_TEAM_IN_SESSION_WARM） |
| `state` | 默认 `RUNNING` | dispatch 决策（行 6/7）、`list_all_info` 快照 | `pause()` → PAUSED；warm 类 action / `RESUME_FROM_PAUSE` → RUNNING |
| `interact_gate` | 默认构造（每 entry 独立 gate） | `interact` 的 admit/consume、`_close_team_interact_gate` | warm 类 action / `RESUME_FROM_PAUSE` 调 `gate.reset()`（不替换实例） |

`stop_team` / `delete_team(force=True)` 通过 `_pool.remove(team_name)` 整体丢弃 entry；不存在"清字段不删 entry"的中间态。

### TeamRuntimeManager 的状态拓扑

```text
                       Runner.run_agent_team*(member=False, base=False)
                                         |
                               manager.activate(spec, session, inputs)
                                         |
                               _build_session  ──────┐
                                         |          │
                               _inspect_session ─────┤  (DB + checkpoint)
                                         |          │
                               decide_run_action  ◄──┘  (pure)
                                         |
                                   _apply_action
                          ┌──────────────┼──────────────┐
                          v              v              v
                       cold 类         warm 类         REJECT 类
                  (3)  CREATE      (3)  WARM_RECOVER     不动 pool
                       NEW_TEAM..       NEW_TEAM..WARM
                       COLD_RECOVER     RESUME_FROM_PAUSE
                          |              |
                       pool.add        改写 current_session_id
                                       state = RUNNING
                                       gate.reset()
```

manager 自身**没有** `_active_*` 之类的并行镜像状态：pool 是 "哪些 team 当前活跃" 的唯一真相。manager 上唯一持有的运行时状态就是 `self._pool: TeamRuntimePool`。

## 与其它 spec 的关系

- **S_01 lifecycle / facade 路由**：本 spec 是 `Runner.run_agent_team*` / `interact_agent_team` / `pause_agent_team` / `stop_team` / `release_session` / `delete_team` 等 SDK 入口在 agent_teams 路径上的实现层。`base=True` BaseTeam 路径与 `member=True` spawn 路径都不经过本层；S_01 负责描述 facade 的 `base` / `member` 分流规则，本 spec 只承诺 `base=False, member=False` 落到此处。
- **S_04 session checkpoint 状态机**：本 spec 通过 `metadata.py` 读写 `state["teams"][team_name]` bucket，但 bucket 内字段（`spec` / `context` / `model_allocator_state` / `lifecycle`）的语义、序列化格式、版本兼容由 S_04 拥有。本层只承诺"团队是否在 session 中"这个布尔值的读路径。
- **S_03 spec → build 装配蓝图**：dispatch 决策中的 `CREATE` / `NEW_TEAM_IN_SESSION` 两条路径调用 `spec.build()`；`NEW_TEAM_IN_SESSION` 之后还会 `agent.resume_for_new_session(session)`。`spec.build()` 的语义、`resolve_db_config()` 的解析归 S_03。
- **S_05 interaction 三视角 inbox**：`manager.interact` 把 `InteractPayload` 派发到 `UserInbox` / `HumanAgentInbox`；payload 类型语义、`@`-mention 路由规则、`parse_interact_str` 前缀约定（`#` / `$<name>` / `@<member>`）由 S_05 拥有。本层只承诺"通过 gate ticket 安全地把 payload 投递给 backend"。
- **S_07（如果存在）spawn / coordination**：`member=True` 的子进程 / 协程启动路径在 spawn spec 中描述。本 spec 不约束子成员实例的生命周期，只声明它们不入 pool、不参与 dispatch。
