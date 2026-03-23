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
