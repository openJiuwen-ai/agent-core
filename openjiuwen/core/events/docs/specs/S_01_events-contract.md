# S_01 Events 公共契约规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | `S_01 / events-contract` |
| 关联模块 | `openjiuwen/core/events/`；消费方包括 Core 业务模块、扩展运行时和 JiuwenSwarm 宿主 |
| 最近一次修订日期 | 2026-10-30 |
| 关联 feature | [F_01 公共契约](../features/F_01_ev01-public-contract.md)、[F_06 包信息能力](../features/F_06_ops-package.md)；进度见[开发计划](../designs/events-development-plan.md) |
| 设计依据 | [设计概览](../designs/events-overview.md)、[详细架构](../designs/events-architecture.md) |

本规约是 Events 公共模型、接口协议、错误码和调用语义的唯一维护位置。公共模型、EventBus、Builder/Ops 已有实现与测试；具体方案和验证范围见关联 feature 与开发计划。

## 1. 范围、术语与责任

### 1.1 责任范围

- **Events 负责：** Core 预置内容的自动注册、事件类型及 Handler 的注册与注销、匹配、执行快照、三层调用链、Builder 保存与使用、EventNext 和调用错误语义。
- **Events 不负责：** 具体业务事件 payload、扩展包安装与卸载、包身份认证、宿主权限模型、停机等待、并发限额、嵌套深度和外部副作用确认。
- **宿主负责：** 启动阶段登记 Build Handler，并在对外提供 Bus 前串行、显式调用一次 `trigger(Event("ops.build", None))`；停止阶段停止发起新调用并等待自己持有的调用返回。
- **扩展运行时负责：** 核实包来源，并在登记 Handler 时通过 `package_metadata` 提供已核实的包身份。

### 1.2 术语

- **普通事件：** `ops.build` 以外的事件。
- **普通 Handler：** 处理普通事件的 USER、BUILTIN 或 CORE Handler。
- **注册快照：** 一次普通 `trigger()` 在首次 `await` 前固定的事件定义和 Handler 注册记录。
- **能力目录：** Builder 保存的 `namespace.method` 定义集合。
- **可用 Builder：** 第一次成功执行 `ops.build` 后由 Bus 保存、供后续普通调用构建 Ops 的 Builder。普通调用只以 Bus 是否已保存 Builder 判断可用。
- **受支持 Session：** 首版接受 `SessionContext`、`None` 和 `openjiuwen.core.single_agent.Session`。

本规约适用于同一进程、同一 asyncio 事件循环内的 EventBus。跨线程、跨事件循环、跨进程投递和进程重启后的调用恢复不在本期范围内。

### 1.3 事实归属

| 对象 | 事实持有者 | 修改入口或责任 |
| --- | --- | --- |
| 事件类型、匹配函数、唯一 CORE、可选输入/输出 Validator | EventBus | `register_event_type()` / `unregister_event_type()` |
| USER / BUILTIN Handler、模式、层级、注册顺序 | EventBus | `register_event_handler()` / `unregister_event_handler()` |
| Event 与业务输入 | 调用方 | 构造 Event；链内如需改参则构造新 Event 交给 EventNext |
| SessionContext | 调用入口 | 每次 `trigger()` 规范化一次，调用内只读 |
| PackageMetadata | EventBus Handler 注册记录 | `register_event_handler(..., package_metadata=...)` 登记；不得从 Event 或 Session 推断 |
| Builder 能力目录 | EventBus | `ops.build` CORE 创建，BUILTIN 填充，首次成功后由 Bus 保存 |
| EventNext 状态 | 当前执行器 | 当前 Handler 所属任务最多成功推进一次 |

`PackageMetadata` 只表示 Handler 注册时提供的包上下文，不是授权结果。`SessionContext` 也不是授权结果。

## 2. 公共模型

### 2.1 类型与值对象

```python
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
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
    root: Path | None = None


MatchPattern: TypeAlias = Mapping[str, object]
MatchPatternFunc: TypeAlias = Callable[[EventInput, MatchPattern], bool]
EventInputValidator: TypeAlias = Callable[[EventInput], bool]
EventOutputValidator: TypeAlias = Callable[[EventOutput | None], bool]


class EventNext:
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
- MatchPatternFunc 与 EventType 可选 Validator 必须同步、无阻塞、无业务副作用，并且返回严格的 `bool`。

### 2.3 公共导出

`openjiuwen.core.events` 的目标公共导出为：

- 标识与模型：EventType、EventInput、EventOutput、HandlerId、OPS_BUILD_EVENT_TYPE、HandlerLevel、Event、SessionContext、PackageMetadata、MatchPattern、MatchPatternFunc、EventInputValidator、EventOutputValidator。
- 回调与协议：EventHandler、OpsMethod、ExtensionEventBusProtocol。
- 运行时对象：EventNext（`openjiuwen/core/events/event_next.py`）、EventHandlerOpsBuilder 和 EventHandlerOps（`openjiuwen/core/events/ops.py`）、ExtensionEventBus。

不公开 `EventDefinition`、生命周期状态枚举、Handler 注册记录或注册表内部结构。Events 不导出专属异常类型。

## 3. 不变量

1. **唯一 CORE：** 每个已登记 EventType 必须且只能有一个 CORE；CORE 只通过 `register_event_type()` 登记。
2. **固定层级：** 单次调用按 USER → BUILTIN → CORE 进入，同层按注册顺序进入；正常返回顺序相反。
3. **调用快照：** 普通 `trigger()` 在首次 `await` 前固定匹配函数、CORE、可选 Validator 和 Handler 记录；之后的注册或注销只影响后续调用。
4. **单次推进：** 每个 EventNext 只能由当前 Handler 所属异步任务，在 Handler 执行期间成功调用一次。
5. **Build 一次成功：** Bus 构造和普通业务调用都不触发 Build；宿主可以在首次成功前重试 `trigger(ops.build)`；第一次成功后 Bus 保存 Builder，同一 Bus 不再执行第二次 Build。
6. **业务调用依赖 Builder：** Bus 尚未保存 Builder 时，普通业务 `trigger()` 必须失败；普通业务调用不执行 `ops.build`。
7. **逐 Handler 构建 Ops：** 每个实际进入的普通 Handler 构建一次独立 Ops；被跳过的 Handler 不构建 Ops。
8. **包身份来自注册记录：** USER / BUILTIN 的 PackageMetadata 只来自其注册记录，CORE 使用 `None`；EventBus 不从 EventInput 或 SessionContext 推断 PackageMetadata。Handler 可直接读取 `ops.package_metadata`，也可使用第 4.6.1 节的 `ops.package` 方法。
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
        *,
        input_validator: EventInputValidator | None = None,
        output_validator: EventOutputValidator | None = None,
    ) -> None: ...

    def unregister_event_type(self, event_type: EventType) -> None: ...

    def register_event_handler(
        self,
        event_type: EventType,
        handler: EventHandler,
        match_pattern: MatchPattern,
        handler_level: HandlerLevel = HandlerLevel.USER,
        *,
        package_metadata: PackageMetadata | None = None,
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

#### `register_event_type(event_type, match_pattern_func, core_handler, *, input_validator=None, output_validator=None)`

- 前三个位置参数作为一个原子事实登记；重复 EventType 不覆盖旧定义。
- `input_validator` 与 `output_validator` 是仅关键字可选参数。未传入时保持与未配置 Validator 的现有行为一致。
- 可选 Validator 必须是同步 callable，并返回严格的 `bool`；与 matcher、CORE 一并保存在事件定义中，`unregister_event_type()` 时一并释放。
- 调用方不得登记 `ops.` 前缀事件。`ops.build` 及其 CORE 由 Core 内部预置。
- EventType、匹配函数、CORE 或 Validator 配置非法时，以 `EVENTS_REGISTRATION_INVALID` 拒绝且不留下部分记录。

#### `register_event_handler(event_type, handler, match_pattern, handler_level=USER, *, package_metadata=None)`

- 只允许 USER 或 BUILTIN；普通入口登记 CORE 必须拒绝。
- MatchPattern 在登记时复制并转成只读值。
- `package_metadata` 是仅关键字可选参数，原四参数调用保持兼容。非 `None` 时必须是 `PackageMetadata`，并随注册记录保存；非法值以 `EVENTS_PAYLOAD_INVALID` 拒绝。
- 未知 EventType、非法回调、非法层级、非法模式或非法 PackageMetadata 不得留下部分记录。
- `ops.build` 只允许登记 BUILTIN，且必须使用空模式。
- Bus 保存 Builder 后，不再允许登记或注销 `ops.build` Handler；普通业务事件的 Handler 仍可变更。

#### 注销

- `unregister_event_handler()` 对未知 HandlerId 幂等返回；不得注销 CORE。
- `unregister_event_type()` 对未知普通 EventType 幂等返回；注销已登记 EventType 时，同步级联删除其全部 USER / BUILTIN Handler、HandlerId 索引和事件定义。
- 调用方不得登记或注销 `ops.` 前缀事件。
- 注销返回只表示注册表已更新；已取得快照的调用会继续执行。

### 4.3 `ops.build`

- `ops.build` EventType 及其唯一 CORE 由 Core 预置模块在每个 Bus 构造时自动注册，调用方无需也不能登记。
- 宿主完成 Build BUILTIN 登记后，显式调用 `trigger(Event(OPS_BUILD_EVENT_TYPE, None), None)`。
- Build 调用的 session 和传给 Handler 的 ops 均为 `None`。Build CORE 返回新的空 Builder。
- 每个 Build BUILTIN 先 `await next_handler(event)`，再向同一个 Builder 调用 `add_namespace()` 或 `add_method()`，最后返回同一个 Builder。
- Bus 校验返回结果后保存 Builder。没有 Build BUILTIN 时，空 Builder 也是合法结果。
- 短路未取得 Builder、能力冲突、结果不是 `EventHandlerOpsBuilder`、异常或取消均视为失败；部分 Builder 丢弃，宿主可修正后再次触发。Bus 不校验结果是否为 CORE 创建的同一个 Builder，“返回同一个 Builder”是 Build Handler 的编码约定。
- 第一次 Build 成功后，再次触发 `ops.build` 或变更 Build Handler 均以 `EVENTS_STATE_INVALID` 拒绝。
- Bus 不为 Build 加锁，也不支持并发 Build；宿主必须串行触发，并发 Build 的结果未定义。不增加公开状态或生命周期接口。

### 4.4 普通 `trigger()`

Bus 按以下顺序处理普通调用：

1. 校验 EventType 和 Event；Bus 尚未保存 Builder 时以 `EVENTS_STATE_INVALID` 拒绝。
2. 在首次 `await` 前固定匹配函数、CORE、可选 Validator 和 USER / BUILTIN Handler 快照。
3. 将受支持 Session、SessionContext 或 None 规范化一次。
4. 若事件定义配置了 `input_validator`，校验初始 `event_input`；返回 `False` 时以 `EVENTS_PAYLOAD_INVALID` 拒绝，matcher 和 Handler 均不执行。
5. 用 MatchPatternFunc 筛选 USER / BUILTIN；CORE 不参与筛选。
6. 按 USER → BUILTIN → CORE 执行，链内不重新匹配。USER / BUILTIN Adapter 在 Handler 调用 `EventNext` 传入新 Event 前校验 `event_input`；Handler 正常返回后校验 Handler 输出；CORE 正常返回后校验 CORE 输出。未配置对应 Validator 时跳过该项业务校验。
7. 每次进入 Handler 前构建独立 Ops。`Builder.build(package_metadata, session_context)` 的 package_metadata 取自该 Handler 注册记录，CORE 传 `None`。
8. 将最外层合法结果交给调用方；CORE 或入口校验失败时 `trigger()` 失败。

Validator 返回 `False` 表示业务值非法；Validator 抛异常或返回非 `bool` 时视为事件定义错误，原样传播，不适用扩展 Handler 的 skip。预置事件通过同一注册参数声明 Validator，不增加专用容错分支。

### 4.5 EventNext

| 当前 Handler | `next(event)` | `to(event, BUILTIN)` | `to(event, CORE)` |
| --- | --- | --- | --- |
| USER | 进入下一条 Handler | 跳过剩余 USER，进入首个 BUILTIN；没有则进入 CORE | 跳过剩余 USER 和 BUILTIN，进入 CORE |
| BUILTIN | 进入下一 BUILTIN；没有则进入 CORE | 非法 | 跳过剩余 BUILTIN，进入 CORE |
| CORE | 链尾返回 None | 非法 | 非法 |

`to(event, USER)` 始终非法。推进时 event_type 不得改变；event_input 可以改变，但当前链不重新匹配。Handler 不调用 EventNext 而直接返回即短路，Bus 不自动补跑 CORE。

同一 Handler 执行期间可以多次推进 EventNext。Handler 退出后，该对象失效，不得再发起推进。

### 4.6 Builder 与 Ops

```python
from typing import Any


OpsMethod: TypeAlias = Callable[..., Awaitable[object]]


class EventHandlerOpsBuilder:
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


class EventHandlerOps:
    @property
    def package_metadata(self) -> PackageMetadata | None: ...

    @property
    def session_context(self) -> SessionContext | None: ...

    def __getattr__(self, namespace: str) -> Any: ...
```

- namespace 和 method 名必须是合法、非下划线开头的 Python 标识符，且不能与 Ops 公共属性冲突。
- `add_namespace()` 不得重复；`add_method()` 要求 namespace 已存在，并拒绝同 namespace 重名 method。
- OpsMethod 是异步函数；实现调用时把当前 EventHandlerOps 作为第一个参数。
- Builder 只保存能力函数定义。每次 `build(...)` 返回新的 Ops，不修改共享能力目录，也不保存可变的“当前包”或“当前会话”。
- `package_metadata` 入参必须保留。EventBus 调用时传入当前 Handler 注册记录中的值。
- Builder 没有冻结状态：`ops.build` 成功返回即代表能力目录构建完成，此后不再修改；add 与 build 不设先后状态检查。
- 宿主若短路 `ops.build` 并返回自己的 Builder，应自行保留所需能力；Bus 不自动补齐被跳过的能力。
- Handler 通过 `ops.<namespace>.<method>(...)` 访问能力。未知 namespace 或未知 method 必须拒绝。
- 名词能力直接执行；动词能力可携带当前 SessionContext 发起新的业务事件。动词完成不自动推进原链。

### 4.6.1 package 包信息能力

按预置构建链完成 `ops.build` 后，普通 Handler 的 Ops 包含 `package` 命名空间。
两个方法均为无参数的异步调用。

| 完整调用 | 返回类型 | 返回规则 |
| --- | --- | --- |
| `await ops.package.root()` | `Path \| None` | 无包信息时返回 `None`，否则返回 `package_metadata.root`；该字段也允许为 `None` |
| `await ops.package.name()` | `str \| None` | 无包信息时返回 `None`，否则返回 `package_metadata.package_id`，不使用展示名称 |

- 两个方法直接读取当前 Handler 的注册信息，不触发事件、不推进当前链、不访问文件，也不改写上下文。
- 返回值不依赖 Session 或 EventInput。平台预置的 BUILTIN Handler 未绑定包信息时，两个方法均允许返回 `None`；CORE 也返回 `None`。
- USER 和 BUILTIN 都可以绑定扩展包信息。只要已绑定，就按上述规则返回字段，不按层级改写包身份。
- 方法不补齐或重新解析路径，也不提供包认证或文件访问授权。扩展加载时如何提供包信息，见
  [Extension S_02](../../../extension/docs/specs/S_02_registry-and-bus.md#4-bus-发布)。
- 方法调用的错误与取消遵循[第 4.7 节](#47-错误码与取消)；没有包信息是合法结果，不视为异常。

### 4.7 错误码与取消

Events 不定义专属异常类。契约失败通过 Core `raise_error` 使用下列 StatusCode；均解析为不可自动重试的 ValidationError。

| StatusCode | 适用情况 |
| --- | --- |
| `EVENTS_REGISTRATION_INVALID` | 重复事件、非法层级、非法回调/模式或非法 `ops.` 登记 |
| `EVENTS_UNKNOWN_EVENT_INVALID` | trigger 使用未知事件 |
| `EVENTS_STATE_INVALID` | Bus 尚未保存 Builder、重复 Build 或 Build 成功后变更 Build Handler |
| `EVENTS_PAYLOAD_INVALID` | Event、SessionContext、PackageMetadata 的结构/只读值非法，或初始 `event_input` 未通过事件定义的 `input_validator` |
| `EVENTS_MATCH_INVALID` | 匹配函数抛错、阻塞契约被违反或返回非 bool |
| `EVENTS_NEXT_INVALID` | 非法跳层、跨任务、失效后推进或改变 event_type |
| `EVENTS_OPS_INVALID` | 能力冲突/缺失或构建失败 |

下游 Handler、能力函数和业务 CORE 抛出的异常，以及 Validator 自身故障，保持原类型和原因链向外传播。`asyncio.CancelledError` 原样传播，不转换成正常 `None`，也不记录为扩展 Handler 被隔离。

USER / BUILTIN Handler 在自身抛异常、返回非 awaitable、转发输入未通过 `input_validator`（返回 `False`）或输出未通过 `output_validator`（返回 `False`）时，跳过当前 Handler 并执行固定恢复：下游尚未执行时使用该 Handler 收到的原始 Event 继续下游一次；最后一次下游已成功时复用该结果（含合法 `None`）；最后一次下游已失败时传播该失败，不得再次调用下游，也不得回退到更早的成功结果。容错机制不得重放已执行的下游或 CORE。每次进入 skip 时通过 Core 日志输出一条 error，字段限于 `event_type`、`handler_id`、`handler_level`、可用的 `package_id` 和异常信息，不记录业务输入、输出或工具数据；调用方在恢复成功时取得正常结果。CORE 不参与 skip。Handler 超时与 `.catch()` 自定义兜底不在本期范围。

## 5. 生命周期与资源责任

- Bus 不公开 CONFIGURING、READY、DRAINING 或 CLOSED 状态。
- 第一次 `ops.build` 成功并由 Bus 保存 Builder 是唯一能力可用边界：成功前普通业务调用失败；成功后 Build Handler 不再变化。
- 普通注册和注销只修改后续调用读取的注册表。已取得快照的调用持有旧 Handler 引用并继续完成。
- EventNext 只在当前 Handler 执行期间有效。Handler 退出后不得发起新的推进。
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
- EV-03 Builder/Ops（已实现）：公开具体类、`build(package_metadata, session_context)`、Ops 隔离与动态调用 → EV-03-V-01～04。
- EV-04 完整调用链集成：预置自动注册、外部 `ops.build`、Builder 门禁、PackageMetadata、SessionContext、逐 Handler Ops 和嵌套事件 → EV-04 验收组。

完整场景、执行命令、版本环境和实际结果由对应 feature 及[开发计划](../designs/events-development-plan.md)记录。
