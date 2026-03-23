# openjiuwen.core.multi_agent

## class openjiuwen.core.multi_agent.Session

```python
class openjiuwen.core.multi_agent.Session(
    session_id: str = None,
    envs: dict[str, Any] = None,
    team_id: str = "agent_team"
)
```

The core runtime session for `AgentTeam` execution. Manages sessions in multi-agent team scenarios, providing state access, stream output writing, and sub-agent session creation.

**Parameters**:

- **session_id** (str, optional): Unique session identifier. Default: `None`. A UUID is generated automatically if not provided.
- **envs** (dict[str, Any], optional): Environment variables used during `AgentTeam` execution. Default: `None`.
- **team_id** (str, optional): Team identifier. Default: `"agent_team"`.

---

### get_session_id

```python
get_session_id(self) -> str
```

Returns the unique session identifier for the current `AgentTeam` execution.

**Returns**:

**str**: The unique session identifier string.

---

### get_env

```python
get_env(self, key: str, default: Any = None) -> Any
```

Retrieves the value of a specific environment variable configured for the current `AgentTeam` execution.

**Parameters**:

- **key** (str): The environment variable key.
- **default** (Any): The default value if the key does not exist.

**Returns**:

**Any**: The value for the given key, or `default` if not found.

---

### get_team_id

```python
get_team_id(self) -> str
```

Returns the team identifier associated with the current session.

**Returns**:

**str**: The team identifier string.

---

### get_envs

```python
get_envs(self) -> dict
```

Returns all environment variables configured for the current `AgentTeam` execution.

**Returns**:

**dict**: A dictionary of all environment variable key-value pairs.

---

### update_state

```python
update_state(self, data: dict) -> None
```

Merges the provided key-value pairs into the session's global state store.

**Parameters**:

- **data** (dict): State key-value pairs to update.

---

### get_state

```python
get_state(self, key=None) -> Any
```

Reads the session's global state.

**Parameters**:

- **key** (str, optional): If specified, returns the value for that key; if omitted, returns the full state dictionary.

**Returns**:

**Any**: The value for the specified key, or the full state dictionary.

---

### dump_state

```python
dump_state(self) -> dict
```

Exports a complete snapshot of the current session state.

**Returns**:

**dict**: The full session state as a dictionary.

---

### write_stream

```python
async write_stream(self, data: dict | OutputSchema) -> None
```

Writes a data frame to the team session's primary output stream, pushing execution progress or results to the caller in real time. Automatically appends `source_team_id` metadata.

**Parameters**:

- **data** (dict | OutputSchema): Output data to write. Supports dict or `OutputSchema` objects.

---

### write_custom_stream

```python
async write_custom_stream(self, data: dict) -> None
```

Writes a data frame to the custom stream channel, for pushing non-standard custom events or progress information.

**Parameters**:

- **data** (dict): Custom data to write.

---

### stream_iterator

```python
stream_iterator(self) -> AsyncIterator
```

Returns the async stream output iterator for the team session, allowing callers to consume output chunk by chunk.

**Returns**:

**AsyncIterator**: An async-iterable output stream.

---

### close_stream

```python
async close_stream(self) -> None
```

Closes the session's stream output channel, signalling to consumers that the stream has ended. Typically called in the `finally` block of a `stream()` implementation.

---

### create_agent_session

```python
create_agent_session(
    self,
    card: AgentCard | None = None,
    agent_id: str | None = None
) -> AgentSession
```

Creates an independent `AgentSession` for a specified sub-agent based on the current team session. The sub-agent shares the same stream output channel and session context.

**Parameters**:

- **card** (AgentCard, optional): The sub-agent's `AgentCard`. Either `card` or `agent_id` must be provided; if both are omitted, an `AgentCard` is constructed automatically using `agent_id`.
- **agent_id** (str, optional): Sub-agent ID, used when `card` is `None`.

**Returns**:

**AgentSession**: A sub-agent session sharing the current team session's output stream.

---

## function openjiuwen.core.multi_agent.create_agent_team_session

```python
create_agent_team_session(
    session_id: str = None,
    envs: dict[str, Any] = None,
    team_id: str = "agent_team"
) -> Session
```

Factory function that creates and returns an `AgentTeam` session object.

**Parameters**:

- **session_id** (str, optional): Unique session identifier; a UUID is generated automatically if not provided.
- **envs** (dict[str, Any], optional): Environment variables used during execution.
- **team_id** (str, optional): Team identifier. Default: `"agent_team"`.

**Returns**:

**Session**: A newly created `AgentTeam` session instance.

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

Identity card for an agent team, describing the team's static metadata. Inherits from `BaseCard` (providing `id`, `name`, `description` fields).

**Attributes**:

- **id** (str): Unique team identifier, cannot be empty.
- **name** (str): Team name, cannot be empty.
- **description** (str): Team description. Default: `""`.
- **agent_cards** (List[AgentCard]): List of `AgentCard` objects for team members (metadata only, not instances). Default: `[]`.
- **topic** (str): The team's primary topic or domain. Default: `""`.
- **version** (str): Team version string. Default: `"1.0.0"`.
- **tags** (List[str]): Tags for categorization. Default: `[]`.

---

## class openjiuwen.core.multi_agent.TeamConfig

```python
class openjiuwen.core.multi_agent.TeamConfig(
    max_agents: int = 10,
    max_concurrent_messages: int = 100,
    message_timeout: float = 30.0
)
```

Mutable runtime configuration for an agent team, controlling team capacity, concurrent message limits, and message timeout behavior.

**Attributes**:

- **max_agents** (int): Maximum number of agents allowed in the team. Default: `10`.
- **max_concurrent_messages** (int): Maximum number of concurrent messages to process. Default: `100`.
- **message_timeout** (float): Message processing timeout in seconds. Default: `30.0`.

---

### configure_max_agents

```python
configure_max_agents(self, max_agents: int) -> TeamConfig
```

Sets the maximum number of agents in the team.

**Parameters**:

- **max_agents** (int): Maximum agent count.

**Returns**:

**TeamConfig**: The current config instance (supports chaining).

---

### configure_timeout

```python
configure_timeout(self, timeout: float) -> TeamConfig
```

Sets the message processing timeout.

**Parameters**:

- **timeout** (float): Timeout in seconds.

**Returns**:

**TeamConfig**: The current config instance (supports chaining).

---

### configure_concurrency

```python
configure_concurrency(self, max_concurrent: int) -> TeamConfig
```

Sets the maximum concurrent message limit.

**Parameters**:

- **max_concurrent** (int): Maximum number of concurrent messages to process.

**Returns**:

**TeamConfig**: The current config instance (supports chaining).

---

## class openjiuwen.core.multi_agent.BaseTeam

```python
class openjiuwen.core.multi_agent.BaseTeam(
    card: TeamCard,
    config: Optional[TeamConfig] = None,
    runtime: Optional[TeamRuntime] = None
)
```

Abstract base class for agent teams, defining the standard team interface. `card` describes team identity, `config` controls runtime behavior, and all agent management is delegated to the internal `TeamRuntime`. Subclasses must implement `invoke()` and `stream()`.

**Parameters**:

- **card** (TeamCard): Team identity card; required.
- **config** (TeamConfig, optional): Runtime configuration; defaults are used if not provided.
- **runtime** (TeamRuntime, optional): Team runtime instance; created automatically if not provided.

**Attributes**:

- **card** (TeamCard): Team identity card.
- **config** (TeamConfig): Runtime configuration.
- **team_id** (str): Team identifier (derived from `card.name`).
- **runtime** (TeamRuntime): Team runtime instance.

---

### configure

```python
configure(self, config: TeamConfig) -> BaseTeam
```

Sets the team runtime configuration.

**Parameters**:

- **config** (TeamConfig): New configuration object.

**Returns**:

**BaseTeam**: The current team instance (supports chaining).

---

### add_agent

```python
add_agent(
    self,
    card: AgentCard,
    provider: AgentProvider
) -> BaseTeam
```

Registers an agent with the team using the Card + Provider pattern. Delegates to `runtime.register_agent` and appends the card to `self.card.agent_cards`. Skips silently if the agent ID already exists; raises an exception if `max_agents` is exceeded.

**Parameters**:

- **card** (AgentCard): Agent identity card (including `id`).
- **provider** (AgentProvider): Agent factory for lazy instance creation. If the created agent inherits `CommunicableAgent`, the runtime automatically calls `bind_runtime()`.

**Returns**:

**BaseTeam**: The current team instance (supports chaining).

**Exceptions**:

- Raises `AGENT_TEAM_ADD_RUNTIME_ERROR` when `max_agents` is exceeded.

---

### remove_agent

```python
remove_agent(
    self,
    agent: Union[str, AgentCard]
) -> BaseTeam
```

Removes an agent from the team, clearing its card registration and topic subscriptions in the runtime. Does not unregister from `ResourceMgr` (the agent may be shared).

**Parameters**:

- **agent** (str | AgentCard): Agent ID string or `AgentCard` instance.

**Returns**:

**BaseTeam**: The current team instance (supports chaining).

---

### subscribe

```python
async subscribe(self, agent_id: str, topic: str) -> None
```

Subscribes an agent to a topic (delegates to `runtime.subscribe`). Supports exact matching and wildcard (`*`, `?`) patterns.

**Parameters**:

- **agent_id** (str): Agent ID.
- **topic** (str): Topic pattern string, e.g. `"code_events"` or `"code_*"`.

---

### unsubscribe

```python
async unsubscribe(self, agent_id: str, topic: str) -> None
```

Unsubscribes an agent from a topic (delegates to `runtime.unsubscribe`).

**Parameters**:

- **agent_id** (str): Agent ID.
- **topic** (str): Topic pattern string.

---

### get_agent_card

```python
get_agent_card(self, agent_id: str) -> Optional[AgentCard]
```

Retrieves the `AgentCard` for a registered agent by ID (delegates to `runtime`).

**Parameters**:

- **agent_id** (str): Agent ID.

**Returns**:

**Optional[AgentCard]**: The corresponding `AgentCard`, or `None` if not found.

---

### get_agent_count

```python
get_agent_count(self) -> int
```

Returns the number of agents currently registered in the team (delegates to `runtime`).

**Returns**:

**int**: Number of registered agents.

---

### list_agents

```python
list_agents(self) -> List[str]
```

Lists the IDs of all agents currently registered in the team (delegates to `runtime`).

**Returns**:

**List[str]**: List of agent IDs.

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

Sends a point-to-point (P2P) message to a specified agent within the team and waits for the response. Both `sender` and `recipient` must be registered in the team.

**Parameters**:

- **message** (Any): Message payload.
- **recipient** (str): Recipient agent ID (must be registered).
- **sender** (str): Sender agent ID (must be registered; required for tracing).
- **session_id** (str, optional): Session ID for session continuity.
- **timeout** (float, optional): Response timeout in seconds.

**Returns**:

**Any**: Response from the recipient agent.

**Exceptions**:

- Raises `AGENT_TEAM_AGENT_NOT_FOUND` if `sender` or `recipient` is not registered in the team.

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

Publishes a message to a topic within the team (Pub-Sub pattern, fire-and-forget). All agents subscribed to the topic receive the message concurrently. `sender` must be registered in the team.

**Parameters**:

- **message** (Any): Message payload.
- **topic_id** (str): Topic ID, e.g. `"code_events"`, `"task_updates"`.
- **sender** (str): Sender agent ID (must be registered).
- **session_id** (str, optional): Session ID.

**Exceptions**:

- Raises `AGENT_TEAM_AGENT_NOT_FOUND` if `sender` is not registered in the team.

---

### abstractmethod invoke

```python
async invoke(
    self,
    message: Any,
    session: Optional[Session] = None
) -> Any
```

(Abstract method) Executes the team task in batch mode. Subclasses must implement this method.

**Parameters**:

- **message** (Any): Input message object or dictionary.
- **session** (Session, optional): `AgentTeam` session instance.

**Returns**:

**Any**: The aggregated execution result from the team.

---

### abstractmethod stream

```python
async stream(
    self,
    message: Any,
    session: Optional[Session] = None
) -> AsyncIterator[Any]
```

(Abstract method) Executes the team task in streaming mode. Subclasses must implement this method.

**Parameters**:

- **message** (Any): Input message object or dictionary.
- **session** (Session, optional): `AgentTeam` session instance.

**Returns**:

**AsyncIterator[Any]**: An async iterator that yields streaming output chunks from the team execution.

---

## class openjiuwen.core.multi_agent.team_runtime.CommunicableAgent

```python
class openjiuwen.core.multi_agent.team_runtime.CommunicableAgent()
```

A mixin class that adds messaging capabilities to agents. Agents inheriting this class can communicate with other agents in the team via `send()`, `publish()`, `subscribe()`, and `unsubscribe()`.

Runtime binding is performed automatically by `TeamRuntime.register_agent`; no manual call is needed.

**Usage**:

```python
class MyAgent(CommunicableAgent, BaseAgent):
    ...
```

---

### bind_runtime

```python
bind_runtime(self, runtime: TeamRuntime, agent_id: str) -> None
```

Binds a `TeamRuntime` to the current agent instance. Called automatically by `TeamRuntime.register_agent`; typically does not need to be called manually.

**Parameters**:

- **runtime** (TeamRuntime): Team runtime instance.
- **agent_id** (str): This agent`s ID.

---

### is_bound

```python
@property
is_bound(self) -> bool
```

Checks whether the current agent is bound to a runtime.

**Returns**:

**bool**: `True` if `bind_runtime` has been called with valid values.

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

Sends a point-to-point (P2P) message to another agent and waits for the response. The agent must be bound to a runtime.

**Parameters**:

- **message** (Any): Message payload.
- **recipient** (str): Recipient agent ID.
- **session_id** (str, optional): Session ID.
- **timeout** (float, optional): Response timeout in seconds.

**Returns**:

**Any**: Response from the recipient agent.

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

Publishes a message to a topic (Pub-Sub pattern, fire-and-forget). The agent must be bound to a runtime.

**Parameters**:

- **message** (Any): Message payload.
- **topic_id** (str): Topic ID.
- **session_id** (str, optional): Session ID.

---

### subscribe

```python
async subscribe(self, topic: str) -> None
```

Subscribes to a topic. When a message is published to the topic, this agent will be invoked. Supports wildcards (`*`, `?`). The agent must be bound to a runtime.

**Parameters**:

- **topic** (str): Topic pattern string.

---

### unsubscribe

```python
async unsubscribe(self, topic: str) -> None
```

Unsubscribes from a topic. The agent must be bound to a runtime.

**Parameters**:

- **topic** (str): Topic pattern string.

---

## class openjiuwen.core.multi_agent.team_runtime.TeamRuntime

```python
class openjiuwen.core.multi_agent.team_runtime.TeamRuntime(
    config: Optional[RuntimeConfig] = None
)
```

Self-contained runtime for multi-agent team communication. Manages agent registration and lifecycle, and provides both point-to-point (P2P) and publish-subscribe (Pub-Sub) messaging patterns through an internal `MessageBus`. Can be used standalone or as the backbone of a `BaseTeam` subclass.

**Parameters**:

- **config** (RuntimeConfig, optional): Runtime configuration; defaults are used if not provided.

---

### is_running

```python
is_running(self) -> bool
```

Checks whether the runtime is currently running.

**Returns**:

**bool**: `True` if running, `False` otherwise.

---

### start

```python
async start(self) -> None
```

Starts the runtime and initializes the message bus background task. Ignored if already running. Supports use as an async context manager (`async with`).

---

### stop

```python
async stop(self) -> None
```

Stops the runtime, shuts down the message bus, and cleans up all resources. Ignored if not running.

---

### register_agent

```python
register_agent(
    self,
    card: AgentCard,
    provider: AgentProvider
) -> None
```

Registers an agent with the runtime using the Card + Provider pattern. Stores the `AgentCard` locally and registers a wrapped provider with `Runner.ResourceMgr`. If the created agent inherits `CommunicableAgent`, the wrapper automatically calls `bind_runtime()`.

**Parameters**:

- **card** (AgentCard): Agent identity card (including `id`).
- **provider** (AgentProvider): Agent factory function for lazy instance creation.

**Exceptions**:

- Raises `AGENT_TEAM_ADD_RUNTIME_ERROR` if the Runner module is unavailable or registration fails.

---

### unregister_agent

```python
unregister_agent(self, agent_id: str) -> Optional[AgentCard]
```

Removes an agent from the runtime, clearing its local card registration and all topic subscriptions. Does not unregister from `ResourceMgr` (the agent may be shared).

**Parameters**:

- **agent_id** (str): Agent ID.

**Returns**:

**Optional[AgentCard]**: The removed `AgentCard`, or `None` if not found.

---

### has_agent

```python
has_agent(self, agent_id: str) -> bool
```

Checks whether an agent is registered in the runtime.

**Parameters**:

- **agent_id** (str): Agent ID.

**Returns**:

**bool**: `True` if the agent is registered.

---

### get_agent_card

```python
get_agent_card(self, agent_id: str) -> Optional[AgentCard]
```

Retrieves the `AgentCard` for a registered agent by ID.

**Parameters**:

- **agent_id** (str): Agent ID.

**Returns**:

**Optional[AgentCard]**: The corresponding `AgentCard`, or `None` if not found.

---

### list_agents

```python
list_agents(self) -> list[str]
```

Lists all registered agent IDs.

**Returns**:

**list[str]**: List of agent IDs.

---

### get_agent_count

```python
get_agent_count(self) -> int
```

Returns the number of registered agents.

**Returns**:

**int**: Agent count.

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

Sends a P2P message to a registered agent and waits for the response. If the runtime has not been started, it is started automatically on first use.

**Parameters**:

- **message** (Any): Message payload.
- **recipient** (str): Recipient agent ID (must be registered).
- **sender** (str): Sender agent ID (required for tracing).
- **session_id** (str, optional): Session ID for per-session topic isolation.
- **timeout** (float, optional): Response timeout in seconds.

**Returns**:

**Any**: Response from the recipient agent.

**Exceptions**:

- Raises `AGENT_TEAM_EXECUTION_ERROR` if `recipient` is not registered, or on timeout.

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

Publishes a message to a topic (Pub-Sub pattern, fire-and-forget). All matching subscribers are invoked concurrently. If the runtime has not been started, it is started automatically on first use.

**Parameters**:

- **message** (Any): Message payload.
- **topic_id** (str): Topic ID, e.g. `"code_events"`.
- **sender** (str): Sender agent ID (required for tracing).
- **session_id** (str, optional): Session ID for topic isolation.

**Exceptions**:

- Raises `AGENT_TEAM_EXECUTION_ERROR` on publish failure.

---

### subscribe

```python
async subscribe(self, agent_id: str, topic: str) -> None
```

Subscribes an agent to a topic pattern. Supports exact matching and wildcard (`*`, `?`) patterns.

**Parameters**:

- **agent_id** (str): Agent ID.
- **topic** (str): Topic pattern string, e.g. `"code_events"` or `"code_*"`.

---

### unsubscribe

```python
async unsubscribe(self, agent_id: str, topic: str) -> None
```

Unsubscribes an agent from a topic pattern.

**Parameters**:

- **agent_id** (str): Agent ID.
- **topic** (str): Topic pattern string.

---

### list_subscriptions

```python
list_subscriptions(self, agent_id: Optional[str] = None) -> dict[str, Any]
```

Queries the current subscription state for debugging and introspection.

**Parameters**:

- **agent_id** (str, optional): If specified, returns only that agent`s subscriptions; if omitted, returns all subscriptions.

**Returns**:

**dict[str, Any]**: Subscription information dictionary.

---

### get_subscription_count

```python
get_subscription_count(self) -> int
```

Returns the total number of active topic subscriptions.

**Returns**:

**int**: Total subscription count.

---

## class openjiuwen.core.multi_agent.team_runtime.RuntimeConfig

```python
class openjiuwen.core.multi_agent.team_runtime.RuntimeConfig(
    team_id: str = "default",
    message_bus: Optional[MessageBusConfig] = None
)
```

Configuration object for `TeamRuntime`.

**Attributes**:

- **team_id** (str): Team ID used for topic isolation. Default: `"default"`.
- **message_bus** (MessageBusConfig, optional): Message bus configuration; defaults are used if not provided.

---

## class openjiuwen.core.multi_agent.team_runtime.MessageBusConfig

```python
class openjiuwen.core.multi_agent.team_runtime.MessageBusConfig(
    max_queue_size: int = 1000,
    process_timeout: Optional[float] = 100000,
    team_id: Optional[str] = None
)
```

Message bus configuration object, controlling message queue capacity, processing timeout, and team isolation.

**Attributes**:

- **max_queue_size** (int): Maximum message queue capacity. Default: `1000`.
- **process_timeout** (float, optional): Message processing timeout in seconds. Default: `100000`.
- **team_id** (str, optional): Team ID used for message topic naming and isolation. Default: `None`.

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

Immutable message routing envelope carrying the complete routing metadata for a single message (`frozen=True`).

**Attributes**:

- **message_id** (str): Unique message identifier.
- **message** (Any): Message payload.
- **sender** (str, optional): Sender agent ID.
- **recipient** (str, optional): Recipient agent ID; set for P2P messages.
- **topic_id** (str, optional): Topic ID; set for Pub-Sub messages.
- **session_id** (str, optional): Session ID.
- **metadata** (dict): Additional metadata.

---

### is_p2p

```python
is_p2p(self) -> bool
```

Checks if this is a point-to-point (P2P) message.

**Returns**:

**bool**: `True` if `recipient` is not `None`.

---

### is_pubsub

```python
is_pubsub(self) -> bool
```

Checks if this is a publish-subscribe (Pub-Sub) message.

**Returns**:

**bool**: `True` if `topic_id` is not `None`.
