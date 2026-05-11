# Coordination Protocol Cleanup

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-09 |
| 涉及 commit | `18823271`（本次修补）；`b1d09037`（上游重构，本次基于此 follow-up） |
| 范围 | `openjiuwen/agent_teams/agent/coordination/`、`openjiuwen/agent_teams/agent/session_manager.py` |
| 测试基线 | `tests/unit_tests/agent_teams/` 864 passed / 16 skipped |
| Refs | `#751` |

## 背景

`b1d09037` 把 `DispatcherHost` 这个 god-bag protocol 拆成 `AgentRoundController` / `TeamLifecycleController` / `PollController` 三个角色化 protocol，方向正确。但 review 时发现该 commit 留下三处典型的"半生不熟"痕迹：

1. `BaseCoordinationHandler` 与 `EventDispatcher` 上的 `self._host` 字段在拆完之后已经无任何属性访问，纯粹是死代码。`coordination/CLAUDE.md` 反而写了一段"不应再用"的劝退文字——靠注释维护"别用这个字段"是反模式。
2. EventBus 增加了 `set_wake_callback()` 公开 mutator，目的是打断"bus 需要 dispatcher.dispatch / dispatcher 需要 bus 作为 PollController"的构造期循环依赖。代价是 EventBus 多了一个能创建非法状态的公开 setter，且构造顺序变得敏感。
3. `SessionManager` 引入了 `bind_session(session)` 公共入口，但 `bind_session(None)` 又被强行复用为"完全解绑"，同时还存在一个 `release_team_session()` 做"半解绑（保留 session_id）"。两个名字相近的方法做不同的事，行为差异没有显式记录。

本次 commit 是针对这三处的清扫修补。

## 数据结构与状态机

### Session 绑定状态

`TeamAgentState` 上有两个字段描述 session 绑定：

```python
session_id: Optional[str] = None
team_session: Optional[AgentTeamSession] = None
```

二者**生命周期是包含关系**：`session_id ⊇ team_session`。pause / stop tear-down 之后，`team_session` 释放掉但 `session_id` 保留——给 log correlation、resume 路径复用同一 id 用。

由此推出三态状态机：

```
未绑态 (id=None, ts=None)
   │
   ├─── bind ──▶ 完整态 (id=X, ts=session)
   │                │
   │                ├─── release ──▶ 半绑态 (id=X, ts=None)
   │                │                     │
   │                │                     └─── bind(new) ──▶ 完整态 (id=X', ts=new)
   │                │
   │                └─── unbind ──▶ 未绑态
   │
   └── unbind ──▶ 未绑态 (idempotent)
```

**API 形态应该对齐数据结构形态**——这是本次修补最核心的判断依据。

### EventBus / EventDispatcher 构造期拓扑

```
EventBus  ──提供 PollController──▶  EventDispatcher
EventBus  ◀──提供 wake_callback──   EventDispatcher.dispatch
```

构造期循环依赖是结构性的，无法消除。问题不是循环本身，而是**让 callback 绑定时机晚到 `start()` 而不是中间某个公开 setter**。

## 决策

### 1. 删除 `self._host` 死字段

`BaseCoordinationHandler` 与 `EventDispatcher` 都不再持有原始 host 引用。所有访问走窄 protocol 字段：`self._round` / `self._lifecycle` / `self._poll` / `self._blueprint` / `self._infra`。`coordination/CLAUDE.md` 中"不应再用 `_host`"的劝退段同步移除，改为"handler 不持有原始 host 引用"——这是事实陈述，不是禁令。

### 2. EventBus wake_callback 移到 `start()`

```python
class EventBus:
    def __init__(self, *, role, mailbox_poll_interval=30.0, task_poll_interval=30.0):
        # No wake_callback here — bound at start time
        self._wake_callback: Optional[WakeCallback] = None
        ...

    async def start(self, *, wake_callback: Optional[WakeCallback] = None) -> None:
        if wake_callback is not None:
            self._wake_callback = wake_callback
        ...
```

- 删除 `set_wake_callback()` 公开 setter
- `__init__` 不再接收 wake_callback
- `start()` 关键字参数 `wake_callback` 可选（None 兼容仅生命周期测试）

CoordinationKernel 装配序：

```python
def setup(self, *, role):
    event_bus = EventBus(role=role)
    dispatcher = EventDispatcher(host, blueprint=..., infra=..., poll_ctrl=event_bus)
    # No callback wiring here — moved to start()

async def start(self, session=None):
    ...
    await self._event_bus.start(wake_callback=self._dispatcher.dispatch)
```

EventBus 不再有"构造完但 callback 缺失"的中间态可被外部观察到——`start()` 成为唯一的绑定时机，构造和启动各自只做一件事。

### 3. SessionManager 拆三方法对应状态机

| 方法 | 类型签名 | 状态转换 | 调用站 |
|---|---|---|---|
| `bind_session(session)` | `session: AgentTeamSession` | * → 完整态 | `kernel.start` 在 session 非 None 时；`resume_for_new_session` / `recover_for_existing_session` |
| `release_session()` | 无参 | 完整 → 半绑 | `kernel.pause`、`kernel.stop` |
| `unbind_session()` | 无参 | * → 未绑 | `kernel.start` 在 session=None 路径 |

`bind_session` 入参类型从 `Optional[AgentTeamSession]` 收紧到 `AgentTeamSession`——None 路径不再合法。`resume_for_new_session` / `recover_for_existing_session` 的 `session` 入参也从 `Any` 收紧到 `AgentTeamSession`。

`kernel.start()` 中 None 路径从"调 `bind_session(session)` 让被调方分支处理"改为显式 `if session is not None: bind 否则 unbind`——状态转换写在调用面，不藏在被调方分支里。

命名上对齐 `openjiuwen/core/controller/base.py:409,427` 里早已存在的 `bind_session` / `unbind_session` 约定，不再发明第三种风格。

## 拒绝的方案

### 方案 B：单方法 + 参数

```python
async def bind_session(self, session, *, keep_session_id: bool = False) -> None
```

调用方写成 `bind_session(None, keep_session_id=True)` 表达"半解绑"。

**拒绝理由**：bool 参数承担"行为切换"，是经典 boolean trap；调用面读起来不如 `release_session()` 直白；并未消除特殊情况（`bind_session(None)` 仍兼任 unbind），只是把行为差异打包成参数。

### 方案 C：仅补 docstring

只在 `release_team_session()` 的 docstring 中明确写"故意保留 session_id"，不动 API 形态。

**拒绝理由**：用注释维护"两个相似方法的微妙差异"是用文档保护设计缺陷。Linus 风格："好代码消除特殊情况"——既然现在是修补阶段，一次修干净比贴标签更合理。

## 验证

- `tests/unit_tests/agent_teams/`：**864 passed / 16 skipped**，与基线一致
- `ruff check` / `ruff format --check`：通过
- `EventBus(role=..., wake_callback=...)` 构造调用在测试中改为 `EventBus(role=...)` + `await loop.start(wake_callback=...)`（4 处）；其它生产代码无 wake_callback 的构造时调用

## 已知遗留

下一步还能做但本次没动的：

- `EventDispatcher` 当前仍持有 `_blueprint` 整个对象，但实际只读 `role` / `member_name` 两个字段。把这两个值在 `setup()` 时直接传入、去掉 `_blueprint` 字段，是更彻底的"按真实使用面收窄注入"。改动较大、收益有限，留作后续。
- `Handler.__init__` 仍接收 5 个参数（`host, blueprint, infra, poll_ctrl[, throttle]`）。`blueprint` / `infra` 是 host 的派生数据但被独立注入，仍是"protocol 不暴露 + 外部注入"的中间态。彻底走法是把每个 handler 真实使用的字段拆出来注入，但要权衡构造站可读性。
