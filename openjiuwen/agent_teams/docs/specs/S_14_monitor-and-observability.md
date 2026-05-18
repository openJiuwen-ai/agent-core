# Monitor and Observability

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/monitor/`、`openjiuwen/agent_teams/observability/`（预留） |
| 最近一次修订日期 | 2026-05-17 |
| 关联 feature | F_09_team-stream-logging.md |

## 范围 / 边界

本规约只管两件事：

1. **`monitor/` 子模块**——团队运行态的**只读观察面**：
   - `TeamMonitor` 暴露给 SDK / CLI / 上层 UI 的查询 API（团队/成员/任务/邮箱）+ 实时事件流（`MonitorEvent` 异步迭代器），以及 `Runner.get_agent_team_monitor` 这个公共 facade。
   - `TeamStreamLogger`——`Runner.run_agent_team_streaming` 流式输出的**聚合诊断日志**处理对象：调用方用目标文件路径构造（`TeamStreamLogger(file_path=...)`）后经 `stream_logger` 入参注入，runner 把每个 chunk 喂给它，它**按 `(成员, 角色)` source 独立聚合** token 流再写入该文件。它是 `monitor/` 里唯一会主动写文件的设施，**不走 `team_logger`**（自管 open/write/flush/close），但仍是只读观察者——不碰 team 运行时状态。
2. **`observability/` 子模块**——OpenTelemetry / 结构化日志 / metrics 的可观测性接入点。**当前仅 `__init__.py` 占位、源文件为空**（git 历史里有 `f151dad0` / `dd634e54` / `69a9354d` 三次 OTel 接入提交，目前实现已被回退/重构中），本规约把它当作"已规划但未上线"的扩展点处理。

**不管**的事情：

- **EventMessage 总线本身的传输 / 订阅**——那是 `messager/` + `coordination/event_bus.py` 的事。monitor 只是 leader `TeamAgent` 上的一个 listener 回调，不直接 subscribe topic。
- **结构化日志 / `team_logger`** 的字段定义——那是 `core/common/logging` 的事；监控关心的是把"发生了什么"暴露给外部观察者，不是"日志怎么落盘"。
- **EventBus / handler / dispatcher 的内部决策**——见 S_03。监控是 coordination 已经发出来的事件之上的**消费者**，决策不在这一层。
- **数据库 schema / `TeamDatabase` 内部表结构**——`MemberInfo / TaskInfo / MessageInfo` 是适配层，把内部 SQLModel 转成对外 Pydantic 模型，schema 漂移由 `from_internal` 吸收。
- **运行时控制面**（pause / stop / delete）——那是 `runtime/` 的事。`get_agent_team_monitor` 复用 runtime pool 寻址，但监控本身不参与生命周期决策。

## 不变量

下列断言在任意时刻必须为真，违反即设计退化。

1. **monitor 是只读视图**。`TeamMonitor` 的查询 API 与事件回调**绝不修改**任何运行时状态：不写 db、不发 messager、不动 `TeamAgent.state`、不触发 round。`_on_event` 把 `EventMessage` 转成 `MonitorEvent` 入队，没有第二条副作用。所有"看一眼"的操作走 monitor，所有"动一下"的操作走 runtime / interaction，两条路径不重叠。
2. **监控失败不阻断 team 运行**。`event_listeners` 在 `coordination/kernel.py:_filter_self` 里以 `try / except Exception` 包住 listener 调用并 `team_logger.error` 记录；listener 抛错只在日志里留痕，不会让 dispatcher 中断、不会丢事件、不会阻塞同 topic 的其它订阅者。`asyncio.Queue.put_nowait` 选择 nowait 而非 `await put`，是为了让监控队列拥塞不回压到事件总线。
3. **只能绑 leader**。`create_monitor` 校验 `team_agent.role == TeamRole.LEADER`，否则 `ValueError`。teammate / human-agent 不持完整 `TeamDatabase` 视图也不订阅 team-wide 事件总线，绑过去拿到的数据是片段。
4. **过滤白名单基于 `MonitorEventType`**。`MonitorEvent.from_event_message` 仅识别枚举里列出的事件类型；plan_approval / tool_approval / worktree / workspace_lock 等内部事件**静默丢弃**（返回 `None`，不入队）。新增对外可见事件 = 加 `MonitorEventType` 成员 + 在 `MonitorEvent` 上声明对应可选字段；不要让监控变成全量事件镜像。
5. **`MonitorEvent` 字段是扁平 + 全可选**。四类 payload（team / member / task / message）的字段全部展平为顶层可选字段，每种事件只填相关子集。这是为了 SDK 消费方一次 `model_dump()` 拿全字段、不必按 `event_type` 分支拆 payload。新增字段也在顶层加，不要嵌 `payload: dict`。
6. **db 查询走 `_bound_session`**。每个 query API 在调用 `TeamDatabase` 前用 `contextlib.contextmanager` 把 `self._session_id` 绑到 `agent_teams.context` 的 contextvar；离开 `with` 块即 `reset_session_id`。原因：per-session 动态表（`team_task_<hash>` 等）的真名靠这个 contextvar 哈希出来，CLI / SDK 调用方所在协程没设过这个值，不绑就会哈希空串、指向不存在的表。
7. **生命周期与 listener 注册成对**。`start()` 注册一次 listener 后置 `_started=True`；`stop()` 反注册并入队 `None` sentinel 终止 `events()` 迭代。重复 `start()` 幂等返回；未 `start` 直接 `stop()` 也幂等。`stop()` 不关 `_event_queue`，仅放哨兵——已 yield 的事件不会丢，迭代器自然 break。
8. **生命周期不携带状态**。`TeamMonitor` 实例本身不持久化任何东西：不写 checkpoint、不挂 db 表、不在 pool 留 entry。`Runner.get_agent_team_monitor` 每次调用都 `create_monitor(entry.agent)` 现造一个，调用方自管 `start` / `stop`。team release 后 leader entry 出 pool，下次 `get_agent_team_monitor` 直接返回 `None`，不需要"清理 monitor"。
9. **不直接订阅 EventBus / messager**。monitor 通过 `TeamAgent.add_event_listener(handler)` 挂在 leader 的 `event_listeners` 列表上；fan-out 在 `coordination/kernel.py:_filter_self` 里。这是有意为之的"事件已经过 dispatcher 粗筛"的二次消费点，避免监控自己再去 messager 上多订一份 topic、跟 EventBus 抢消息。
10. **`get_agent_team_monitor` 寻址语义与其它 lifecycle facade 一致**。Key 是 `(team_name, session_id)`；pool 里没匹配 entry 返回 `None`、不抛异常（与 `pause_agent_team` / `stop_agent_team` 的失败语义对齐，见 S_01）。
11. **`observability/` 不挂载 monitor**。即便后续 OTel 上线，监控数据流（`TeamMonitor.events()` 队列）与遥测数据流（OTel span / metrics）是**两条独立通路**。一边给"想看"的调用方，一边给 APM / 日志后端。不要让其中一条退化成另一条的派生。
12. **`TeamStreamLogger` 永不向流式路径抛异常**。`feed` / `flush` 整体被 `try / except Exception` 包住，失败时尽力把错误标记行写回自己的输出文件（写不进去就静默吞）——诊断 logger 挂掉绝不能搞挂它观察的 stream。`__init__` 不在此豁免范围：路径不可用直接抛，让调用方在构造时立刻发现。与不变量 2「监控失败不阻断 team 运行」同源。
13. **`TeamStreamLogger` 是一次性的、与单次 run 绑定**。每次 `run_agent_team_streaming` 调用配一个新实例：`__init__` 用调用方给的 `file_path` 以 append 模式打开文件，`flush()` 在 stream 结束时把所有 source 的尾段写完并 close 文件。`_llm_output_seen` 等去重门控、`_runs` 累积缓冲贯穿整个 run。runner 不构造、不复用——构造责任在调用方（CLI / SDK），runner 只 `feed` / `flush`，类型在 `TYPE_CHECKING` 下引用。
14. **聚合按 source 独立维护**。`_runs: dict[(member, role), _Run]`——每个 source 有自己的待定累积段；同一 source 切换 category 或遇到该 source 的离散 chunk 时 flush **该 source 的段**，**不同 source 的 chunk 交错不互相打断**。leader 与 teammate 在 inprocess fan-out 下 chunk 必然交错，单一游标模型会把每个 token 切成独立记录、彻底破坏聚合，故须按 source 分桶。
15. **`hide_dm` 是 monitor 实例级别的对称过滤**。`TeamMonitor(hide_dm=True)` 同时作用于 pull 与 push 两条路径：`get_messages` 把非广播消息（`MessageInfo.broadcast=False`）从结果中剔除——单收件人 DM 视图（带 `to_member_name`）直接返 `[]`，全 team 视图走 `get_team_messages(broadcast=True)` 下推到 DAO；`_on_event` 丢弃 `MonitorEventType.MESSAGE` 事件，`BROADCAST` 不动。两路必须一致：单边过滤会让"流里看不到 DM 但 query 仍能查到"或反之，破坏调用方对"hide_dm = DM 不可见"的语义预期。`hide_dm` 只屏蔽消息维度，team / member / task 事件不受影响。

## 接口契约

### `monitor/` 公共表面

`openjiuwen.agent_teams.monitor` 仅导出以下符号（来自 `monitor/__init__.py`）：

```python
__all__ = [
    "create_monitor",
    "MemberInfo",
    "MessageInfo",
    "MonitorEvent",
    "MonitorEventType",
    "TaskInfo",
    "TeamInfo",
    "TeamMonitor",
    "TeamStreamLogger",
]
```

#### 工厂

```python
def create_monitor(team_agent: TeamAgent, *, hide_dm: bool = False) -> TeamMonitor:
    """Create a TeamMonitor bound to a leader TeamAgent.

    Args:
        team_agent: A fully configured leader TeamAgent instance.
        hide_dm: When True, instructs the returned ``TeamMonitor`` to
            drop every non-broadcast message from both query results
            and the live event stream. Defaults to False.

    Raises:
        ValueError: team_agent.role != LEADER, or team_backend is None.
    """
```

直接构造 `TeamMonitor(...)` 不是公共路径——`team_name` / `session_id` / `db` 全靠 `create_monitor` 从 `TeamAgent` 上拉，绕过工厂自己拼参数容易拼错（特别是 `session_id` 必须取 contextvar 当前值）。

#### `TeamMonitor` lifecycle

```python
class TeamMonitor:
    @property
    def team_name(self) -> str: ...
    @property
    def session_id(self) -> str: ...

    async def start(self) -> None:
        """Idempotent. Registers self._on_event on the leader."""

    async def stop(self) -> None:
        """Idempotent. Unregisters listener and terminates events() iterator."""
```

#### `TeamMonitor` 查询 API

| 方法 | 入参 | 返回 | 说明 |
|---|---|---|---|
| `get_team_info()` | — | `TeamInfo \| None` | 团队基本信息；team 不存在返回 None |
| `get_members(status=None)` | 可选 `MemberStatus` 字符串 | `list[MemberInfo]` | 全成员 / 按状态过滤 |
| `get_member(member_name)` | `str` | `MemberInfo \| None` | 单成员；不存在返回 None |
| `get_tasks(status=None)` | 可选 `TaskStatus` 字符串 | `list[TaskInfo]` | 全任务 / 按状态过滤 |
| `get_messages(*, to_member_name=None, from_member_name=None)` | keyword-only | `list[MessageInfo]` | 邮箱消息；`to_member_name` 走单成员收件箱视图，否则全 team。`hide_dm=True` 时单成员视图直接返 `[]`，全 team 视图退化为广播仅留 |

所有查询 API 都在 `_bound_session()` 上下文管理器中执行——调用方协程不需要预先 `set_session_id`。

#### `TeamMonitor` 事件流

```python
async def events(self) -> AsyncIterator[MonitorEvent]:
    """Yields MonitorEvent until stop() is called."""
```

实现：内部 `asyncio.Queue[MonitorEvent | None]`，`stop()` 入队 `None` 哨兵；迭代器 `await queue.get()`，遇 `None` 退出。**典型用法**：

```python
monitor = await Runner.get_agent_team_monitor(team_name=..., session_id=...)
await monitor.start()
try:
    async for evt in monitor.events():
        ...   # consume
finally:
    await monitor.stop()
```

调用方负责并发安全：单个 `events()` 迭代器不可同时被多个 consumer 消费（队列是单端的，多 consumer 会瓜分事件）；要 fan-out 自己起 `asyncio.Task` + 复制队列，monitor 不替你做。

### `Runner.get_agent_team_monitor` 公共 facade

```python
@classmethod
async def Runner.get_agent_team_monitor(
    *,
    team_name: str,
    session_id: str,
    hide_dm: bool = False,
) -> TeamMonitor | None:
    """Return a TeamMonitor for the active TeamAgent runtime, if present.

    ``hide_dm`` forwards to ``TeamMonitor``; when True the monitor drops
    every non-broadcast message from both query results and the live
    event stream.
    """
```

行为（实现链：`Runner.get_agent_team_monitor` → `_RunnerImpl.get_agent_team_monitor` → `TeamRuntimeManager.get_monitor` → `_pool._resolve_entry` → `create_monitor(entry.agent)`）：

- pool 里有 `(team_name, session_id)` 匹配 entry → 返回新 `TeamMonitor`（**未 `start()`**，调用方自启）；
- 无匹配 → `None`；
- entry 存在但 leader 已不再是 `LEADER` 角色（理论不应出现）→ `create_monitor` 直接 `ValueError`，让上游早暴露。

### `TeamStreamLogger` 流式诊断日志

```python
class TeamStreamLogger:
    def __init__(self, file_path: str | Path) -> None:
        """Open ``file_path`` for appending aggregated stream records.
        Parent dirs are created if missing. Raises on an unusable path
        so the caller fails fast at construction time."""

    def feed(self, chunk: OutputSchema) -> None:
        """Consume one stream chunk: buffer it (token streams) or write
        it immediately (discrete chunks). Never raises."""

    def flush(self) -> None:
        """Flush every pending per-source run and close the file. Call
        once after the stream ends. Never raises."""
```

用法：调用方用目标文件路径构造一个实例，经
`Runner.run_agent_team_streaming(..., stream_logger=...)` 注入；runner 在 leader 路径上对每个
chunk 调 `feed`，并在 `finally` 里第一时间调 `flush`（关闭文件）。**logger 自管 open / write /
flush / close**，**不走 `team_logger`**。

**聚合模型**：`feed` 把 chunk 按 `type` 归到一个 *category*，再分两类处理——

- **累积型**（`llm_output` / `llm_reasoning`）：token 流缓冲到 `self._runs[(member, role)]`
  对应的 `_Run` 里。**每个 source 一个独立累积段**。同一 source 切换 category、该 source 出现
  离散 chunk、或 `flush()` 时，flush 该 source 的段为一条记录。
- **离散型**（`tool_call` / `tool_result` / `tool_update` / `__interaction__` /
  `controller_output` / `message` / `todo.updated` / 未知类型）：先 flush **该 source** 的
  待定累积段，再立即写一条。

按 source 分桶是不变量 14——单一游标会被不同成员的 chunk 交错打断成 token 级记录。

**级别标签**（写入 record 的明文 level，不再走 `team_logger` 的级别过滤）：文本输出
（`llm_output` / `answer`）→ `[INFO]`；思考（`llm_reasoning`）与工具调用（`tool_call` /
`tool_result` / `tool_update`）→ `[DEBUG]`；中断（`__interaction__`）与任务失败
（`controller_output`）→
`[WARN]`；其余（`message` / `todo.updated` / 未知）→ `[INFO]`。映射由 `_CATEGORY_LEVEL` 表
声明式给出。

**记录格式**：每条记录 = 一行 header + 每行加 `  | ` 前缀的原始内容块，换行原样保留，
多行 markdown 完整可读。append 写入目标文件，每条记录后跟一个换行分隔：

```
2026-05-15 10:13:10.534 [INFO] member=team_leader role=leader category=text
  | Here is the plan:
  |
  | 1. Research
  | 2. Implement
```

时间戳是 `feed` 调用时的本地时钟；首行带 `[LEVEL]` 标签 + `member=` / `role=` / `category=`。
内容里的字面 `{}` / `%s` 不会被任何 formatter 处理（直接 `file.write`），无注入面。

**与契约相关的细节**：

- chunk 上的 `source_member` / `role` 来自 `TeamOutputSchema`（S_05 / S_12）；**非
  `TeamOutputSchema`（裸 `OutputSchema`）的 chunk 直接跳过**——tracer span 等基础设施
  层通过 session 归一化漏进 stream 时全是裸 `OutputSchema`，不属于团队成员输出，留在日志
  里只会淹没真正的团队流程（chunk 计数仍 +1，只是不写记录）。
- `answer` chunk 在**同一 source** 已出现过 `llm_output` 时丢弃（per-source 去重，对齐 CLI
  renderer 的 `has_llm_output` 但按 source 维度）。
- `team.runtime_ready`（`message` chunk 且 `payload.event_type` 命中）单列为
  `runtime_ready` 类别。
- `tool_call` / `tool_result` 的 payload 在缺少标准 `tool_name` / `tool_args` /
  `tool_result` 字段时（非 `tool_tracker` 路径的不同 schema），fallback 为整个 payload 的
  capped 字符串——保证记录有实际内容而不是 `tool_name= tool_args=` 两个空字段。
- `tool_update` 是第三方 rail（如 `jiuwenclaw/stream_event_rail`）对一次 tool call 进度
  状态的通知，payload shape `{"tool_update": {"tool_name", "tool_call_id", "arguments",
  "status"}}`；logger 抽出内嵌字段写一行 `tool_name=… status=… tool_call_id=… arguments=…`，
  不再落到 `category=other` 里 dump 整个 payload。
- `tool_result` / `tool_args` 内容超阈值截断；模型文本输出（`llm_output` /
  `llm_reasoning` / `answer`）永不截断。
- `feed` / `flush` 内部出错时，best-effort 写一行 `[WARN] ... error: ...` 标记到同一文件；
  写不进去就静默吞。绝不让异常逃逸到 runner。

### `observability/` 占位

当前 `openjiuwen/agent_teams/observability/` **不导出任何符号**（目录里仅 `__pycache__/`，没有提交的 `.py`）。git 历史保留了 OTel callback handler / span context / semconv / redaction / monitor handler / rail 的模块骨架（commits `f151dad0` / `69a9354d` / `dd634e54`），未来扩展点：

- **OTel span 包裹** team / member / task / message lifecycle，与 `MonitorEventType` 同一份白名单（避免两条出口语义漂移）。
- **Redaction**：sensitive payload 字段在 attribute 层与 `MonitorEvent` 都需要做敏感值脱敏，**两边共享一份规则**——不要让 monitor 输出明文、observability 输出脱敏，反之亦然。
- **Rail / callback handler** 接入 DeepAgent 的 prompt / tool 链路，把 LLM 调用作为 child span 挂到 team-level root span 下。

回填实现时遵循本规约不变量 11：observability 数据通路与 monitor 队列**不互相替代**。

## 数据结构

### `MonitorEventType`（事件白名单）

```python
class MonitorEventType(str, Enum):
    # Team lifecycle
    TEAM_CREATED, TEAM_CLEANED, TEAM_STANDBY

    # Member lifecycle
    MEMBER_SPAWNED, MEMBER_RESTARTED,
    MEMBER_STATUS_CHANGED, MEMBER_EXECUTION_CHANGED,
    MEMBER_SHUTDOWN, MEMBER_CANCELED

    # Task
    TASK_CREATED, TASK_UPDATED, TASK_CLAIMED,
    TASK_COMPLETED, TASK_CANCELLED, TASK_UNBLOCKED

    # Message
    MESSAGE, BROADCAST
```

枚举值与 `schema/events.py:TeamEvent` 的对应字符串常量**一一对齐**。`TeamEvent` 中存在但本枚举**有意排除**的：`PLAN_APPROVAL` / `TOOL_APPROVAL_RESULT` / `WORKTREE_*` / `WORKSPACE_*`——这些是协调内部信令，监控不暴露给外部。

### `MonitorEvent`（扁平事件）

```python
class MonitorEvent(BaseModel):
    # Common (always present)
    event_type: MonitorEventType
    team_name: str
    member_name: str | None
    timestamp: int                # monitor receive time (ms)

    # Team subset
    display_name: str | None
    leader_member_name: str | None
    created: int | None

    # Member subset
    old_status: str | None
    new_status: str | None
    reason: str | None
    restart_count: int | None
    force: bool | None

    # Task subset
    task_id: str | None
    status: str | None

    # Message subset
    message_id: str | None
    from_member_name: str | None
    to_member_name: str | None
```

每种事件只填相关子集，其余保持 `None`。`timestamp` 是**监控收到事件的本地时钟（毫秒）**，不是事件原始产生时间——后者在 db 行的 `updated_at` / `timestamp` 字段里，需要历史精确时刻请走 query API。

### 适配层 Pydantic 模型

| 模型 | `from_internal` 输入 | 关键字段 |
|---|---|---|
| `TeamInfo` | `Team` SQLModel | `team_name`, `display_name`, `leader_member_name`, `desc`, `created` (ms) |
| `MemberInfo` | `TeamMember` SQLModel | `member_name`, `team_name`, `display_name`, `desc`, `status`, `execution_status`, `mode` |
| `TaskInfo` | `TeamTaskBase` SQLModel | `task_id`, `team_name`, `title`, `content`, `status`, `assignee`, `updated_at` (ms, 与当前 `status` 绑定) |
| `MessageInfo` | `TeamMessageBase` SQLModel | `message_id`, `team_name`, `from_member_name`, `to_member_name`, `content`, `timestamp` (ms), `broadcast`, `is_read` |

适配层的存在意义：让监控 API 与内部 db schema 解耦。新增 db 字段不会自动暴露给 monitor 消费方；要暴露需要**显式**在对应 `Info` 上加字段并改 `from_internal`。

### 数据更新路径

监控数据有两条互补的更新路径，目标是同一份"团队当前状态"：

```
                       ┌───────────────────────────────────┐
                       │  TeamDatabase (per-session tables)│
                       └─────────────▲─────────────────────┘
                                     │ query (pull)
                                     │   _bound_session() 绑 contextvar
                                     │
   leader EventBus / messager        │
   (coordination/_filter_self)       │
              │                      │
              ▼                      │
   event_listeners[handler]   <──────┴── TeamMonitor
              │   push (asyncio.Queue, nowait)
              ▼
   MonitorEvent.from_event_message
              │
              ▼
       events() async iterator
```

- **Pull**：`get_team_info / get_members / get_tasks / get_messages` 直接读 db，反映**当前快照**——同时也是 monitor 启动前已经发生事件的唯一回填手段。
- **Push**：`_on_event` 由 EventBus fan-out 触发，反映**启动后的增量**。增量过滤白名单见上文。

两条路径**不保证一致**：push 队列里可能积压未消费的事件，此时 db 已经是更新后的状态；不要假设 `event A 到达 → query 立刻能看见 A 的副作用`。需要"严格读你刚 push 的事件"语义，调用方自己拿 `event` 去 query。

### 监控数据生命周期

`TeamMonitor` 实例自身**无持久化**——它只是 leader `TeamAgent` 之上的一层观察者。生命周期由 leader 决定：

| 阶段 | leader pool entry 状态 | `Runner.get_agent_team_monitor` |
|---|---|---|
| spec 路径 `activate` 后 | `RUNNING` 或 `PAUSED`（pool 持有 entry） | 返回新 `TeamMonitor` |
| `stop_agent_team` 之后 | entry 出 pool | 返回 `None` |
| `release_session` / `delete_team` 之后 | entry 出 pool（且 db 数据视情况清理） | 返回 `None` |

调用方持有的 `TeamMonitor` 不会"自动失效"——`stop_agent_team` 不通知 monitor。listener 在 `TeamAgent` tear-down 路径中随 leader 一起释放（leader 销毁 = `event_listeners` 列表被回收 = listener 引用被自动移除）。**消费方应该 ScopeGuard 模式：拿到 monitor → `start` → 消费 → `finally: stop`**，不要长期持有跨 session 复用。

### EventBus / handlers 的边界

参考 `S_03_coordination-protocol`：

- monitor **不直接订阅 EventBus**，也不订阅 messager topic。
- 注入点是 `coordination/kernel.py:_filter_self` 中的循环：

  ```python
  for listener in host.state.event_listeners:
      try:
          await listener(event)
      except Exception as e:
          team_logger.error("Event listener error: {}", e)
  ```

  即"事件已经经过 messager subscribe → `_filter_self` 收到 → fan-out listener → 入 EventBus → dispatcher / handler"链上的**第一段 fan-out**。
- 顺序保证：listener 先于 `_event_bus.enqueue` 调用——监控看到事件时，handler 还没必然处理完。这意味着 monitor 看到的"事件已发生"**早于**该事件造成的状态副作用落库。需要"看到事件 + 状态都到位"的消费方自己 retry 一次 query 即可。

## 与其它 spec 的关系

- **S_01 Public API**：`Runner.get_agent_team_monitor` 是 lifecycle facade 之一，按 `(team_name, session_id)` 寻址、无匹配 entry 返回 `None`（与 `pause_agent_team` / `stop_agent_team` 同口径）。
- **S_02 TeamAgent Architecture**：`event_listeners` 字段属于 `TeamAgentState`（跨 operator 可变状态象限），由 `add_event_listener` / `remove_event_listener` 维护；monitor 是该字段的**唯一公共生产者**。
- **S_03 Coordination Protocol**：fan-out 实际入口在 `_filter_self`；监听器异常吞没策略由 coordination 提供，monitor 不感知。新增对外可见事件类型时同步动 `TeamEvent` + `MonitorEventType` + `MonitorEvent` 三处。
- **S_05 Member Spawn & Stream**：`TeamStreamLogger` 消费的就是 S_05 定义的 leader 路径流式 chunk——inprocess 模式下 teammate chunk 经 `StreamController` fan-out 已带 `source_member` / `role` 标签汇入 leader stream，logger 直接读这些标签归属成员，不自己再做寻址。
- **S_06 Runtime Pool & Dispatch**：`TeamRuntimeManager.get_monitor` 复用 pool 的 `_resolve_entry` 寻址；entry 不存在直接返回 `None`，不影响 dispatch / gate。`get_monitor` 只读 pool，不参与决策。
- **S_07 Interaction Views & HITT**：HumanAgent inbox 与 monitor 是两条独立观察通道——前者面向"扮演成员"的人类输入交互，后者面向"旁观团队运行"的纯读数据。共用 `TeamDatabase`，不共用任何 listener / 队列。
- **S_08 Team Tools Contract**：team tools 修改任务 / 邮箱时通过 `TeamMessageManager` / `TeamTaskManager` 发出 `TeamEvent.TASK_*` / `MESSAGE` / `BROADCAST` 事件，由 monitor 翻译为 `MonitorEvent`。工具与监控解耦：工具不知道有 monitor 在看。
