# openjiuwen.core.multi_agent

## class openjiuwen.core.multi_agent.Session

```python
class openjiuwen.core.multi_agent.Session(
    session_id: str = None,
    envs: dict[str, Any] = None,
    team_id: str = "agent_team"
)
```

`AgentTeam` 执行的核心运行时会话，负责多智能体团队场景下的会话管理，包含状态存取、流式输出写入与子智能体会话创建等能力。

**参数**：

- **session_id** (str, 可选)：会话唯一标识。默认值：`None`，未提供时自动生成 UUID。
- **envs** (dict[str, Any], 可选)：`AgentTeam` 执行过程中使用的环境变量。默认值：`None`。
- **team_id** (str, 可选)：团队标识，默认值：`"agent_team"`。

---

### get_session_id

```python
get_session_id(self) -> str
```

获取本次 `AgentTeam` 执行的唯一会话标识。

**返回**：

**str**：当前会话的唯一标识字符串。

---

### get_env

```python
get_env(self, key: str, default: Any = None) -> Any
```

获取本次 `AgentTeam` 执行所配置的指定环境变量的值。

**参数**：

- **key** (str)：环境变量的键。
- **default** (Any)：键不存在时的默认值。

**返回**：

**Any**：对应键的环境变量值，不存在时返回 `default`。

---

### get_team_id

```python
get_team_id(self) -> str
```

获取当前会话所属的团队标识。

**返回**：

**str**：团队标识字符串。

---

### get_envs

```python
get_envs(self) -> dict
```

获取本次 `AgentTeam` 执行所配置的全部环境变量。

**返回**：

**dict**：所有环境变量的键值对字典。

---

### update_state

```python
update_state(self, data: dict) -> None
```

更新会话的全局状态，将 `data` 中的键值合并写入全局状态存储。

**参数**：

- **data** (dict)：要更新的状态键值对。

---

### get_state

```python
get_state(self, key=None) -> Any
```

读取会话的全局状态。

**参数**：

- **key** (str, 可选)：指定键名时返回该键对应的值；不传时返回完整状态字典。

**返回**：

**Any**：指定键的状态值，或完整状态字典。

---

### dump_state

```python
dump_state(self) -> dict
```

导出当前会话的完整状态快照。

**返回**：

**dict**：会话状态的完整字典表示。

---

### write_stream

```python
async write_stream(self, data: dict | OutputSchema) -> None
```

向团队会话的主输出流写入一条数据帧，用于向调用方实时推送团队执行进度或结果。会自动附加 `source_team_id` 元数据。

**参数**：

- **data** (dict | OutputSchema)：待写入的输出数据，支持字典或 `OutputSchema` 对象。

---

### write_custom_stream

```python
async write_custom_stream(self, data: dict) -> None
```

向自定义流通道写入数据帧，用于推送非标准格式的自定义事件或进度信息。

**参数**：

- **data** (dict)：待写入的自定义数据。

---

### stream_iterator

```python
stream_iterator(self) -> AsyncIterator
```

返回团队会话的流式输出迭代器，供调用方逐块消费输出内容。

**返回**：

**AsyncIterator**：可异步迭代的输出流。

---

### close_stream

```python
async close_stream(self) -> None
```

关闭会话的流式输出通道，通知消费方流已结束。通常在 `stream()` 实现的 `finally` 块中调用。

---

### create_agent_session

```python
create_agent_session(
    self,
    card: AgentCard | None = None,
    agent_id: str | None = None
) -> AgentSession
```

基于当前团队会话为指定子智能体创建独立的 `AgentSession`，子智能体通过该会话共享同一流式输出通道和会话上下文。

**参数**：

- **card** (AgentCard, 可选)：子智能体的 `AgentCard`；与 `agent_id` 二选一，均不传时自动以 `agent_id` 构造。
- **agent_id** (str, 可选)：子智能体 ID，`card` 为 `None` 时使用。

**返回**：

**AgentSession**：与当前团队会话共享输出流的子智能体会话对象。

---

## function openjiuwen.core.multi_agent.create_agent_team_session

```python
create_agent_team_session(
    session_id: str = None,
    envs: dict[str, Any] = None,
    team_id: str = "agent_team"
) -> Session
```

工厂函数，创建并返回一个 `AgentTeam` 会话对象。

**参数**：

- **session_id** (str, 可选)：会话唯一标识，不传时自动生成 UUID。
- **envs** (dict[str, Any], 可选)：执行过程中使用的环境变量。
- **team_id** (str, 可选)：团队标识，默认值：`"agent_team"`。

**返回**：

**Session**：新建的 `AgentTeam` 会话实例。

---

## class openjiuwen.core.multi_agent.TeamCard

```python
class openjiuwen.core.multi_agent.TeamCard(
    id: str,
    name: str,
    description: str = "",
    agent_cards: List[AgentCard] = [],
    topic: str = "",
    version: str = "1.0.0",
    tags: List[str] = []
)
```

智能体团队的身份卡片，描述团队的静态标识信息。继承自 `BaseCard`（提供 `id`、`name`、`description` 字段）。

**属性**：

- **id** (str)：团队唯一标识，不可为空。
- **name** (str)：团队名称，不可为空。
- **description** (str)：团队描述。默认值：`""`。
- **agent_cards** (List[AgentCard])：团队成员的 `AgentCard` 列表（仅元数据，非实例）。默认值：`[]`。
- **topic** (str)：团队所属的主题或领域。默认值：`""`。
- **version** (str)：团队版本号。默认值：`"1.0.0"`。
- **tags** (List[str])：用于分类的标签列表。默认值：`[]`。

---

## class openjiuwen.core.multi_agent.TeamConfig

```python
class openjiuwen.core.multi_agent.TeamConfig(
    max_agents: int = 10,
    max_concurrent_messages: int = 100,
    message_timeout: float = 30.0
)
```

智能体团队的可变运行时配置，控制团队容量、并发消息数与消息超时等行为。

**属性**：

- **max_agents** (int)：团队允许的最大智能体数量。默认值：`10`。
- **max_concurrent_messages** (int)：最大并发消息处理数。默认值：`100`。
- **message_timeout** (float)：消息处理超时时间（秒）。默认值：`30.0`。

---

### configure_max_agents

```python
configure_max_agents(self, max_agents: int) -> TeamConfig
```

设置团队最大智能体数量。

**参数**：

- **max_agents** (int)：最大智能体数量。

**返回**：

**TeamConfig**：当前配置实例（支持链式调用）。

---

### configure_timeout

```python
configure_timeout(self, timeout: float) -> TeamConfig
```

设置消息处理超时时间。

**参数**：

- **timeout** (float)：超时时间（秒）。

**返回**：

**TeamConfig**：当前配置实例（支持链式调用）。

---

### configure_concurrency

```python
configure_concurrency(self, max_concurrent: int) -> TeamConfig
```

设置最大并发消息数。

**参数**：

- **max_concurrent** (int)：最大并发消息处理数量。

**返回**：

**TeamConfig**：当前配置实例（支持链式调用）。

---

## class openjiuwen.core.multi_agent.BaseTeam

```python
class openjiuwen.core.multi_agent.BaseTeam(
    card: TeamCard,
    config: Optional[TeamConfig] = None,
    runtime: Optional[TeamRuntime] = None
)
```

智能体团队的抽象基类，定义标准团队接口。`card` 描述团队身份，`config` 控制运行时行为，所有智能体管理均委托给内部的 `TeamRuntime`。子类必须实现 `invoke()` 与 `stream()`。

**参数**：

- **card** (TeamCard)：团队身份卡片，不可为空。
- **config** (TeamConfig, 可选)：运行时配置，未提供时使用默认值。
- **runtime** (TeamRuntime, 可选)：团队运行时实例，未提供时自动创建。

**属性**：

- **card** (TeamCard)：团队身份卡片。
- **config** (TeamConfig)：运行时配置。
- **team_id** (str)：团队标识（派生自 `card.name`）。
- **runtime** (TeamRuntime)：团队运行时实例。

---

### configure

```python
configure(self, config: TeamConfig) -> BaseTeam
```

设置团队运行时配置。

**参数**：

- **config** (TeamConfig)：新的配置对象。

**返回**：

**BaseTeam**：当前团队实例（支持链式调用）。

---

### add_agent

```python
add_agent(
    self,
    card: AgentCard,
    provider: AgentProvider
) -> BaseTeam
```

使用 Card + Provider 模式向团队注册一个智能体。委托给 `runtime.register_agent` 并将卡片追加到 `self.card.agent_cards`。若智能体 ID 已存在则静默跳过；超出 `max_agents` 时抛出异常。

**参数**：

- **card** (AgentCard)：智能体身份卡片（含 `id`）。
- **provider** (AgentProvider)：用于延迟创建实例的智能体工厂。若创建的智能体继承了 `CommunicableAgent`，运行时会自动调用 `bind_runtime()`。

**返回**：

**BaseTeam**：当前团队实例（支持链式调用）。

**异常**：

- 超出 `max_agents` 时抛出 `AGENT_TEAM_ADD_RUNTIME_ERROR`。

---

### remove_agent

```python
remove_agent(
    self,
    agent: Union[str, AgentCard]
) -> BaseTeam
```

从团队中移除一个智能体，清除其卡片注册及运行时中的主题订阅。不会从 `ResourceMgr` 注销（该智能体可能被共享）。

**参数**：

- **agent** (str | AgentCard)：智能体 ID 字符串或 `AgentCard` 实例。

**返回**：

**BaseTeam**：当前团队实例（支持链式调用）。

---

### subscribe

```python
async subscribe(self, agent_id: str, topic: str) -> None
```

将智能体订阅到指定主题（委托给 `runtime.subscribe`）。支持精确匹配和通配符（`*`、`?`）模式。

**参数**：

- **agent_id** (str)：智能体 ID。
- **topic** (str)：主题模式字符串，例如 `"code_events"` 或 `"code_*"`。

---

### unsubscribe

```python
async unsubscribe(self, agent_id: str, topic: str) -> None
```

将智能体从指定主题取消订阅（委托给 `runtime.unsubscribe`）。

**参数**：

- **agent_id** (str)：智能体 ID。
- **topic** (str)：主题模式字符串。

---

### get_agent_card

```python
get_agent_card(self, agent_id: str) -> Optional[AgentCard]
```

根据 ID 获取已注册智能体的 `AgentCard`（委托给 `runtime`）。

**参数**：

- **agent_id** (str)：智能体 ID。

**返回**：

**Optional[AgentCard]**：对应的 `AgentCard`，未找到时返回 `None`。

---

### get_agent_count

```python
get_agent_count(self) -> int
```

返回当前团队中已注册的智能体数量（委托给 `runtime`）。

**返回**：

**int**：已注册的智能体数量。

---

### list_agents

```python
list_agents(self) -> List[str]
```

列出当前团队中所有已注册智能体的 ID（委托给 `runtime`）。

**返回**：

**List[str]**：智能体 ID 列表。

---

### send

```python
async send(
    self,
    message: Any,
    recipient: str,
    sender: str,
    session_id: Optional[str] = None,
    timeout: Optional[float] = None
) -> Any
```

向团队内的指定智能体发送点对点（P2P）消息，并等待响应。`sender` 与 `recipient` 均须已在团队中注册。

**参数**：

- **message** (Any)：消息载荷。
- **recipient** (str)：接收方智能体 ID（须已注册）。
- **sender** (str)：发送方智能体 ID（须已注册，用于追踪）。
- **session_id** (str, 可选)：用于会话连续性的会话 ID。
- **timeout** (float, 可选)：响应超时时间（秒）。

**返回**：

**Any**：接收方智能体的响应结果。

**异常**：

- `sender` 或 `recipient` 未在团队中注册时，抛出 `AGENT_TEAM_AGENT_NOT_FOUND`。

---

### publish

```python
async publish(
    self,
    message: Any,
    topic_id: str,
    sender: str,
    session_id: Optional[str] = None
) -> None
```

向团队内的某个主题发布消息（发布-订阅模式，即发即忘）。所有订阅该主题的智能体将并发接收消息。`sender` 须已在团队中注册。

**参数**：

- **message** (Any)：消息载荷。
- **topic_id** (str)：主题 ID，例如 `"code_events"`、`"task_updates"`。
- **sender** (str)：发送方智能体 ID（须已注册）。
- **session_id** (str, 可选)：会话 ID。

**异常**：

- `sender` 未在团队中注册时，抛出 `AGENT_TEAM_AGENT_NOT_FOUND`。

---

### abstractmethod invoke

```python
async invoke(
    self,
    message: Any,
    session: Optional[Session] = None
) -> Any
```

（抽象方法）以批量模式执行团队任务。子类必须实现此方法。

**参数**：

- **message** (Any)：输入消息对象或字典。
- **session** (Session, 可选)：`AgentTeam` 会话实例。

**返回**：

**Any**：团队的聚合执行结果。

---

### abstractmethod stream

```python
async stream(
    self,
    message: Any,
    session: Optional[Session] = None
) -> AsyncIterator[Any]
```

（抽象方法）以流式模式执行团队任务。子类必须实现此方法。

**参数**：

- **message** (Any)：输入消息对象或字典。
- **session** (Session, 可选)：`AgentTeam` 会话实例。

**返回**：

**AsyncIterator[Any]**：异步迭代器，逐块产出团队执行的流式输出。

---

## class openjiuwen.core.multi_agent.team_runtime.CommunicableAgent

```python
class openjiuwen.core.multi_agent.team_runtime.CommunicableAgent()
```

为智能体添加消息通信能力的混入类。继承此类的智能体可通过 `send()`、`publish()`、`subscribe()` 和 `unsubscribe()` 与团队内其他智能体通信。

运行时绑定由 `TeamRuntime.register_agent` 自动完成，无需手动调用。

**用法**：

```python
class MyAgent(CommunicableAgent, BaseAgent):
    ...
```

---

### bind_runtime

```python
bind_runtime(self, runtime: TeamRuntime, agent_id: str) -> None
```

将 `TeamRuntime` 绑定到当前智能体实例。由 `TeamRuntime.register_agent` 自动调用，通常无需手动调用。

**参数**：

- **runtime** (TeamRuntime)：团队运行时实例。
- **agent_id** (str)：当前智能体的 ID。

---

### is_bound

```python
@property
is_bound(self) -> bool
```

检查当前智能体是否已绑定到运行时。

**返回**：

**bool**：若已以有效值调用过 `bind_runtime`，则返回 `True`。

---

### send

```python
async send(
    self,
    message: Any,
    recipient: str,
    session_id: Optional[str] = None,
    timeout: Optional[float] = None
) -> Any
```

向另一个智能体发送点对点（P2P）消息，并等待响应。智能体须已绑定到运行时。

**参数**：

- **message** (Any)：消息载荷。
- **recipient** (str)：接收方智能体 ID。
- **session_id** (str, 可选)：会话 ID。
- **timeout** (float, 可选)：响应超时时间（秒）。

**返回**：

**Any**：接收方智能体的响应结果。

---

### publish

```python
async publish(
    self,
    message: Any,
    topic_id: str,
    session_id: Optional[str] = None
) -> None
```

向主题发布消息（发布-订阅模式，即发即忘）。智能体须已绑定到运行时。

**参数**：

- **message** (Any)：消息载荷。
- **topic_id** (str)：主题 ID。
- **session_id** (str, 可选)：会话 ID。

---

### subscribe

```python
async subscribe(self, topic: str) -> None
```

订阅主题。当有消息发布到该主题时，此智能体将被调用。支持通配符（`*`、`?`）。智能体须已绑定到运行时。

**参数**：

- **topic** (str)：主题模式字符串。

---

### unsubscribe

```python
async unsubscribe(self, topic: str) -> None
```

取消订阅主题。智能体须已绑定到运行时。

**参数**：

- **topic** (str)：主题模式字符串。

---

## class openjiuwen.core.multi_agent.team_runtime.TeamRuntime

```python
class openjiuwen.core.multi_agent.team_runtime.TeamRuntime(
    config: Optional[RuntimeConfig] = None
)
```

多智能体团队通信的自包含运行时。管理智能体的注册与生命周期，并通过内部 `MessageBus` 提供点对点（P2P）和发布-订阅（Pub-Sub）两种消息模式。可单独使用，也可作为 `BaseTeam` 子类的骨干。

**参数**：

- **config** (RuntimeConfig, 可选)：运行时配置，未提供时使用默认值。

---

### is_running

```python
is_running(self) -> bool
```

检查运行时当前是否正在运行。

**返回**：

**bool**：运行中返回 `True`，否则返回 `False`。

---

### start

```python
async start(self) -> None
```

启动运行时并初始化消息总线后台任务。已在运行时忽略。支持作为异步上下文管理器使用（`async with`）。

---

### stop

```python
async stop(self) -> None
```

停止运行时，关闭消息总线并清理所有资源。未运行时忽略。

---

### register_agent

```python
register_agent(
    self,
    card: AgentCard,
    provider: AgentProvider
) -> None
```

使用 Card + Provider 模式向运行时注册智能体。在本地存储 `AgentCard` 并向 `Runner.ResourceMgr` 注册封装后的 provider。若创建的智能体继承了 `CommunicableAgent`，封装器会自动调用 `bind_runtime()`。

**参数**：

- **card** (AgentCard)：智能体身份卡片（含 `id`）。
- **provider** (AgentProvider)：用于延迟创建实例的智能体工厂函数。

**异常**：

- Runner 模块不可用或注册失败时，抛出 `AGENT_TEAM_ADD_RUNTIME_ERROR`。

---

### unregister_agent

```python
unregister_agent(self, agent_id: str) -> Optional[AgentCard]
```

从运行时移除智能体，清除其本地卡片注册及所有主题订阅。不会从 `ResourceMgr` 注销（该智能体可能被共享）。

**参数**：

- **agent_id** (str)：智能体 ID。

**返回**：

**Optional[AgentCard]**：被移除的 `AgentCard`，未找到时返回 `None`。

---

### has_agent

```python
has_agent(self, agent_id: str) -> bool
```

检查某智能体是否已在运行时注册。

**参数**：

- **agent_id** (str)：智能体 ID。

**返回**：

**bool**：已注册返回 `True`。

---

### get_agent_card

```python
get_agent_card(self, agent_id: str) -> Optional[AgentCard]
```

根据 ID 获取已注册智能体的 `AgentCard`。

**参数**：

- **agent_id** (str)：智能体 ID。

**返回**：

**Optional[AgentCard]**：对应的 `AgentCard`，未找到时返回 `None`。

---

### list_agents

```python
list_agents(self) -> list[str]
```

列出所有已注册智能体的 ID。

**返回**：

**list[str]**：智能体 ID 列表。

---

### get_agent_count

```python
get_agent_count(self) -> int
```

返回已注册的智能体数量。

**返回**：

**int**：智能体数量。

---

### send

```python
async send(
    self,
    message: Any,
    recipient: str,
    sender: str,
    session_id: Optional[str] = None,
    timeout: Optional[float] = None
) -> Any
```

向已注册的智能体发送 P2P 消息，并等待响应。若运行时尚未启动，首次使用时自动启动。

**参数**：

- **message** (Any)：消息载荷。
- **recipient** (str)：接收方智能体 ID（须已注册）。
- **sender** (str)：发送方智能体 ID（用于追踪）。
- **session_id** (str, 可选)：用于按会话隔离主题的会话 ID。
- **timeout** (float, 可选)：响应超时时间（秒）。

**返回**：

**Any**：接收方智能体的响应结果。

**异常**：

- `recipient` 未注册或超时时，抛出 `AGENT_TEAM_EXECUTION_ERROR`。

---

### publish

```python
async publish(
    self,
    message: Any,
    topic_id: str,
    sender: str,
    session_id: Optional[str] = None
) -> None
```

向主题发布消息（发布-订阅模式，即发即忘）。所有匹配的订阅者将并发被调用。若运行时尚未启动，首次使用时自动启动。

**参数**：

- **message** (Any)：消息载荷。
- **topic_id** (str)：主题 ID，例如 `"code_events"`。
- **sender** (str)：发送方智能体 ID（用于追踪）。
- **session_id** (str, 可选)：用于主题隔离的会话 ID。

**异常**：

- 发布失败时抛出 `AGENT_TEAM_EXECUTION_ERROR`。

---

### subscribe

```python
async subscribe(self, agent_id: str, topic: str) -> None
```

将智能体订阅到某个主题模式。支持精确匹配和通配符（`*`、`?`）模式。

**参数**：

- **agent_id** (str)：智能体 ID。
- **topic** (str)：主题模式字符串，例如 `"code_events"` 或 `"code_*"`。

---

### unsubscribe

```python
async unsubscribe(self, agent_id: str, topic: str) -> None
```

将智能体从某个主题模式取消订阅。

**参数**：

- **agent_id** (str)：智能体 ID。
- **topic** (str)：主题模式字符串。

---

### list_subscriptions

```python
list_subscriptions(self, agent_id: Optional[str] = None) -> dict[str, Any]
```

查询当前订阅状态，用于调试和内省。

**参数**：

- **agent_id** (str, 可选)：指定时仅返回该智能体的订阅；不传时返回所有订阅。

**返回**：

**dict[str, Any]**：订阅信息字典。

---

### get_subscription_count

```python
get_subscription_count(self) -> int
```

返回当前活跃的主题订阅总数。

**返回**：

**int**：订阅总数。

---

## class openjiuwen.core.multi_agent.team_runtime.RuntimeConfig

```python
class openjiuwen.core.multi_agent.team_runtime.RuntimeConfig(
    team_id: str = "default",
    message_bus: Optional[MessageBusConfig] = None
)
```

`TeamRuntime` 的配置对象。

**属性**：

- **team_id** (str)：用于主题隔离的团队 ID。默认值：`"default"`。
- **message_bus** (MessageBusConfig, 可选)：消息总线配置，未提供时使用默认值。

---

## class openjiuwen.core.multi_agent.team_runtime.MessageBusConfig

```python
class openjiuwen.core.multi_agent.team_runtime.MessageBusConfig(
    max_queue_size: int = 1000,
    process_timeout: Optional[float] = 100000,
    team_id: Optional[str] = None
)
```

消息总线配置对象，控制消息队列容量、处理超时及团队隔离。

**属性**：

- **max_queue_size** (int)：消息队列最大容量。默认值：`1000`。
- **process_timeout** (float, 可选)：消息处理超时时间（秒）。默认值：`100000`。
- **team_id** (str, 可选)：用于消息主题命名与隔离的团队 ID。默认值：`None`。

---

## class openjiuwen.core.multi_agent.team_runtime.MessageEnvelope

```python
@dataclass(frozen=True)
class openjiuwen.core.multi_agent.team_runtime.MessageEnvelope(
    message_id: str,
    message: Any,
    sender: Optional[str] = None,
    recipient: Optional[str] = None,
    topic_id: Optional[str] = None,
    session_id: Optional[str] = None,
    metadata: dict = {}
)
```

不可变的消息路由信封，携带单条消息的完整路由元数据（`frozen=True`）。

**属性**：

- **message_id** (str)：消息唯一标识。
- **message** (Any)：消息载荷。
- **sender** (str, 可选)：发送方智能体 ID。
- **recipient** (str, 可选)：接收方智能体 ID，P2P 消息时设置。
- **topic_id** (str, 可选)：主题 ID，Pub-Sub 消息时设置。
- **session_id** (str, 可选)：会话 ID。
- **metadata** (dict)：附加元数据。

---

### is_p2p

```python
is_p2p(self) -> bool
```

检查是否为点对点（P2P）消息。

**返回**：

**bool**：`recipient` 不为 `None` 时返回 `True`。

---

### is_pubsub

```python
is_pubsub(self) -> bool
```

检查是否为发布-订阅（Pub-Sub）消息。

**返回**：

**bool**：`topic_id` 不为 `None` 时返回 `True`。

---

## function openjiuwen.core.multi_agent.teams.make_team_session

```python
make_team_session(
    card: TeamCard,
    message: Any
) -> Session
```

创建一个新的 `AgentTeam` 会话对象。当 `message` 为字典且包含 `conversation_id` 键时，复用该值作为会话 ID；否则自动生成 UUID。

**参数**：

- **card** (TeamCard)：所属团队的身份卡片，用于提供 `team_id`。
- **message** (Any)：用户输入，字典或字符串。

**返回**：

**Session**：绑定到当前团队的新会话实例。

---

## function openjiuwen.core.multi_agent.teams.standalone_invoke_context

```python
@asynccontextmanager
async def standalone_invoke_context(
    runtime: TeamRuntime,
    card: TeamCard,
    message: Any,
    session: Optional[Session] = None
) -> AsyncIterator[Tuple[Session, str]]
```

用于 `invoke()` 的异步上下文管理器，负责完整的会话生命周期管理。

- **独立模式**（`session=None`）：自动创建会话、调用 `pre_run()`、绑定到运行时，退出时解绑并调用 `post_run()`。
- **Runner 模式**（传入已有 `Session`）：直接透传，不执行任何生命周期操作。

**参数**：

- **runtime** (TeamRuntime)：所属团队的运行时实例。
- **card** (TeamCard)：所属团队的身份卡片。
- **message** (Any)：用户输入，字典或字符串。
- **session** (Session, 可选)：Runner 传入的外部会话，`None` 时进入独立模式。

**产出（yield）**：

**Tuple[Session, str]**：`(team_session, session_id)` 元组。

---

## function openjiuwen.core.multi_agent.teams.standalone_stream_context

```python
async def standalone_stream_context(
    runtime: TeamRuntime,
    card: TeamCard,
    message: Any,
    run_coro: Callable[[Session, str], Awaitable[None]],
    session: Optional[Session] = None
) -> AsyncIterator[Any]
```

用于 `stream()` 的异步生成器，负责完整的会话生命周期管理。在后台 Task 中运行 `run_coro`，同时向调用方逐块产出流式输出。

- **独立模式**（`session=None`）：创建会话并绑定运行时，后台 Task 结束时自动解绑并调用 `post_run()`。
- **Runner 模式**（传入已有 `Session`）：后台 Task 结束时调用 `session.close_stream()` 通知流结束。

**参数**：

- **runtime** (TeamRuntime)：所属团队的运行时实例。
- **card** (TeamCard)：所属团队的身份卡片。
- **message** (Any)：用户输入，字典或字符串。
- **run_coro** (Callable[[Session, str], Awaitable[None]])：团队实际工作协程，签名为 `async (session, session_id) -> None`。
- **session** (Session, 可选)：Runner 传入的外部会话，`None` 时进入独立模式。

**产出（yield）**：

**Any**：由 `run_coro` 写入流的输出块。

---

## class openjiuwen.core.multi_agent.teams.handoff.HandoffRoute

```python
@dataclass(frozen=True)
class openjiuwen.core.multi_agent.teams.handoff.HandoffRoute(
    source: str,
    target: str
)
```

不可变的路由规则，声明 `source` 智能体可以将控制权移交给 `target` 智能体（`frozen=True`）。

**属性**：

- **source** (str)：源智能体 ID。
- **target** (str)：目标智能体 ID。

---

## class openjiuwen.core.multi_agent.teams.handoff.HandoffConfig

```python
@dataclass
class openjiuwen.core.multi_agent.teams.handoff.HandoffConfig(
    start_agent: Optional[AgentCard] = None,
    max_handoffs: int = 10,
    routes: List[HandoffRoute] = field(default_factory=list),
    termination_condition: Optional[Callable] = None
)
```

`HandoffTeam` 的编排参数配置。

**属性**：

- **start_agent** (AgentCard, 可选)：第一个执行的智能体的 `AgentCard`。默认值：`None`，未提供时使用第一个通过 `add_agent()` 添加的智能体。
- **max_handoffs** (int)：初始跳之后允许的最大移交次数。例如 `max_handoffs=2` 允许 A→B→C，但阻止第四跳。默认值：`10`。
- **routes** (List[HandoffRoute])：显式路由规则列表。为空时任意智能体均可移交给其他任意智能体（全网格模式），同时控制注入哪些 `HandoffTool`。默认值：`[]`。
- **termination_condition** (Callable, 可选)：可选的终止条件，签名为 `(HandoffOrchestrator) -> bool`，支持同步或异步。返回 `True` 时触发提前终止。默认值：`None`。

---

## class openjiuwen.core.multi_agent.teams.handoff.HandoffTeamConfig

```python
class openjiuwen.core.multi_agent.teams.handoff.HandoffTeamConfig(
    handoff: HandoffConfig = HandoffConfig(),
    max_agents: int = 10,
    max_concurrent_messages: int = 100,
    message_timeout: float = 30.0
)
```

`HandoffTeam` 的完整配置，继承自 `TeamConfig`，扩展了交接编排参数。支持额外字段（`extra='allow'`）和任意类型（`arbitrary_types_allowed=True`）。

**属性**：

- **handoff** (HandoffConfig)：交接编排配置。默认值：`HandoffConfig()`。
- 继承 `TeamConfig` 的所有属性（`max_agents`、`max_concurrent_messages`、`message_timeout`）及其链式配置方法。

---

## class openjiuwen.core.multi_agent.teams.handoff.HandoffTeam

```python
class openjiuwen.core.multi_agent.teams.handoff.HandoffTeam(
    card: TeamCard,
    config: Optional[HandoffTeamConfig] = None
)
```

基于事件驱动的交接式多智能体团队，继承自 `BaseTeam`。智能体通过有序的交接协作：每个智能体中的 LLM 决定完成任务还是通过调用注入的 `transfer_to_{agent}` 工具将控制权移交给下一个智能体。

**参数**：

- **card** (TeamCard)：团队身份卡片。
- **config** (HandoffTeamConfig, 可选)：团队配置。未提供时使用 `HandoffTeamConfig()` 默认值。

---

### add_agent

```python
add_agent(
    self,
    card: AgentCard,
    provider: AgentProvider
) -> HandoffTeam
```

向团队注册一个智能体。若该智能体 ID 已存在则静默跳过。添加后会重置内部初始化状态，以便在下次调用时重新配置交接路由。

**参数**：

- **card** (AgentCard)：智能体身份卡片。
- **provider** (AgentProvider)：用于延迟创建智能体实例的工厂函数。

**返回**：

**HandoffTeam**：当前团队实例（支持链式调用）。

**异常**：

- 智能体数量超过 `max_agents` 时抛出 `AGENT_TEAM_ADD_RUNTIME_ERROR`。

---

### invoke

```python
async invoke(
    self,
    message: Any,
    session: Optional[Session] = None
) -> Any
```

以批量模式运行交接链，返回最终结果。

**参数**：

- **message** (Any)：用户输入，字典或字符串。
- **session** (Session, 可选)：Runner 传入的会话，`None` 时自动创建独立会话。

**返回**：

**Any**：交接链中最后一个智能体返回的最终结果。

---

### stream

```python
async stream(
    self,
    message: Any,
    session: Optional[Session] = None
) -> AsyncIterator[Any]
```

以流式模式运行交接链，实时产出输出块。

**参数**：

- **message** (Any)：用户输入，字典或字符串。
- **session** (Session, 可选)：Runner 传入的会话，`None` 时自动创建独立会话。

**产出（yield）**：

**Any**：交接链中各智能体执行过程中产出的输出块。

---

## class openjiuwen.core.multi_agent.teams.handoff.HandoffOrchestrator

```python
class openjiuwen.core.multi_agent.teams.handoff.HandoffOrchestrator(
    start_agent_id: str,
    registered_agents: List[str],
    config: Optional[HandoffConfig] = None
)
```

每次调用的交接状态协调器，由 `HandoffTeam` 创建和持有。负责追踪当前智能体、计数移交次数、执行路由决策，并通过 `done_future` 传递最终结果。使用 `restore_from_session` 可恢复被中断的会话。

**参数**：

- **start_agent_id** (str)：第一个执行的智能体 ID。
- **registered_agents** (List[str])：团队中所有已注册智能体的 ID 列表。
- **config** (HandoffConfig, 可选)：包含路由规则、最大移交次数及终止条件的配置。`None` 时使用默认值。

**属性**：

- **handoff_count** (int, 只读)：当前会话中已完成的移交次数。
- **current_agent_id** (str, 只读)：将执行下一跳的智能体 ID。
- **done_future** (asyncio.Future, 只读)：交接链的完成 Future，在运行中的事件循环内懒创建。

---

### build_route_graph

```python
@staticmethod
build_route_graph(
    agents: List[str],
    routes: List[HandoffRoute]
) -> Dict[str, Set[str]]
```

构建允许移交路由的邻接图。

**参数**：

- **agents** (List[str])：团队中所有智能体 ID 的列表。
- **routes** (List[HandoffRoute])：显式路由规则列表。为空时生成全网格（任意智能体可互相移交）。

**返回**：

**Dict[str, Set[str]]**：将每个智能体 ID 映射到其允许移交目标集合的字典。

---

### request_handoff

```python
async request_handoff(
    self,
    target_id: str,
    reason: Optional[str] = None
) -> bool
```

尝试批准向 `target_id` 的移交请求。若批准则更新内部状态；若拒绝（达到上限、触发终止条件或路由不被允许）则不更新状态。

**参数**：

- **target_id** (str)：目标智能体 ID。
- **reason** (str, 可选)：可选的原因字符串，用于日志记录。

**返回**：

**bool**：批准时返回 `True`，拒绝时返回 `False`。

---

### complete

```python
async complete(self, result: Any) -> None
```

以 `result` 解析 `done_future`，结束交接链。幂等操作，多次调用以第一次为准。

**参数**：

- **result** (Any)：返回给调用方的最终结果。

---

### error

```python
async error(self, exception: Exception) -> None
```

以 `exception` 拒绝 `done_future`，将错误传播给调用方。幂等操作，多次调用以第一次为准。

**参数**：

- **exception** (Exception)：要在 `await` 处抛出的异常。

---

### save_to_session

```python
save_to_session(self, session: Session) -> None
```

将协调器状态持久化到 `session`，用于中断/恢复支持。

**参数**：

- **session** (Session)：要写入状态的团队会话。

---

### restore_from_session

```python
@classmethod
restore_from_session(
    cls,
    session: Session,
    start_agent_id: str,
    registered_agents: List[str],
    config: Optional[HandoffConfig] = None
) -> HandoffOrchestrator
```

从先前中断的会话中恢复状态，创建协调器实例。若会话中无历史状态，则以 `start_agent_id` 为起点创建全新协调器。

**参数**：

- **session** (Session)：要读取状态的团队会话。
- **start_agent_id** (str)：无历史状态时使用的起始智能体 ID。
- **registered_agents** (List[str])：团队中所有智能体 ID 的列表。
- **config** (HandoffConfig, 可选)：包含路由规则、最大移交次数及终止条件的配置。

**返回**：

**HandoffOrchestrator**：从会话状态恢复的协调器实例，或全新创建的实例。

---

## class openjiuwen.core.multi_agent.teams.handoff.HandoffSignal

```python
@dataclass(frozen=True)
class openjiuwen.core.multi_agent.teams.handoff.HandoffSignal(
    target: str,
    message: Optional[str] = None,
    reason: Optional[str] = None
)
```

由 `extract_handoff_signal` 生成的不可变交接指令（`frozen=True`）。

**属性**：

- **target** (str)：目标智能体 ID。
- **message** (str, 可选)：转发给目标智能体的上下文消息。空字符串时置为 `None`。
- **reason** (str, 可选)：交接原因的人类可读描述。空字符串时置为 `None`。

---

## function openjiuwen.core.multi_agent.teams.handoff.extract_handoff_signal

```python
extract_handoff_signal(result: Any) -> Optional[HandoffSignal]
```

若 `result` 中包含交接指令则返回 `HandoffSignal`，否则返回 `None`。

在 `result` 本身或其 `output`、`result`、`content` 子键中搜索 `__handoff_to__` 键。目标值必须为非空字符串，否则返回 `None`。

**参数**：

- **result** (Any)：要检查的智能体返回值。

**返回**：

**Optional[HandoffSignal]**：找到有效交接指令时返回 `HandoffSignal`，否则返回 `None`。

---

## class openjiuwen.core.multi_agent.teams.handoff.TeamInterruptSignal

```python
@dataclass
class openjiuwen.core.multi_agent.teams.handoff.TeamInterruptSignal(
    result: Any,
    message: Optional[str] = None
)
```

暂停交接链并持久化状态以供后续恢复的中断信号。

**属性**：

- **result** (Any)：返回给调用方的中断载荷，必须包含 `result_type='interrupt'`。
- **message** (str, 可选)：中断原因的人类可读描述。

---

## function openjiuwen.core.multi_agent.teams.handoff.extract_interrupt_signal

```python
extract_interrupt_signal(
    result: Any = None,
    exc: Optional[Exception] = None
) -> Optional[TeamInterruptSignal]
```

从智能体返回值或异常中提取 `TeamInterruptSignal`。

- 当 `result` 为字典且 `result.get('result_type') == 'interrupt'` 时识别为中断。
- 当 `exc` 为 `AgentInterrupt` 实例时识别为中断。
- `result` 优先于 `exc`：`result` 非中断时才检查 `exc`。

**参数**：

- **result** (Any, 可选)：要检查的智能体返回值。
- **exc** (Exception, 可选)：要检查的异常。

**返回**：

**Optional[TeamInterruptSignal]**：检测到中断时返回 `TeamInterruptSignal`，否则返回 `None`。

---

## function openjiuwen.core.multi_agent.teams.handoff.flush_team_session

```python
async flush_team_session(session: Optional[Session]) -> None
```

中断后对团队会话调用 `post_run()` 以尽力完成清理。尽力执行：`post_run()` 失败时仅记录警告，不向调用方传播异常，确保中断结果始终能正常返回。

**参数**：

- **session** (Session, 可选)：要刷新的团队会话。为 `None` 时直接返回，不执行任何操作。

---


---

## class openjiuwen.core.multi_agent.teams.handoff.HandoffTool

```python
class openjiuwen.core.multi_agent.teams.handoff.HandoffTool(
    target_id: str,
    target_description: str = ""
)
```

交接工具，由 `HandoffTeam` 自动注入到每个智能体的 `AbilityManager` 中，用于触发智能体间的控制权移交。向 LLM 暴露的工具名称为 `transfer_to_{target_id}`。继承自 `Tool`。

**参数**：

- **target_id** (str)：目标智能体的 ID。
- **target_description** (str, 可选)：目标智能体的描述，附加到展示给 LLM 的工具描述中。默认值：`""`。

---

### invoke

```python
async invoke(self, inputs: Any, **kwargs: Any) -> dict
```

返回交接信号载荷字典，供 `extract_handoff_signal` 消费。

**参数**：

- **inputs** (Any)：LLM 传入的工具参数，支持含 `reason` / `message` 键的字典或 JSON 字符串。

**返回**：

**dict**：包含 `__handoff_to__`、`__handoff_message__`、`__handoff_reason__` 键的字典。

---

### stream

```python
async stream(self, inputs: Any, **kwargs: Any) -> AsyncIterator[dict]
```

流式变体，产出单个 `invoke()` 的结果。

**参数**：

- **inputs** (Any)：LLM 传入的工具参数。

**产出（yield）**：

**dict**：与 `invoke()` 返回值相同的交接信号载荷字典。

---

## class openjiuwen.core.multi_agent.teams.handoff.ContainerAgent

```python
class openjiuwen.core.multi_agent.teams.handoff.ContainerAgent(
    target_card: AgentCard,
    target_provider: Callable[[], BaseAgent],
    allowed_targets: List[str],
    coordinator_lookup: Optional[Callable[[str], HandoffOrchestrator]] = None
)
```

`HandoffTeam` 为每个注册智能体自动创建的包装智能体。负责懒加载目标智能体实例、注入 `HandoffTool`、将执行历史写入团队会话上下文，以及处理交接（handoff）和中断（interrupt）信号。继承自 `CommunicableAgent` 和 `BaseAgent`。

**参数**：

- **target_card** (AgentCard)：被包装智能体的身份卡片。
- **target_provider** (Callable[[], BaseAgent])：惰性工厂函数，首次调用时创建目标智能体实例。
- **allowed_targets** (List[str])：允许移交的目标智能体 ID 列表，用于注入对应的 `HandoffTool`。
- **coordinator_lookup** (Callable[[str], HandoffOrchestrator], 可选)：根据 `session_id` 查找对应 `HandoffOrchestrator` 的回调函数。

---

### invoke

```python
async invoke(self, inputs: HandoffRequest, session: Optional[Session] = None) -> dict
```

执行目标智能体并处理其输出。根据输出结果决定完成编排、发起下一跳交接或传播中断信号。

**参数**：

- **inputs** (HandoffRequest)：驱动消息，包含用户输入、历史记录和团队会话。
- **session** (Session, 可选)：当前智能体会话，通常不直接传入。

**返回**：

**dict**：始终返回空字典 `{}`，编排结果通过 `HandoffOrchestrator` 传递。

**异常**：

- 当 `inputs` 不是 `HandoffRequest` 实例时，静默返回 `{}`。
- 当 `coordinator_lookup` 返回 `None` 时，抛出 `AGENT_TEAM_EXECUTION_ERROR`。
- 当目标智能体执行抛出非中断异常时，抛出 `AGENT_TEAM_EXECUTION_ERROR`。

---

### stream

```python
async stream(self, inputs: HandoffRequest, session: Optional[Session] = None, **kwargs) -> AsyncIterator[dict]
```

流式变体，内部调用 `invoke()` 并产出其结果。

**参数**：

- **inputs** (HandoffRequest)：驱动消息。
- **session** (Session, 可选)：当前智能体会话。

**产出（yield）**：

**dict**：`invoke()` 的返回值（始终为 `{}`）。

---

## class openjiuwen.core.multi_agent.teams.handoff.HandoffRequest

```python
@dataclass
class openjiuwen.core.multi_agent.teams.handoff.HandoffRequest(
    input_message: Any,
    history: List[dict] = field(default_factory=list),
    session: Optional[Session] = None
)
```

`HandoffTeam` 内部用于驱动跨智能体交接的消息对象，发布到 `container_{agent_id}` 主题。每一跳交接均携带累积的历史记录和团队会话引用。

**属性**：

- **input_message** (Any)：传递给下一个智能体的用户输入或中间输入。
- **history** (List[dict])：跨跳累积的交接历史，每项包含 `agent` 和 `output` 键。默认值：`[]`。
- **session** (Session, 可选)：用于流式 I/O 的团队会话。单元测试场景下为 `None`。

---

### session_id

```python
@property
session_id(self) -> str
```

从附加的会话派生会话 ID；无会话时返回空字符串。

**返回**：

**str**：会话 ID 字符串，或无会话时的空字符串 `""`。

---

## class openjiuwen.core.multi_agent.teams.hierarchical_tools.HierarchicalTeamConfig

```python
class openjiuwen.core.multi_agent.teams.hierarchical_tools.HierarchicalTeamConfig(
    root_agent: AgentCard,
    max_agents: int = 10,
    max_concurrent_messages: int = 100,
    message_timeout: float = 30.0
)
```

`HierarchicalTeam`（Agents-as-Tools 模式）的配置，继承自 `TeamConfig`。

**属性**：

- **root_agent** (AgentCard)：顶层入口智能体的 `AgentCard`，必填。
- 继承 `TeamConfig` 的所有属性（`max_agents`、`max_concurrent_messages`、`message_timeout`）及其链式配置方法。

---

## class openjiuwen.core.multi_agent.teams.hierarchical_tools.HierarchicalTeam

```python
class openjiuwen.core.multi_agent.teams.hierarchical_tools.HierarchicalTeam(
    card: TeamCard,
    config: HierarchicalTeamConfig,
    runtime: Optional[TeamRuntime] = None
)
```

Agents-as-Tools 层级多智能体团队，继承自 `BaseTeam`。智能体通过每个智能体的 `ability_manager` 分层组合，根智能体作为执行入口。

**参数**：

- **card** (TeamCard)：团队身份卡片。
- **config** (HierarchicalTeamConfig)：团队配置，必须包含 `root_agent`。
- **runtime** (TeamRuntime, 可选)：团队运行时实例，未提供时自动创建。

---

### add_agent

```python
add_agent(
    self,
    card: AgentCard,
    provider: AgentProvider,
    parent_agent_id: Optional[str] = None
) -> HierarchicalTeam
```

向团队添加一个智能体。若提供 `parent_agent_id`，则在首次运行前将该智能体的卡片作为工具注册到父智能体的 `ability_manager` 中。

**参数**：

- **card** (AgentCard)：智能体身份卡片。
- **provider** (AgentProvider)：用于延迟创建智能体实例的工厂函数。
- **parent_agent_id** (str, 可选)：父智能体 ID，指定后将当前智能体注册为该父智能体的子工具。

**返回**：

**HierarchicalTeam**：当前团队实例（支持链式调用）。

**异常**：

- 超出 `max_agents` 时抛出 `AGENT_TEAM_ADD_RUNTIME_ERROR`。

---

### invoke

```python
async invoke(
    self,
    inputs: Any,
    session: Optional[Session] = None
) -> Any
```

从根智能体开始运行团队，返回最终结果。

**参数**：

- **inputs** (Any)：输入消息或字典。
- **session** (Session, 可选)：外部传入的 `AgentTeamSession`，`None` 时进入独立模式。

**返回**：

**Any**：根智能体返回的最终结果。

**异常**：

- 根智能体未在运行时注册时抛出 `AGENT_TEAM_EXECUTION_ERROR`。

---

### stream

```python
async stream(
    self,
    inputs: Any,
    session: Optional[Session] = None
) -> AsyncGenerator[Any, None]
```

从根智能体开始运行团队，以流式方式产出输出块。

**参数**：

- **inputs** (Any)：输入消息或字典。
- **session** (Session, 可选)：外部传入的 `AgentTeamSession`，`None` 时进入独立模式。

**产出（yield）**：

**Any**：流式输出块。

**异常**：

- 根智能体未在运行时注册时抛出 `AGENT_TEAM_EXECUTION_ERROR`。

---

## class openjiuwen.core.multi_agent.teams.hierarchical_msgbus.HierarchicalTeamConfig

```python
class openjiuwen.core.multi_agent.teams.hierarchical_msgbus.HierarchicalTeamConfig(
    supervisor_agent: AgentCard,
    max_agents: int = 10,
    max_concurrent_messages: int = 100,
    message_timeout: float = 30.0
)
```

`HierarchicalTeam`（P2P MessageBus 模式）的配置，继承自 `TeamConfig`。

**属性**：

- **supervisor_agent** (AgentCard)：顶层监督者智能体的 `AgentCard`，必填。
- 继承 `TeamConfig` 的所有属性（`max_agents`、`max_concurrent_messages`、`message_timeout`）及其链式配置方法。

---

## class openjiuwen.core.multi_agent.teams.hierarchical_msgbus.HierarchicalTeam

```python
class openjiuwen.core.multi_agent.teams.hierarchical_msgbus.HierarchicalTeam(
    card: TeamCard,
    config: HierarchicalTeamConfig
)
```

由监督者智能体驱动的层级多智能体团队，继承自 `BaseTeam`。监督者通过 `P2PAbilityManager` 经消息总线将任务分发给子智能体。

**参数**：

- **card** (TeamCard)：团队身份卡片。
- **config** (HierarchicalTeamConfig)：团队配置，必须包含 `supervisor_agent`。

---

### add_agent

```python
add_agent(
    self,
    card: AgentCard,
    provider: AgentProvider
) -> HierarchicalTeam
```

向团队注册一个智能体（监督者或子智能体）。注册监督者时会记录 info 日志。

**参数**：

- **card** (AgentCard)：智能体身份卡片。
- **provider** (AgentProvider)：用于延迟创建智能体实例的工厂函数。

**返回**：

**HierarchicalTeam**：当前团队实例（支持链式调用）。

---

### invoke

```python
async invoke(
    self,
    message: Any,
    session: Optional[Session] = None
) -> Any
```

运行监督者智能体，返回最终结果。

**参数**：

- **message** (Any)：用户输入，字典或字符串。
- **session** (Session, 可选)：Runner 传入的会话，`None` 时自动创建独立会话。

**返回**：

**Any**：监督者智能体返回的最终结果。

**异常**：

- 监督者未在运行时注册时抛出 `AGENT_TEAM_EXECUTION_ERROR`。

---

### stream

```python
async stream(
    self,
    message: Any,
    session: Optional[Session] = None
) -> AsyncIterator[Any]
```

运行监督者智能体，以流式方式产出输出块。

**参数**：

- **message** (Any)：用户输入，字典或字符串。
- **session** (Session, 可选)：Runner 传入的会话，`None` 时自动创建独立会话。

**产出（yield）**：

**Any**：监督者或子智能体在执行过程中产出的输出块。

**异常**：

- 监督者未在运行时注册时抛出 `AGENT_TEAM_EXECUTION_ERROR`。

---

## class openjiuwen.core.multi_agent.teams.hierarchical_msgbus.SupervisorAgent

```python
class openjiuwen.core.multi_agent.teams.hierarchical_msgbus.SupervisorAgent(
    card: AgentCard,
    config: Optional[ReActAgentConfig] = None,
    max_parallel_sub_agents: int = 10
)
```

`HierarchicalTeam`（P2P MessageBus 模式）的默认内置监督者智能体，同时继承 `CommunicableAgent`（P2P 通信）和 `ReActAgent`（ReAct 推理循环）。AgentCard 工具调用通过 `P2PAbilityManager` 路由；其他工具类型由基类正常处理。

**参数**：

- **card** (AgentCard)：监督者智能体的身份卡片。
- **config** (ReActAgentConfig, 可选)：ReAct 智能体配置，未提供时使用默认值。
- **max_parallel_sub_agents** (int)：最大并行子智能体分发数量。默认值：`10`。

---

### register_sub_agent_card

```python
register_sub_agent_card(self, card: AgentCard) -> None
```

将子智能体卡片注册为 LLM 可调用的工具。

**参数**：

- **card** (AgentCard)：子智能体的身份卡片。

---

### configure

```python
configure(self, config: Any) -> SupervisorAgent
```

应用 `ReActAgentConfig` 配置；对其他配置类型为空操作（no-op），始终返回 `self`。

**参数**：

- **config** (Any)：配置对象。传入 `ReActAgentConfig` 时生效，其他类型忽略。

**返回**：

**SupervisorAgent**：当前实例（支持链式调用）。

---

### create

```python
@classmethod
create(
    cls,
    agents: List[AgentCard],
    *,
    model_client_config: Any,
    model_request_config: Any,
    agent_card: AgentCard,
    system_prompt: str,
    max_iterations: int = 5,
    max_parallel_sub_agents: int = 10
) -> Tuple[AgentCard, AgentProvider]
```

创建预加载了子智能体卡片的 `SupervisorAgent`，返回与 `HierarchicalTeam.add_agent()` 兼容的 `(agent_card, provider)` 元组。

**参数**：

- **agents** (List[AgentCard])：对该监督者可见的子智能体卡片列表，不可为空。
- **model_client_config** (Any)：LLM 客户端配置。
- **model_request_config** (Any)：LLM 模型及请求配置。
- **agent_card** (AgentCard)：监督者智能体的身份卡片。
- **system_prompt** (str)：监督者系统提示词。
- **max_iterations** (int)：最大 ReAct 迭代次数。默认值：`5`。
- **max_parallel_sub_agents** (int)：最大并行子智能体分发数量。默认值：`10`。

**返回**：

**Tuple[AgentCard, AgentProvider]**：`(agent_card, provider)` 元组，其中 `provider` 为懒创建监督者实例的工厂函数。

**异常**：

- `agents` 为空时抛出 `AGENT_TEAM_CREATE_RUNTIME_ERROR`。
- `agents` 中存在非 `AgentCard` 条目时抛出 `AGENT_TEAM_CREATE_RUNTIME_ERROR`。

---

## class openjiuwen.core.multi_agent.teams.hierarchical_msgbus.P2PAbilityManager

```python
class openjiuwen.core.multi_agent.teams.hierarchical_msgbus.P2PAbilityManager(
    supervisor: CommunicableAgent,
    max_parallel_sub_agents: int = 10
)
```

将 AgentCard 工具调用通过 `TeamRuntime` P2P `send()` 路由的 `AbilityManager`。AgentCard 调用并行分发，受 `max_parallel_sub_agents` 限制；其他工具类型透传给基类 `execute()` 处理。

**参数**：

- **supervisor** (CommunicableAgent)：用于 P2P 分发的监督者智能体实例，其 `send()` 方法将被调用。
- **max_parallel_sub_agents** (int)：每次 `execute()` 调用中最大并发 AgentCard 分发数量，最小值为 `1`。默认值：`10`。

---

### add

```python
add(self, card: AgentCard) -> AddAbilityResult
```

将子智能体卡片注册为可分发的工具。

**参数**：

- **card** (AgentCard)：子智能体的身份卡片。

**返回**：

**AddAbilityResult**：注册结果，`added=True` 表示新注册，`added=False` 表示已存在（重复添加为空操作）。

---

### execute

```python
async execute(
    self,
    ctx: AgentCallbackContext,
    tool_call: Union[ToolCall, List[ToolCall]],
    session: Session,
    tag: Any = None
) -> List[Tuple[Any, ToolMessage]]
```

执行工具调用：AgentCard 调用通过 P2P 并行分发，其他调用委托给基类处理。结果按原始调用顺序返回。

**参数**：

- **ctx** (AgentCallbackContext)：工具调用生命周期钩子的回调上下文。
- **tool_call** (ToolCall | List[ToolCall])：LLM 产出的单个或多个工具调用。
- **session** (Session)：当前智能体会话。
- **tag** (Any, 可选)：透传给基类的可选资源标签。

**返回**：

**List[Tuple[Any, ToolMessage]]**：按原始调用顺序排列的 `(result, ToolMessage)` 元组列表。AgentCard 调用失败时返回 `(None, error_ToolMessage)`。  
