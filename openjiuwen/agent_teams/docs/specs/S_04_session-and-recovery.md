# Session and Recovery

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/agent/session_manager.py`、`openjiuwen/agent_teams/agent/recovery_manager.py`、`openjiuwen/agent_teams/runtime/metadata.py`、`openjiuwen/agent_teams/context.py` |
| 最近一次修订日期 | 2026-05-12 |
| 关联 feature | `F_01_coordination-protocol-cleanup.md`、`F_05_lifecycle-finalize-relocation.md` |

## 范围 / 边界

本规约定义 `TeamAgent` 在一个 leader 进程内的 **session 绑定状态机** 与 **session
切换 / checkpoint 恢复路径**，并约束 session checkpoint 的写入位置。三件事一起
描述：

1. `SessionManager` 上 `bind_session` / `release_session` / `unbind_session`
   三方法的语义、合法转移、对外副作用（contextvar、DB 表初始化、leader config 落盘）。
2. `RecoveryManager` 在 session 切换 / checkpoint 恢复时的两条恢复路径
   （`resume_for_new_session` 与 `recover_for_existing_session`），以及 leader-only
   的成员重启策略。
3. session checkpoint 中 per-team namespace 的状态分桶约定与读写入口
   （`runtime/metadata.py` 的 `read_team_namespace` / `merge_team_namespace`
   / `write_team_namespace`）。

不在本规约内：

- 协调层（`coordination/`）的事件分发与 round 调度——session 状态机只暴露给 kernel
  调用，路由细节归 `S_03_coordination-protocol`。
- `runtime/manager.py` 的 7 路 dispatch truth table——dispatch 决定要不要
  `recover_for_existing_session` / `resume_for_new_session`，但具体决策见
  `S_06_runtime-pool-dispatch`，不在此重复。
- 多 team 共享 session 的 pool 拓扑——本规约只关心单 team 视角下的 session 字段。
- `core/session/agent_team` 内 `AgentTeamSession` 自身的实现——这是上游 SDK 提供的
  session 容器，本规约只规定调用契约与字段写入位置。

## 不变量

下列条目在 leader 进程的任意时刻必须为真。teammate 进程的 `SessionManager` 只
持镜像的 `session_id`，DB 表创建、config 落盘、成员重启路径均不在 teammate 侧
触发，相关条目对 teammate 自动满足。

### I-1 Session 字段三态

`TeamAgentState` 上有且仅有以下两个字段刻画 session 绑定：

```python
session_id: Optional[str]
team_session: Optional[AgentTeamSession]
```

二者的合法组合只有三种，不存在第四种状态：

| 状态 | `session_id` | `team_session` | 含义 |
|---|---|---|---|
| **未绑（unbound）** | `None` | `None` | 尚未绑定，或被 `unbind_session` 显式清空 |
| **半绑（half-bound）** | `str` | `None` | live session 已被 `release_session` 释放，id 留作 log correlation / resume 复用 |
| **完整（fully-bound）** | `str` | `AgentTeamSession` | round 在跑，可读可写 |

**禁止出现** `session_id is None and team_session is not None`——这是悬挂状态，
意味着 session id 漏写。任何代码路径如果只更新两字段之一，必须同时维持上述
三态约束。

### I-2 Live session 永远从合法 session 来

`team_session` 不为 `None` 时，必须是 `AgentTeamSession` 类型（`bind_session`
内部用 `isinstance(session, AgentTeamSession)` 兜底；非该类型会被强制写 `None`，
回退到半绑而不是携带异类对象）。这条不变量保证下游 `merge_team_namespace`
等读写一律走 `core/session/agent_team` 的接口形状。

### I-3 contextvar 单调收敛

`context.py` 的 `_session_id_context` 在 `bind_session` 时被 `set_session_id`
写到当前协程上下文。`release_session` 与 `unbind_session` 都**不显式
reset** contextvar——故意为之：

- `release_session` 之后 contextvar 仍指向旧 session_id，pause / stop 后续清理
  代码（如 `_persist_team_lifecycle`）仍能通过 `get_session_id` 取到正确 id；
- 下一次 `bind_session` 必然覆盖 contextvar 到新 session_id。

唯一允许 contextvar 取到 `None / ""` 的场景，是从未 `bind_session` 过的纯净进程
（默认值 `None`，`get_session_id` 把 `None` 显式映射为空串）。

### I-4 Leader-only 持久化

`persist_leader_config(session)` **仅在** `role == TeamRole.LEADER` 且
`spec is not None` 且 `team_name is not None` 时落写。teammate 调 `bind_session`
不写 leader config。`bind_session` 已自洽地排除 teammate 路径，调用方不需要
预判角色。

### I-5 Per-session DB 表幂等创建

`bind_session` 在 `team_backend` 存在时调用一次
`team_backend.db.create_cur_session_tables()`。该调用必须幂等：同一 session
重复 `bind_session`（如 stop 后又 resume）不允许产生重复建表副作用。
`SessionManager` 自身不缓存"已建表"标记，幂等性由 backend 实现保证。

### I-6 Checkpoint 写入只走 namespace 入口

session checkpoint 中所有 per-team 状态都写在 `state["teams"][team_name]`
namespace 下：

```
state["teams"][team_name] = {
    "spec":                  TeamAgentSpec.model_dump(mode="json"),
    "context":               TeamRuntimeContext.model_dump(mode="json"),
    "model_allocator_state": Optional[dict],
    "lifecycle":             Optional["running" | "paused"],
}
```

任何代码改写这个 bucket，必须经 `runtime/metadata.py` 的
`read_team_namespace` / `merge_team_namespace` / `write_team_namespace`
/ `remove_team_namespace`。直接 `session.update_state({"spec": ...})` 之类的
"在 root 上 update" 是非法操作——`session.update_state` 在顶层做的是浅合并，
绕过 namespace 入口会污染其它 team 的 bucket。

### I-7 协调层调用契约

`CoordinationKernel` 是 `SessionManager` 在 round 边界的唯一调用方，调用
拓扑固定为：

| kernel 阶段 | session 入参 | 调用 |
|---|---|---|
| `start(session=session)` | 非 None | `await sess_mgr.bind_session(session)` |
| `start(session=None)`    | None    | `sess_mgr.unbind_session()` |
| `pause(...)`             | 不接 session | `sess_mgr.release_session()` |
| `stop()`                 | 不接 session | `sess_mgr.release_session()` |

`bind_session(None)` 不再合法（详见 I-1 与 § 接口契约）。状态转移写在调用面，
让被调方做 None 分支是把决策藏起来——已被显式拒绝。

### I-8 恢复路径的清理对偶

session 切换有两条入口，差异**只在是否先清理旧 handle**：

- `resume_for_new_session`：来自 `manager.activate` 的 `NEW_TEAM_IN_SESSION` 分支
  （`_apply_action` 先 `spec.build()` 出新 `TeamAgent`，再调
  `agent.resume_for_new_session(session)`）。新 agent 上 spawn_manager 是空的，本
  路径以 `cleanup_first=True` 走 cleanup 不会找到任何活 handle，对单次 build 是
  no-op；保留 `True` 是为了 cold-recover 之外、由调用方手动 `resume_for_new_session`
  的低阶用法仍有清理保障。`RESUME_FROM_PAUSE` 不调用此方法——pause 后 pool 里同
  实例继续跑，没有 session 切换。
- `recover_for_existing_session`：来自 `COLD_RECOVER` 分支
  （`TeamAgent.recover_from_session(...)` 之后 `agent.recover_team()`）。recover_team
  自身在 leader 路径里会重新拉起 teammate，因此调用方此前已经把旧 spawn handle
  清空（或干脆是新 build 的 agent），本路径以 `cleanup_first=False` 跳过重复清理。

两条入口在 leader-only 路径下都收敛到 `RecoveryManager.restart_for_session_switch`，
区别只剩 `cleanup_first`。teammate / 非 leader 路径直接早返回。

**`WARM_RECOVER` / `NEW_TEAM_IN_SESSION_WARM` 两条入口已下线**（见 F_05）：跨 session
切换不再复用 pool 里的同一个 `TeamAgent` 实例，由 `manager.activate` 在 dispatch
前 `stop_team` 拆掉 stale entry，下一轮走 cold-build 路径重新构造。这消除了"in-memory
状态（contextvars / 客户端缓存）跨 session 漂移"的边界条件。

## 接口契约

### `openjiuwen.agent_teams.agent.session_manager.SessionManager`

```python
class SessionManager:
    @property
    def session_id(self) -> Optional[str]: ...

    @session_id.setter
    def session_id(self, value: Optional[str]) -> None: ...

    @property
    def team_session(self) -> Optional[AgentTeamSession]: ...

    @team_session.setter
    def team_session(self, value: Optional[AgentTeamSession]) -> None: ...

    async def bind_session(self, session: AgentTeamSession) -> None:
        """完整绑定。session 形参类型 ``AgentTeamSession``，**不接受 None**。"""

    def release_session(self) -> None:
        """完整 → 半绑。release live ``team_session``，保留 ``session_id``。"""

    def unbind_session(self) -> None:
        """* → 未绑。同时清 ``session_id`` 和 ``team_session``。"""

    async def resume_for_new_session(self, session: AgentTeamSession) -> None:
        """切到新 session：内部以 ``cleanup_first=True`` 重启 live teammate。"""

    async def recover_for_existing_session(self, session: AgentTeamSession) -> None:
        """恢复 checkpoint 的 session：以 ``cleanup_first=False`` 重启 live teammate。"""
```

接口语义细节：

- **`bind_session(session)`**
  1. `state.session_id = session.get_session_id()`
  2. `set_session_id(state.session_id)` 写当前协程 contextvar
  3. `state.team_session = session if isinstance(session, AgentTeamSession) else None`
     （I-2）
  4. `team_backend` 存在 → `await team_backend.db.create_cur_session_tables()`
  5. role==LEADER 且 spec 存在 → `recovery_manager.persist_leader_config(session)`
  传 None 是调用方 bug；语义不再"None 表示解绑"，那条路径走 `unbind_session`。

- **`release_session()`**
  仅置 `state.team_session = None`，**不动 session_id、不 reset contextvar**。
  允许在已经是半绑或未绑态的对象上重复调用（幂等：再调一次仍是相同状态）。

- **`unbind_session()`**
  同时把 `state.session_id` 与 `state.team_session` 置 `None`。**不显式
  reset contextvar**——下一次 `bind_session` 必然覆盖；当前协程在 unbind 后到
  下次 bind 前若调 `get_session_id`，仍取到旧值，刻意保留以便清理路径用旧 id
  做最后的写入。允许在未绑态上重复调用。

- **`resume_for_new_session(session)`**
  伪代码：
  ```python
  recoverable = await recovery_manager.collect_live_teammates_for_session_switch()
  await self.bind_session(session)
  if role != LEADER or not team_backend:
      return
  await recovery_manager.restart_for_session_switch(recoverable, cleanup_first=True)
  ```
  顺序固定：先快照 → 再 bind → 再重启。`bind_session` 早于重启意味着新 spawn 的
  teammate 进程拿到的是新 session_id（通过 spawn payload 与 messager topic）。

- **`recover_for_existing_session(session)`**
  与 `resume_for_new_session` 同结构，只把 `cleanup_first=True` 改为 `False`。
  调用方契约：必须在调用前已经 tear down 旧 handle（典型路径是
  `manager._apply_action` 在 cold/warm recover 前重新 build 了 agent，
  spawn_handles 自然为空）。

### `openjiuwen.agent_teams.agent.recovery_manager.RecoveryManager`

```python
class RecoveryManager:
    async def recover_team(self) -> list[str]:
        """leader-only：从 DB 拉所有非自身成员，逐个置 RESTARTING 后重启。"""

    def persist_leader_config(self, session) -> None:
        """leader-only：把 spec / context / allocator state 写到 per-team namespace。"""

    async def collect_live_teammates_for_session_switch(
        self,
    ) -> list[tuple[str, MemberStatus]]:
        """leader-only：快照 (member_name, current_status)，过滤 UNSTARTED/SHUTDOWN。"""

    async def restart_for_session_switch(
        self,
        recoverable_members: list[tuple[str, MemberStatus]],
        *,
        cleanup_first: bool,
    ) -> None:
        """逐个：可选 cleanup → 状态归一到 RESTARTING → 重新 spawn。"""

    def persist_allocator_state(self, team_session) -> None:
        """leader-only：把 allocator 的 state_dict 增量 merge 进 namespace。"""
```

关键语义：

- **`collect_live_teammates_for_session_switch`** 只对 `role == LEADER` 且
  `team_backend` 存在的情况下返回非空。它的判定条件是
  "DB 里 status 不在 `{UNSTARTED, SHUTDOWN, STOPPED}`" **且** "spawn_manager 上仍持有
  非 None handle"——两个条件都满足才认为该 teammate 需要在新 session 下重新拉起。
  `STOPPED` 被加入过滤集合（与 `UNSTARTED` / `SHUTDOWN` 同列）是因为它表示
  "runtime 已被外部 stop_team 拆掉"，没有 live handle 可恢复；spawn 过滤已经能挡
  大部分情况，状态过滤是为了 stale handle 仍在注册时也不漏到 live 列表里。
- **`restart_for_session_switch`** 内部对每个成员的处理：
  1. 若 `cleanup_first` → `await spawn_manager.cleanup_teammate(member_name)`
  2. `_mark_teammate_restarting_for_session_switch`：READY/BUSY/UNSTARTED/SHUTDOWN_REQUESTED
     不能直跳 RESTARTING，先归一到 ERROR 再到 RESTARTING；PAUSED/STOPPED/ERROR/SHUTDOWN
     是"directly restartable"，按 `MEMBER_TRANSITIONS` 可直接到 RESTARTING；任何
     DB 更新失败 → 跳过该成员、不抛
  3. `await spawn_manager.restart_teammate(member_name)`
- **`persist_leader_config`** 是**全量覆盖** `spec` + `context`
  （+ optional `model_allocator_state`）通过 `write_team_namespace`，这是
  bind 路径上唯一调用点；后续 round 内的增量更新走 `persist_allocator_state`
  的 `merge_team_namespace`。

### `openjiuwen.agent_teams.runtime.metadata`

```python
TEAMS_KEY = "teams"

def read_teams_bucket(session) -> dict[str, dict[str, Any]]: ...
def read_team_namespace(session, team_name: str) -> dict[str, Any] | None: ...
def read_team_names_in_session(session) -> list[str]: ...
def write_team_namespace(session, team_name: str, payload: dict[str, Any]) -> None: ...
def merge_team_namespace(session, team_name: str, partial: dict[str, Any]) -> None: ...
def remove_team_namespace(session, team_name: str) -> bool: ...
```

写语义统一：先 `read_teams_bucket` 取当前 map，本地 mutate 后整张 map 一起写
回 `session.update_state({TEAMS_KEY: teams})`。这是因为 `update_state` 在 root
做的是浅合并——如果只回写 `teams[team_name]`，会把别的 team bucket 抹掉。
**该模块是 namespace 写入的唯一合法入口**，I-6 不变量靠它收敛。

### `openjiuwen.agent_teams.context`

```python
def set_session_id(session_id: str) -> Token[str]: ...
def get_session_id() -> Optional[str]: ...   # None 自动返回空串
def reset_session_id(token: Token[str]) -> None: ...
```

`SessionManager` 只调 `set_session_id`，不调 `reset_session_id`——后者是 spawn
/ message 路径的局部上下文管理工具，不参与本状态机。

## 数据结构

### 三态状态机

```
                       ┌─────────────────────┐
                       │   未绑 (unbound)    │
                       │  id=None, ts=None   │
                       └──────────┬──────────┘
                                  │
                  bind_session(s) │   unbind_session()  (idempotent)
                                  │ ◄────────────────────┐
                                  ▼                      │
                       ┌─────────────────────┐           │
                       │  完整 (fully-bound) │───────────┘
                       │  id=X, ts=session   │
                       └──────────┬──────────┘
                                  │
                  release_session │  bind_session(s')   (覆盖式)
                                  ▼
                       ┌─────────────────────┐
                       │   半绑 (half-bound) │───────────┐
                       │  id=X, ts=None      │           │
                       └──────────┬──────────┘           │
                                  │                      │
                  bind_session(s')│  unbind_session()    │
                                  └──────────────────────┘
```

合法转移枚举（其它转移视为 bug）：

| from \ via | bind | release | unbind |
|---|---|---|---|
| 未绑 | → 完整 | → 未绑（noop） | → 未绑（noop） |
| 完整 | → 完整（覆盖 id/ts） | → 半绑 | → 未绑 |
| 半绑 | → 完整（覆盖 id/ts） | → 半绑（noop） | → 未绑 |

`release_session` 在未绑 / 半绑态都是 noop——与 `unbind_session` 的幂等语义对齐，
调用方不需要先判状态。

### 恢复路径

```
                ┌─────────────────────────────────────────┐
                │   resume_for_new_session(session)       │
                │   入口：dispatch NEW_TEAM_IN_SESSION    │
                │        (spec.build() 后立即调用)         │
                │   ─────────────────────────────────     │
                │   collect_live_teammates_for_         │
                │       session_switch()                  │
                │   bind_session(session)                 │
                │   restart_for_session_switch(           │
                │       cleanup_first=True)               │
                └────────────────┬────────────────────────┘
                                 │
                                 │ leader-only 才进重启分支
                                 ▼
                ┌─────────────────────────────────────────┐
                │   restart_for_session_switch            │
                │   ─────────────────────────────────     │
                │   for (name, status) in recoverable:    │
                │     [cleanup_first] cleanup_teammate    │
                │     mark RESTARTING                     │
                │       (READY/BUSY/UNSTARTED/SHUTDOWN_   │
                │        REQUESTED 经 ERROR 归一；        │
                │        PAUSED/STOPPED/ERROR/SHUTDOWN    │
                │        directly restartable)            │
                │     restart_teammate                    │
                └─────────────────────────────────────────┘
                                 ▲
                                 │
                ┌────────────────┴────────────────────────┐
                │   recover_for_existing_session(session) │
                │   入口：dispatch COLD_RECOVER           │
                │        (TeamAgent.recover_from_session  │
                │         + recover_team)                  │
                │   ─────────────────────────────────     │
                │   collect_live_teammates_for_         │
                │       session_switch()                  │
                │   bind_session(session)                 │
                │   restart_for_session_switch(           │
                │       cleanup_first=False)              │
                └─────────────────────────────────────────┘
```

两条路径**完全同骨架**，差异只有最后一步 `cleanup_first`，对应 I-8。

### Checkpoint 状态分桶

```
session.state
│
└── teams                                              ← TEAMS_KEY="teams"
    ├── <team_name_A>
    │   ├── spec                  TeamAgentSpec.model_dump(mode="json")
    │   ├── context               TeamRuntimeContext.model_dump(mode="json")
    │   ├── model_allocator_state Optional[dict]      ← persist_allocator_state
    │   └── lifecycle             Optional["running" | "paused"]
    │                                                  ← coordination 写入
    └── <team_name_B>
        └── ...
```

写入入口与字段责任：

| 字段 | 写入函数 | 调用时机 |
|---|---|---|
| `spec` | `recovery_manager.persist_leader_config` → `write_team_namespace` | `bind_session` 末尾（leader） |
| `context` | 同上 | 同上 |
| `model_allocator_state`（初始）| 同上 | 同上 |
| `model_allocator_state`（增量）| `recovery_manager.persist_allocator_state` → `merge_team_namespace` | round 中模型分配变更后 |
| `lifecycle` | `coordination/kernel._persist_team_lifecycle` → `merge_team_namespace` | pause / resume 切换时 |

新增字段时，按"是否在 bind 时一次性给定"决定走 `write_team_namespace`（覆盖）
还是 `merge_team_namespace`（增量）。**新增字段名进 namespace 之前**，先在
`runtime/metadata.py` 的模块 docstring 上加一行说明——bucket 结构是公共契约
（多 team 共住一份 session，跨成员读写都靠它），靠 grep dict key 维护契约会
腐化。

## 与其它 spec 的关系

- **F_01_coordination-protocol-cleanup.md**：本规约直接来自 F_01 的决策落地。
  F_01 把"`bind_session(None)` 兼任解绑"这个特殊情况砍掉，拆出本规约描述的
  三方法。本 spec 是 F_01 的当前态快照。
- **`S_06_runtime-pool-dispatch`**：`manager.activate` 的 7 路 truth
  table 决定 `_apply_action` 走 `recover_for_existing_session`（`COLD_RECOVER`，
  内部用 `recover_from_session` + `recover_team`）还是 `resume_for_new_session`
  （`NEW_TEAM_IN_SESSION`），是 I-8 的上游决策层；本 spec 只描述被调用面。原
  `WARM_RECOVER` / `NEW_TEAM_IN_SESSION_WARM` 已在 F_05 中废止，跨 session 切换
  改走 `activate` 的 stop+rebuild 路径，详见 S_06。
- **`S_02_team-agent-architecture` 四象限**：`session_id` / `team_session` 是
  `TeamAgentState` 的字段，遵守"跨 operator 才放 state"原则；spec / ctx /
  allocator 在 `AgentConfigurator` / `models/` 各自归宿，本规约只规定它们
  dump 后落到哪个 namespace 字段。
- **schema/team.py**：`TeamLifecycle`（temporary / persistent）描述静态团队
  类型，与 namespace 中的 `lifecycle: "running" | "paused"` 是**不同枚举**
  ——后者是 round 运行时状态，由 coordination 写入。混用会得到错误的恢复
  策略，命名相近务必看清来源。
