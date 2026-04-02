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

---

## function openjiuwen.core.multi_agent.teams.make_team_session

```python
make_team_session(
    card: TeamCard,
    message: Any
) -> Session
```

Creates a fresh `AgentTeam` session. Reuses the `conversation_id` from `message` when it is a dict containing that key; otherwise generates a new UUID.

**Parameters**:

- **card** (TeamCard): The owning team's identity card; provides `team_id`.
- **message** (Any): User input — dict or str.

**Returns**:

**Session**: A new session bound to the team.

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

Async context manager that owns the complete session lifecycle for `invoke()`.

- **Standalone mode** (`session=None`): Creates a session, calls `pre_run()`, binds it to the runtime; on exit, unbinds and calls `post_run()`.
- **Runner mode** (existing `Session` supplied): Acts as a transparent passthrough with no lifecycle side-effects.

**Parameters**:

- **runtime** (TeamRuntime): The owning team's runtime instance.
- **card** (TeamCard): The owning team's identity card.
- **message** (Any): User input — dict or str.
- **session** (Session, optional): Externally supplied session from Runner; `None` enables standalone mode.

**Yields**:

**Tuple[Session, str]**: `(team_session, session_id)` tuple.

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

Async generator that owns the complete session lifecycle for `stream()`. Runs `run_coro` in a background Task while yielding stream chunks to the caller concurrently.

- **Standalone mode** (`session=None`): Creates and binds a session; the background Task unbinds and calls `post_run()` on completion.
- **Runner mode** (existing `Session` supplied): The background Task calls `session.close_stream()` on completion to signal end-of-stream.

**Parameters**:

- **runtime** (TeamRuntime): The owning team's runtime instance.
- **card** (TeamCard): The owning team's identity card.
- **message** (Any): User input — dict or str.
- **run_coro** (Callable[[Session, str], Awaitable[None]]): The team's actual work coroutine; signature `async (session, session_id) -> None`.
- **session** (Session, optional): Externally supplied session from Runner; `None` enables standalone mode.

**Yields**:

**Any**: Output chunks written to the stream by `run_coro`.

---

## class openjiuwen.core.multi_agent.teams.handoff.HandoffRoute

```python
@dataclass(frozen=True)
class openjiuwen.core.multi_agent.teams.handoff.HandoffRoute(
    source: str,
    target: str
)
```

Immutable routing rule declaring that the `source` agent may hand off to the `target` agent (`frozen=True`).

**Attributes**:

- **source** (str): Source agent ID.
- **target** (str): Target agent ID.

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

Orchestration parameters for `HandoffTeam`.

**Attributes**:

- **start_agent** (AgentCard, optional): `AgentCard` of the first agent to run. Default: `None`; uses the first agent added via `add_agent()` when not provided.
- **max_handoffs** (int): Maximum number of handoff transfers after the initial hop. For example, `max_handoffs=2` allows A→B→C but blocks a 4th hop. Default: `10`.
- **routes** (List[HandoffRoute]): Explicit routing rules. When empty, any agent may hand off to any other (full-mesh), and this also controls which `HandoffTool` instances are injected. Default: `[]`.
- **termination_condition** (Callable, optional): Optional condition with signature `(HandoffOrchestrator) -> bool`; supports sync or async. Triggers early termination when it returns `True`. Default: `None`.

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

Full configuration for `HandoffTeam`. Extends `TeamConfig` with handoff-specific orchestration parameters. Allows extra fields (`extra='allow'`) and arbitrary types (`arbitrary_types_allowed=True`).

**Attributes**:

- **handoff** (HandoffConfig): Handoff orchestration configuration. Default: `HandoffConfig()`.
- Inherits all `TeamConfig` attributes (`max_agents`, `max_concurrent_messages`, `message_timeout`) and their chaining configuration methods.

---

## class openjiuwen.core.multi_agent.teams.handoff.HandoffTeam

```python
class openjiuwen.core.multi_agent.teams.handoff.HandoffTeam(
    card: TeamCard,
    config: Optional[HandoffTeamConfig] = None
)
```

Event-driven handoff multi-agent team, inheriting from `BaseTeam`. Agents collaborate via sequential handoffs: the LLM in each agent decides whether to complete the task or transfer control by calling an injected `transfer_to_{agent}` tool.

**Parameters**:

- **card** (TeamCard): Team identity card.
- **config** (HandoffTeamConfig, optional): Team configuration. Uses `HandoffTeamConfig()` defaults when not provided.

---

### add_agent

```python
add_agent(
    self,
    card: AgentCard,
    provider: AgentProvider
) -> HandoffTeam
```

Registers an agent into the team. Silently skips if the agent ID already exists. Resets the internal initialization state so handoff routes are reconfigured on next invocation.

**Parameters**:

- **card** (AgentCard): Agent identity card.
- **provider** (AgentProvider): Factory callable for lazy agent instance creation.

**Returns**:

**HandoffTeam**: The current team instance (supports chaining).

**Exceptions**:

- Raises `AGENT_TEAM_ADD_RUNTIME_ERROR` when `max_agents` is exceeded.

---

### invoke

```python
async invoke(
    self,
    message: Any,
    session: Optional[Session] = None
) -> Any
```

Runs the handoff chain in batch mode and returns the final result.

**Parameters**:

- **message** (Any): User input — dict or str.
- **session** (Session, optional): Session from Runner; creates a standalone session when `None`.

**Returns**:

**Any**: Final result produced by the last agent in the handoff chain.

---

### stream

```python
async stream(
    self,
    message: Any,
    session: Optional[Session] = None
) -> AsyncIterator[Any]
```

Runs the handoff chain in streaming mode, yielding output chunks in real time.

**Parameters**:

- **message** (Any): User input — dict or str.
- **session** (Session, optional): Session from Runner; creates a standalone session when `None`.

**Yields**:

**Any**: Output chunks emitted by agents during the handoff chain.

---

## class openjiuwen.core.multi_agent.teams.handoff.HandoffOrchestrator

```python
class openjiuwen.core.multi_agent.teams.handoff.HandoffOrchestrator(
    start_agent_id: str,
    registered_agents: List[str],
    config: Optional[HandoffConfig] = None
)
```

Per-invocation handoff state coordinator, created and owned by `HandoffTeam`. Tracks the current agent, counts handoff transfers, makes routing decisions, and delivers the final result via `done_future`. Use `restore_from_session` to resume an interrupted session.

**Parameters**:

- **start_agent_id** (str): ID of the first agent to run.
- **registered_agents** (List[str]): List of all registered agent IDs in the team.
- **config** (HandoffConfig, optional): Configuration carrying routes, `max_handoffs`, and `termination_condition`. Uses defaults when `None`.

**Attributes**:

- **handoff_count** (int, read-only): Number of handoff transfers completed in this session.
- **current_agent_id** (str, read-only): ID of the agent that will execute the next hop.
- **done_future** (asyncio.Future, read-only): Completion future for the handoff chain; created lazily inside the running event loop.

---

### build_route_graph

```python
@staticmethod
build_route_graph(
    agents: List[str],
    routes: List[HandoffRoute]
) -> Dict[str, Set[str]]
```

Builds an adjacency graph of allowed handoff routes.

**Parameters**:

- **agents** (List[str]): List of all agent IDs in the team.
- **routes** (List[HandoffRoute]): Explicit routing rules. An empty list generates full-mesh (any agent may hand off to any other).

**Returns**:

**Dict[str, Set[str]]**: Dict mapping each agent ID to the set of agent IDs it may hand off to.

---

### request_handoff

```python
async request_handoff(
    self,
    target_id: str,
    reason: Optional[str] = None
) -> bool
```

Attempts to approve a handoff to `target_id`. Updates internal state on approval; does not update state on rejection (limit reached, termination condition triggered, or route not allowed).

**Parameters**:

- **target_id** (str): Target agent ID.
- **reason** (str, optional): Optional reason string for logging.

**Returns**:

**bool**: `True` if the handoff is approved; `False` if rejected.

---

### complete

```python
async complete(self, result: Any) -> None
```

Resolves `done_future` with `result`, ending the handoff chain. Idempotent — first call wins.

**Parameters**:

- **result** (Any): Final result to return to the caller.

---

### error

```python
async error(self, exception: Exception) -> None
```

Rejects `done_future` with `exception`, propagating the error to the caller. Idempotent — first call wins.

**Parameters**:

- **exception** (Exception): Exception to raise at the `await` site.

---

### save_to_session

```python
save_to_session(self, session: Session) -> None
```

Persists coordinator state to `session` for interrupt/resume support.

**Parameters**:

- **session** (Session): Team session to write state into.

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

Creates an orchestrator restoring state from a previous interrupted session. Returns a fresh orchestrator starting at `start_agent_id` when no prior state exists.

**Parameters**:

- **session** (Session): Team session to read state from.
- **start_agent_id** (str): Starting agent ID used when no prior state exists.
- **registered_agents** (List[str]): List of all agent IDs in the team.
- **config** (HandoffConfig, optional): Configuration carrying routes, `max_handoffs`, and `termination_condition`.

**Returns**:

**HandoffOrchestrator**: Orchestrator restored from session state, or a freshly created one.

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

Immutable handoff directive produced by `extract_handoff_signal` (`frozen=True`).

**Attributes**:

- **target** (str): Target agent ID.
- **message** (str, optional): Context message forwarded to the target agent. Set to `None` when the value is an empty string.
- **reason** (str, optional): Human-readable reason for the handoff. Set to `None` when the value is an empty string.

---

## function openjiuwen.core.multi_agent.teams.handoff.extract_handoff_signal

```python
extract_handoff_signal(result: Any) -> Optional[HandoffSignal]
```

Returns a `HandoffSignal` if `result` contains a handoff directive; otherwise returns `None`.

Searches for the `__handoff_to__` key in `result` itself or in its `output`, `result`, or `content` sub-key. The target value must be a non-empty string, otherwise `None` is returned.

**Parameters**:

- **result** (Any): Agent return value to inspect.

**Returns**:

**Optional[HandoffSignal]**: `HandoffSignal` when a valid handoff directive is found; `None` otherwise.

---

## class openjiuwen.core.multi_agent.teams.handoff.TeamInterruptSignal

```python
@dataclass
class openjiuwen.core.multi_agent.teams.handoff.TeamInterruptSignal(
    result: Any,
    message: Optional[str] = None
)
```

Signal that pauses the handoff chain and persists state for later resumption.

**Attributes**:

- **result** (Any): Interrupt payload returned to the caller; must have `result_type='interrupt'`.
- **message** (str, optional): Optional human-readable description of the interrupt reason.

---

## function openjiuwen.core.multi_agent.teams.handoff.extract_interrupt_signal

```python
extract_interrupt_signal(
    result: Any = None,
    exc: Optional[Exception] = None
) -> Optional[TeamInterruptSignal]
```

Extracts a `TeamInterruptSignal` from an agent result or exception.

- Recognised from `result` when it is a dict and `result.get('result_type') == 'interrupt'`.
- Recognised from `exc` when it is an `AgentInterrupt` instance.
- `result` takes priority over `exc`: `exc` is only checked when `result` is not an interrupt.

**Parameters**:

- **result** (Any, optional): Agent return value to inspect.
- **exc** (Exception, optional): Exception to inspect.

**Returns**:

**Optional[TeamInterruptSignal]**: `TeamInterruptSignal` when an interrupt is detected; `None` otherwise.

---

## function openjiuwen.core.multi_agent.teams.handoff.flush_team_session

```python
async flush_team_session(session: Optional[Session]) -> None
```

Calls `post_run()` on the team session after an interrupt as a best-effort cleanup. Flush failures are logged as warnings and never propagated, so that interrupt delivery to the caller is never blocked.

**Parameters**:

- **session** (Session, optional): Team session to flush. No-op when `None`.

---


---

## class openjiuwen.core.multi_agent.teams.handoff.HandoffTool

```python
class openjiuwen.core.multi_agent.teams.handoff.HandoffTool(
    target_id: str,
    target_description: str = ""
)
```

Handoff tool automatically injected by `HandoffTeam` into every agent's `AbilityManager` to signal agent-to-agent control transfers. The tool name exposed to the LLM is `transfer_to_{target_id}`. Inherits from `Tool`.

**Parameters**:

- **target_id** (str): ID of the target agent.
- **target_description** (str, optional): Description of the target agent, appended to the tool description shown to the LLM. Default: `""`.

---

### invoke

```python
async invoke(self, inputs: Any, **kwargs: Any) -> dict
```

Returns a handoff signal payload dict consumed by `extract_handoff_signal`.

**Parameters**:

- **inputs** (Any): Tool arguments from the LLM; accepts a dict or JSON string with `reason` / `message` keys.

**Returns**:

**dict**: Dict containing `__handoff_to__`, `__handoff_message__`, and `__handoff_reason__` keys.

---

### stream

```python
async stream(self, inputs: Any, **kwargs: Any) -> AsyncIterator[dict]
```

Streaming variant that yields the single `invoke()` result.

**Parameters**:

- **inputs** (Any): Tool arguments from the LLM.

**Yields**:

**dict**: The same handoff signal payload dict as `invoke()`.

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

Per-agent wrapper automatically created by `HandoffTeam` for each registered agent. Responsible for lazy-loading the target agent instance, injecting `HandoffTool`, writing execution history to the team session context, and handling handoff and interrupt signals. Inherits from `CommunicableAgent` and `BaseAgent`.

**Parameters**:

- **target_card** (AgentCard): Identity card of the wrapped agent.
- **target_provider** (Callable[[], BaseAgent]): Lazy factory; called once on first use to create the target agent instance.
- **allowed_targets** (List[str]): List of agent IDs that this agent is allowed to hand off to; used to inject the corresponding `HandoffTool` instances.
- **coordinator_lookup** (Callable[[str], HandoffOrchestrator], optional): Callback that resolves a `HandoffOrchestrator` from a `session_id`.

---

### invoke

```python
async invoke(self, inputs: HandoffRequest, session: Optional[Session] = None) -> dict
```

Runs the target agent and processes its output. Decides whether to complete the orchestration, initiate the next handoff hop, or propagate an interrupt signal.

**Parameters**:

- **inputs** (HandoffRequest): Drive message containing the user input, accumulated history, and team session.
- **session** (Session, optional): Current agent session; not normally passed directly.

**Returns**:

**dict**: Always returns an empty dict `{}`; orchestration results are delivered via `HandoffOrchestrator`.

**Exceptions**:

- Silently returns `{}` when `inputs` is not a `HandoffRequest` instance.
- Raises `AGENT_TEAM_EXECUTION_ERROR` when `coordinator_lookup` returns `None`.
- Raises `AGENT_TEAM_EXECUTION_ERROR` when the target agent raises a non-interrupt exception.

---

### stream

```python
async stream(self, inputs: HandoffRequest, session: Optional[Session] = None, **kwargs) -> AsyncIterator[dict]
```

Streaming variant; internally calls `invoke()` and yields its result.

**Parameters**:

- **inputs** (HandoffRequest): Drive message.
- **session** (Session, optional): Current agent session.

**Yields**:

**dict**: The return value of `invoke()` (always `{}`).

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

Internal drive message published to `container_{agent_id}` topics by `HandoffTeam` to orchestrate cross-agent handoffs. Each hop carries the accumulated history and a reference to the team session.

**Attributes**:

- **input_message** (Any): User or intermediate input forwarded to the next agent.
- **history** (List[dict]): Accumulated handoff history across hops; each entry contains `agent` and `output` keys. Default: `[]`.
- **session** (Session, optional): Team session for stream I/O. `None` in unit-test scenarios.

---

### session_id

```python
@property
session_id(self) -> str
```

Session ID derived from the attached session; returns an empty string when no session is attached.

**Returns**:

**str**: Session ID string, or `""` when no session is attached.

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

Configuration for `HierarchicalTeam` (Agents-as-Tools mode), inheriting from `TeamConfig`.

**Attributes**:

- **root_agent** (AgentCard): `AgentCard` of the top-level entry agent; required.
- Inherits all `TeamConfig` attributes (`max_agents`, `max_concurrent_messages`, `message_timeout`) and their chaining configuration methods.

---

## class openjiuwen.core.multi_agent.teams.hierarchical_tools.HierarchicalTeam

```python
class openjiuwen.core.multi_agent.teams.hierarchical_tools.HierarchicalTeam(
    card: TeamCard,
    config: HierarchicalTeamConfig,
    runtime: Optional[TeamRuntime] = None
)
```

Agents-as-Tools hierarchical multi-agent team, inheriting from `BaseTeam`. Agents are composed hierarchically through each agent's `ability_manager`, with the root agent as the execution entry point.

**Parameters**:

- **card** (TeamCard): Team identity card.
- **config** (HierarchicalTeamConfig): Team configuration; must include `root_agent`.
- **runtime** (TeamRuntime, optional): Team runtime instance; created automatically when not provided.

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

Adds an agent to the team. When `parent_agent_id` is provided, queues the agent's card to be registered as a tool under the parent's `ability_manager` before the first run.

**Parameters**:

- **card** (AgentCard): Agent identity card.
- **provider** (AgentProvider): Factory callable for lazy agent instance creation.
- **parent_agent_id** (str, optional): Parent agent ID; when provided, registers the current agent as a child tool of that parent.

**Returns**:

**HierarchicalTeam**: The current team instance (supports chaining).

**Exceptions**:

- Raises `AGENT_TEAM_ADD_RUNTIME_ERROR` when `max_agents` is exceeded.

---

### invoke

```python
async invoke(
    self,
    inputs: Any,
    session: Optional[Session] = None
) -> Any
```

Runs the team from the root agent and returns the final result.

**Parameters**:

- **inputs** (Any): Input message or dict.
- **session** (Session, optional): External `AgentTeamSession`; creates a standalone session when `None`.

**Returns**:

**Any**: Final result from the root agent.

**Exceptions**:

- Raises `AGENT_TEAM_EXECUTION_ERROR` when the root agent is not registered in the runtime.

---

### stream

```python
async stream(
    self,
    inputs: Any,
    session: Optional[Session] = None
) -> AsyncGenerator[Any, None]
```

Runs the team from the root agent and yields streaming output chunks.

**Parameters**:

- **inputs** (Any): Input message or dict.
- **session** (Session, optional): External `AgentTeamSession`; creates a standalone session when `None`.

**Yields**:

**Any**: Streaming output chunks.

**Exceptions**:

- Raises `AGENT_TEAM_EXECUTION_ERROR` when the root agent is not registered in the runtime.


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

Configuration for HierarchicalTeam (P2P MessageBus mode), inheriting from TeamConfig.

**Attributes**:

- **supervisor_agent** (AgentCard): AgentCard of the top-level supervisor agent; required.
- Inherits all TeamConfig attributes (max_agents, max_concurrent_messages, message_timeout) and their chaining configuration methods.

---

## class openjiuwen.core.multi_agent.teams.hierarchical_msgbus.HierarchicalTeam

```python
class openjiuwen.core.multi_agent.teams.hierarchical_msgbus.HierarchicalTeam(
    card: TeamCard,
    config: HierarchicalTeamConfig
)
```

Supervisor-driven hierarchical multi-agent team, inheriting from BaseTeam. The supervisor dispatches tasks to sub-agents via P2PAbilityManager over the message bus.

**Parameters**:

- **card** (TeamCard): Team identity card.
- **config** (HierarchicalTeamConfig): Team configuration; must include supervisor_agent.

---

### add_agent

```python
add_agent(
    self,
    card: AgentCard,
    provider: AgentProvider
) -> HierarchicalTeam
```

Registers an agent (supervisor or sub-agent) into the team runtime. Logs an info message when the supervisor card is registered.

**Parameters**:

- **card** (AgentCard): Agent identity card.
- **provider** (AgentProvider): Factory callable for lazy agent instance creation.

**Returns**:

**HierarchicalTeam**: The current team instance (supports chaining).

---

### invoke

```python
async invoke(
    self,
    message: Any,
    session: Optional[Session] = None
) -> Any
```

Runs the supervisor agent and returns the final result.

**Parameters**:

- **message** (Any): User input - dict or str.
- **session** (Session, optional): Session from Runner; creates a standalone session when None.

**Returns**:

**Any**: Final result returned by the supervisor agent.

**Exceptions**:

- Raises AGENT_TEAM_EXECUTION_ERROR when the supervisor is not registered in the runtime.

---

### stream

```python
async stream(
    self,
    message: Any,
    session: Optional[Session] = None
) -> AsyncIterator[Any]
```

Runs the supervisor agent and yields streaming output chunks.

**Parameters**:

- **message** (Any): User input - dict or str.
- **session** (Session, optional): Session from Runner; creates a standalone session when None.

**Yields**:

**Any**: Chunks emitted by the supervisor or sub-agents during execution.

**Exceptions**:

- Raises AGENT_TEAM_EXECUTION_ERROR when the supervisor is not registered in the runtime.

---

## class openjiuwen.core.multi_agent.teams.hierarchical_msgbus.SupervisorAgent

```python
class openjiuwen.core.multi_agent.teams.hierarchical_msgbus.SupervisorAgent(
    card: AgentCard,
    config: Optional[ReActAgentConfig] = None,
    max_parallel_sub_agents: int = 10
)
```

Default built-in supervisor agent for HierarchicalTeam (P2P MessageBus mode). Inherits both CommunicableAgent (P2P send/publish) and ReActAgent (ReAct loop). AgentCard tool calls are routed via P2PAbilityManager; all other ability types execute normally.

**Parameters**:

- **card** (AgentCard): Supervisor agent identity card.
- **config** (ReActAgentConfig, optional): ReAct agent configuration; uses defaults when not provided.
- **max_parallel_sub_agents** (int): Maximum concurrent sub-agent dispatches. Default: 10.

---

### register_sub_agent_card

```python
register_sub_agent_card(self, card: AgentCard) -> None
```

Exposes a sub-agent card to the LLM as a callable tool.

**Parameters**:

- **card** (AgentCard): Sub-agent identity card.

---

### configure

```python
configure(self, config: Any) -> SupervisorAgent
```

Applies a ReActAgentConfig; no-op for other config types. Always returns self.

**Parameters**:

- **config** (Any): Configuration object. Takes effect when a ReActAgentConfig is supplied; ignored otherwise.

**Returns**:

**SupervisorAgent**: The current instance (supports chaining).

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

Creates a SupervisorAgent pre-loaded with sub-agent cards. Returns (agent_card, provider) compatible with HierarchicalTeam.add_agent().

**Parameters**:

- **agents** (List[AgentCard]): Sub-agent cards visible to this supervisor; must not be empty.
- **model_client_config** (Any): LLM client configuration.
- **model_request_config** (Any): LLM model and request configuration.
- **agent_card** (AgentCard): Supervisor agent identity card.
- **system_prompt** (str): Supervisor system prompt.
- **max_iterations** (int): Maximum ReAct iterations. Default: 5.
- **max_parallel_sub_agents** (int): Maximum concurrent sub-agent dispatches. Default: 10.

**Returns**:

**Tuple[AgentCard, AgentProvider]**: (agent_card, provider) where provider lazily constructs the supervisor instance.

**Exceptions**:

- Raises AGENT_TEAM_CREATE_RUNTIME_ERROR when `agents` is empty.
- Raises AGENT_TEAM_CREATE_RUNTIME_ERROR when any entry in `agents` is not an AgentCard.

---

## class openjiuwen.core.multi_agent.teams.hierarchical_msgbus.P2PAbilityManager

```python
class openjiuwen.core.multi_agent.teams.hierarchical_msgbus.P2PAbilityManager(
    supervisor: CommunicableAgent,
    max_parallel_sub_agents: int = 10
)
```

AbilityManager that routes AgentCard tool calls via TeamRuntime P2P send(). AgentCard calls are dispatched in parallel, bounded by max_parallel_sub_agents. All other ability types are forwarded to the base class execute() unchanged.

**Parameters**:

- **supervisor** (CommunicableAgent): The supervisor agent whose send() is used for P2P dispatch.
- **max_parallel_sub_agents** (int): Maximum concurrent AgentCard dispatches per execute() call; clamped to a minimum of 1. Default: 10.

---

### add

```python
add(self, card: AgentCard) -> AddAbilityResult
```

Registers a sub-agent card as a dispatchable tool.

**Parameters**:

- **card** (AgentCard): Sub-agent identity card.

**Returns**:

**AddAbilityResult**: Registration result; `added=True` for new registrations, `added=False` for duplicates (no-op).

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

Executes tool calls: dispatches AgentCard calls concurrently via P2P and delegates other calls to the base class. Results are returned in the original call order.

**Parameters**:

- **ctx** (AgentCallbackContext): Callback context for tool-call lifecycle hooks.
- **tool_call** (ToolCall | List[ToolCall]): Single or list of ToolCall objects from the LLM.
- **session** (Session): Current agent session.
- **tag** (Any, optional): Optional resource tag forwarded to the base class.

**Returns**:

**List[Tuple[Any, ToolMessage]]**: List of (result, ToolMessage) tuples in the original call order. Failed AgentCard dispatches return (None, error_ToolMessage).
