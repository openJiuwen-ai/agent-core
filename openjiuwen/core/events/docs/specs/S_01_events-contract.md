# S_01 Events 公共契约规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | `S_01 / events-contract` |
| 关联模块 | `openjiuwen/core/events/`；消费方包括 Core 业务模块、扩展运行时和 JiuwenSwarm 宿主 |
| 最近一次修订日期 | 2026-10-19 |
| 关联 feature | [F_01 EV-08 公共契约](../features/F_01_ev08-public-contract.md)；进度见[开发计划 EV-08](../designs/events-development-plan.md#ev-08-公共契约与接口基线可供外部并行开发) |
| 设计依据 | [设计概览](../designs/events-overview.md)、[详细架构](../designs/events-architecture.md) |

本规约是 Events 公共模型、接口协议、错误码和调用语义的唯一维护位置。当前 Events 尚无实现、公开导出和专项测试；以下条款均为**已确认、待实现**的目标契约。

## 1. 范围、术语与责任

### 1.1 责任范围

- **Events 负责：** 事件类型及 Handler 的注册与注销、匹配、执行快照、三层调用链、Builder 冻结与使用、EventNext 和调用错误语义。
- **Events 不负责：** 具体业务事件 payload、扩展包安装与卸载、包身份认证、宿主权限模型、停机等待、并发限额、嵌套深度和外部副作用确认。
- **宿主负责：** 启动阶段登记 Build Handler 并显式调用 `trigger(Event("ops.build", None))`；停止阶段停止发起新调用并等待自己持有的调用返回。
- **扩展运行时负责：** 核实包来源，并在自身运行时或 Handler 闭包/包装层保存包身份。EventBus 注册表不保存包身份。

### 1.2 术语

- **普通事件：** `ops.build` 以外的事件。
- **普通 Handler：** 处理普通事件的 USER、BUILTIN 或 CORE Handler。
- **注册快照：** 一次普通 `trigger()` 在首次 `await` 前固定的事件定义和 Handler 注册记录。
- **能力目录：** Builder 保存的 `namespace.method` 定义集合。
- **冻结 Builder：** 第一次成功执行 `ops.build` 后不再允许增加 namespace 或 method，并由 Bus 保存供后续业务调用使用的 Builder。
- **受支持 Session：** 首版接受 `SessionContext`、`None` 和 `openjiuwen.core.single_agent.Session`。

本规约适用于同一进程、同一 asyncio 事件循环内的 EventBus。跨线程、跨事件循环、跨进程投递和进程重启后的调用恢复不在本期范围内。

### 1.3 事实归属

| 对象 | 事实持有者 | 修改入口或责任 |
| --- | --- | --- |
| 事件类型、匹配函数、唯一 CORE | EventBus | `register_event_type()` / `unregister_event_type()` |
| USER / BUILTIN Handler、模式、层级、注册顺序 | EventBus | `register_event_handler()` / `unregister_event_handler()` |
| Event 与业务输入 | 调用方 | 构造 Event；链内如需改参则构造新 Event 交给 EventNext |
| SessionContext | 调用入口 | 每次 `trigger()` 规范化一次，调用内只读 |
| PackageMetadata | 扩展运行时、Handler 闭包或 Builder 调用方 | 不进入 Bus 注册记录；不得从 Event 或 Session 推断 |
| Builder 能力目录 | EventBus | `ops.build` CORE 创建，BUILTIN 填充，首次成功后冻结并保存 |
| EventNext 状态 | 当前执行器 | 当前 Handler 所属任务最多成功推进一次 |

`PackageMetadata` 只表示 Builder 构建 Ops 时调用方提供的包上下文，不是授权结果。`SessionContext` 也不是授权结果。

## 2. 公共模型

### 2.1 类型与值对象

```python
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol, TypeAlias

EventType: TypeAlias = str
EventInput: TypeAlias = object
EventOutput: TypeAlias = object
HandlerId: TypeAlias = str
OPS_BUILD_EVENT_TYPE: Final[EventType] = "ops.build"


class HandlerLevel(StrEnum):
    USER = "user"
    BUILTIN = "builtin"
    CORE = "core"


@dataclass(frozen=True, slots=True)
class Event:
    event_type: EventType
    event_input: EventInput


@dataclass(frozen=True, slots=True)
class SessionContext:
    session_id: str
    cwd: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PackageMetadata:
    package_id: str
    package_version: str | None = None


MatchPattern: TypeAlias = Mapping[str, object]
MatchPatternFunc: TypeAlias = Callable[[EventInput, MatchPattern], bool]


class EventNext(Protocol):
    async def __call__(self, event: Event) -> EventOutput | None: ...

    async def to(
        self,
        event: Event,
        handler_level: HandlerLevel,
    ) -> EventOutput | None: ...


EventHandler: TypeAlias = Callable[
    [Event, "EventHandlerOps | None", EventNext],
    Awaitable[EventOutput | None],
]
```

EventType 使用字符串，不使用枚举。业务事件名由业务平台在自己的仓库中维护；Core 只定义 `OPS_BUILD_EVENT_TYPE`。

### 2.2 标识与只读值

- EventType 必须匹配 `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`。`ops.` 前缀由 Core 保留，调用方不得登记、覆盖或注销该前缀下的事件。
- HandlerId 是所属 Bus 实例内唯一的不透明字符串，只用于注销注册记录。
- `PackageMetadata.package_id` 和 `SessionContext.session_id` 必须非空；缺失字段不得从 EventInput 推断。
- `SessionContext.metadata` 和 MatchPattern 只允许 `None`、`bool`、`int`、`float`、`str`，以及由这些值递归组成的映射和序列。Bus 在边界复制并转成只读形态。
- MatchPatternFunc 必须同步、无阻塞、无业务副作用，并且返回 `bool`。

### 2.3 公共导出

`openjiuwen.core.events` 的目标公共导出为：

- 标识与模型：EventType、EventInput、EventOutput、HandlerId、OPS_BUILD_EVENT_TYPE、HandlerLevel、Event、SessionContext、PackageMetadata、MatchPattern、MatchPatternFunc。
- 回调与协议：EventHandler、EventNext、OpsMethod、EventHandlerOpsBuilder、EventHandlerOps、ExtensionEventBusProtocol。
- 运行时对象：ExtensionEventBus。

不公开 `EventDefinition`、生命周期状态枚举、Handler 注册记录或注册表内部结构。Events 不导出专属异常类型。

## 3. 不变量

1. **唯一 CORE：** 每个已登记 EventType 必须且只能有一个 CORE；CORE 只通过 `register_event_type()` 登记。
2. **固定层级：** 单次调用按 USER → BUILTIN → CORE 进入，同层按注册顺序进入；正常返回顺序相反。
3. **调用快照：** 普通 `trigger()` 在首次 `await` 前固定匹配函数、CORE 和 Handler 记录；之后的注册或注销只影响后续调用。
4. **单次推进：** 每个 EventNext 只能由当前 Handler 所属异步任务，在 Handler 执行期间成功调用一次。
5. **Build 一次成功：** 宿主可以在首次成功前重试 `trigger(ops.build)`；第一次成功后 Builder 冻结并保存，同一 Bus 不再执行第二次 Build。
6. **业务调用依赖 Builder：** 尚无冻结 Builder 时，普通业务 `trigger()` 必须失败；普通业务调用不执行 `ops.build`。
7. **逐 Handler 构建 Ops：** 每个实际进入的普通 Handler 构建一次独立 Ops；被跳过的 Handler 不构建 Ops。
8. **包身份不进注册表：** EventBus 不从注册记录、EventInput 或 SessionContext 推断 PackageMetadata。`Builder.build()` 保留该入参，由掌握包上下文的调用方提供；没有包上下文时传 `None`。
9. **无隐式重试：** 匹配、构建、Handler、能力或取消失败不得自动补跑 CORE、重试 Handler 或转换为正常 `None`。
10. **注销不打断在途调用：** 注销只更新注册表；已经取得旧快照的调用继续执行到返回、异常或取消。

## 4. 接口契约

### 4.1 EventBus 公共协议

```python
class ExtensionEventBusProtocol(Protocol):
    def register_event_type(
        self,
        event_type: EventType,
        match_pattern_func: MatchPatternFunc,
        core_handler: EventHandler,
    ) -> None: ...

    def unregister_event_type(self, event_type: EventType) -> None: ...

    def register_event_handler(
        self,
        event_type: EventType,
        handler: EventHandler,
        match_pattern: MatchPattern,
        handler_level: HandlerLevel = HandlerLevel.USER,
    ) -> HandlerId: ...

    def unregister_event_handler(self, handler_id: HandlerId) -> None: ...

    async def trigger(
        self,
        event: Event,
        session: "Session | SessionContext | None" = None,
    ) -> EventOutput | None: ...
```

公共接口没有 `initialize()`、`close()` 和状态属性。宿主只通过上述注册接口和 `trigger()` 协作。

### 4.2 注册和注销

#### `register_event_type(event_type, match_pattern_func, core_handler)`

- 三个参数作为一个原子事实登记；重复 EventType 不覆盖旧定义。
- 调用方不得登记 `ops.` 前缀事件。`ops.build` 及其 CORE 由 Core 内部预置。
- EventType、匹配函数或 CORE 非法时，以 `EVENTS_REGISTRATION_INVALID` 拒绝且不留下部分记录。

#### `register_event_handler(event_type, handler, match_pattern, handler_level=USER)`

- 只允许 USER 或 BUILTIN；普通入口登记 CORE 必须拒绝。
- MatchPattern 在登记时复制并转成只读值。
- 未知 EventType、非法回调、非法层级或非法模式不得留下部分记录。
- `ops.build` 只允许登记 BUILTIN，且必须使用空模式。
- Builder 冻结后，不再允许登记或注销 `ops.build` Handler；普通业务事件的 Handler 仍可变更。
- 接口不接收、保存或校验 PackageMetadata。

#### 注销

- `unregister_event_handler()` 对未知 HandlerId 幂等返回；不得注销 CORE。
- `unregister_event_type()` 对未知普通 EventType 幂等返回；仍有关联 USER 或 BUILTIN 时拒绝且不做部分删除。
- 调用方不得登记或注销 `ops.` 前缀事件。
- 注销返回只表示注册表已更新；已取得快照的调用会继续执行。

### 4.3 `ops.build`

- 宿主完成 Build BUILTIN 登记后，显式调用 `trigger(Event(OPS_BUILD_EVENT_TYPE, None), None)`。
- Build 调用的 session 和传给 Handler 的 ops 均为 `None`。Build CORE 返回新的空 Builder。
- 每个 Build BUILTIN 先 `await next_handler(event)`，再向同一个 Builder 调用 `add_namespace()` 或 `add_method()`，最后返回同一个 Builder。
- Bus 校验返回结果后冻结并保存 Builder。没有 Build BUILTIN 时，空 Builder 也是合法结果。
- 短路未取得 Builder、替换 Builder、能力冲突、非法结果、异常或取消均视为失败；部分 Builder 丢弃，宿主可修正后再次触发。
- 第一次 Build 成功后，再次触发 `ops.build` 或变更 Build Handler 均以 `EVENTS_STATE_INVALID` 拒绝。
- 并发保护属于实现细节，不增加公开锁、状态或生命周期接口。

### 4.4 普通 `trigger()`

Bus 按以下顺序处理普通调用：

1. 校验 EventType 和 Event；没有冻结 Builder 时以 `EVENTS_STATE_INVALID` 拒绝。
2. 在首次 `await` 前固定匹配函数、CORE 和 USER / BUILTIN Handler 快照。
3. 将受支持 Session、SessionContext 或 None 规范化一次。
4. 用 MatchPatternFunc 筛选 USER / BUILTIN；CORE 不参与筛选。
5. 按 USER → BUILTIN → CORE 执行，链内不重新匹配。
6. 每次进入 Handler 前构建独立 Ops。`Builder.build(package_metadata, session_context)` 的 package_metadata 由掌握包上下文的适配层提供；通用 Bus 没有包上下文时传 `None`。
7. 将最外层 Handler 返回的 `EventOutput | None` 原样交给调用方。

本规约不引入通用输入/输出校验器。具体 payload 约束由业务事件自己的模型或 Handler/CORE 负责。

### 4.5 EventNext

| 当前 Handler | `next(event)` | `to(event, BUILTIN)` | `to(event, CORE)` |
| --- | --- | --- | --- |
| USER | 进入下一条 Handler | 跳过剩余 USER，进入首个 BUILTIN；没有则进入 CORE | 跳过剩余 USER 和 BUILTIN，进入 CORE |
| BUILTIN | 进入下一 BUILTIN；没有则进入 CORE | 非法 | 跳过剩余 BUILTIN，进入 CORE |
| CORE | 链尾返回 None | 非法 | 非法 |

`to(event, USER)` 始终非法。推进时 event_type 不得改变；event_input 可以改变，但当前链不重新匹配。Handler 不调用 EventNext 而直接返回即短路，Bus 不自动补跑 CORE。

EventNext 在参数和任务校验通过后即标记为已使用；即使下游失败，也不得再次使用同一对象。

### 4.6 Builder 与 Ops

```python
OpsMethod: TypeAlias = Callable[..., Awaitable[object]]


class EventHandlerOpsBuilder(Protocol):
    def add_namespace(self, namespace: str) -> None: ...

    def add_method(
        self,
        namespace: str,
        method_name: str,
        method: OpsMethod,
    ) -> None: ...

    def build(
        self,
        package_metadata: PackageMetadata | None = None,
        session_context: SessionContext | None = None,
    ) -> "EventHandlerOps": ...


class EventHandlerOps(Protocol):
    @property
    def package_metadata(self) -> PackageMetadata | None: ...

    @property
    def session_context(self) -> SessionContext | None: ...
```

- namespace 和 method 名必须是合法、非下划线开头的 Python 标识符，且不能与 Ops 公共属性冲突。
- `add_namespace()` 不得重复；`add_method()` 要求 namespace 已存在，并拒绝同 namespace 重名 method。
- OpsMethod 是异步函数；实现调用时把当前 EventHandlerOps 作为第一个参数。
- Builder 只保存能力函数定义。每次 `build(...)` 返回新的 Ops，不修改共享能力目录，也不保存可变的“当前包”或“当前会话”。
- `package_metadata` 入参必须保留。它由 Builder 调用方显式传入；EventBus 注册接口不负责收集或持久化它。
- Builder 冻结后，所有 add 操作以 `EVENTS_OPS_INVALID` 拒绝；build 仍可并发读取。
- Handler 通过 `ops.<namespace>.<method>(...)` 访问能力。未知 namespace、未知 method 或已失效 Ops 必须拒绝。
- 名词能力直接执行；动词能力可携带当前 SessionContext 发起新的业务事件。动词完成不自动推进原链。

### 4.7 错误码与取消

Events 不定义专属异常类。契约失败通过 Core `raise_error` 使用下列 StatusCode；均解析为不可自动重试的 ValidationError。

| StatusCode | 适用情况 |
| --- | --- |
| `EVENTS_REGISTRATION_INVALID` | 重复事件、非法层级、非法回调/模式、非法 `ops.` 登记或仍有关联 Handler |
| `EVENTS_UNKNOWN_EVENT_INVALID` | trigger 使用未知事件 |
| `EVENTS_STATE_INVALID` | 尚无冻结 Builder、重复 Build 或冻结后变更 Build Handler |
| `EVENTS_PAYLOAD_INVALID` | Event、SessionContext 或 PackageMetadata 的结构/只读值非法 |
| `EVENTS_MATCH_INVALID` | 匹配函数抛错、阻塞契约被违反或返回非 bool |
| `EVENTS_NEXT_INVALID` | 非法跳层、重复、并发、跨任务、失效后推进或改变 event_type |
| `EVENTS_OPS_INVALID` | 能力冲突/缺失、冻结后修改、构建失败或使用失效 Ops |

Handler、能力函数和业务 CORE 抛出的业务异常保持原类型和原因链向外传播。`asyncio.CancelledError` 原样传播，不转换成正常 `None`。

## 5. 生命周期与资源责任

- Bus 不公开 CONFIGURING、READY、DRAINING 或 CLOSED 状态。
- 第一次 `ops.build` 成功是唯一能力可用边界：成功前普通业务调用失败；成功后 Builder 冻结且 Build Handler 不再变化。
- 普通注册和注销只修改后续调用读取的注册表。已取得快照的调用持有旧 Handler 引用并继续完成。
- EventNext 与当前 Handler 的 Ops 只在该 Handler 执行期间有效。Handler 退出后不得发起新的推进或能力调用。
- 宿主停止时先停止发起新 `trigger()`，再等待自己持有的调用返回，最后释放扩展和 Bus 资源。Events 不提供 `close()`、drain、调用跟踪或停机超时策略。
- Events 不持久化调用栈或执行位置。进程重启后重新创建 Bus、重新登记并重新触发 `ops.build`。

## 6. 适用限制与兼容性

- 不保证不同 `trigger()` 的开始或完成顺序，也不按 session_id 自动串行。
- 不提供事件持久队列、跨进程投递、故障重放、自动重试、副作用回滚或工具全局恰好执行一次。
- 嵌套深度、宿主并发限额、输入大小和 Handler 数量没有公共默认值。
- 同进程回调、只读模型和受控注册入口不构成恶意代码沙箱。
- 当前没有旧 Events 公共 API，S_01 是首版兼容基线。后续修改公共名称、参数、默认值、错误码或不变量时，必须同步本规约和契约测试。

## 7. 验证索引

当前没有 Events 实现和专项测试，以下入口均为待建立：

- EV-08 公共接口契约：公共导出、模型只读、Protocol 签名和错误码 → EV-08-V-01～04。
- EV-01 EventBus 注册与执行引擎：注册、匹配、快照和 EventNext → 原 EV-01/02/03 验收组。
- EV-04 Builder/Ops：Builder 登记、冻结、`build(package_metadata, session_context)` 和 Ops 隔离 → EV-04-V-01～04。
- EV-05 完整调用链集成：`ops.build`、冻结门禁、SessionContext、逐 Handler Ops 和嵌套事件 → 原 EV-05/06 验收组。

完整场景、执行命令、版本环境和实际结果由对应 feature 及[开发计划](../designs/events-development-plan.md)记录。
