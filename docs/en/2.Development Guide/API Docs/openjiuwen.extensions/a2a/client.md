# A2A Client

`A2AClient` is the openjiuwen wrapper around the A2A SDK client.
It accepts openjiuwen-style inputs and converts them into A2A requests.

## Main API

```python
from openjiuwen.extensions.a2a.a2a_client import A2AClient
```

- `A2AClient(card: AgentCard | None = None, polling: bool = False)`
  - Creates an A2A client from an A2A `AgentCard`.
  - `polling` is forwarded to the underlying A2A SDK client configuration.
  - The wrapper resolves the SDK client once at construction time.
- `await client.invoke(inputs: dict[str, Any]) -> AgentResult`
  - Sends a request and returns the first available event as an `AgentResult`.
  - This matches the SDK's `send_message()` iterator model, which does not expose a separate HTTP "invoke" RPC.
- `async for chunk in client.stream(inputs: dict[str, Any])`
  - Sends a streaming request and yields each event as an `AgentResult` chunk.
- `await client.stop()`
  - Closes the underlying A2A client.

## Input Convention

The client expects openjiuwen-style request dictionaries. The most common keys are:

- `query`: user input text
- `conversation_id`: preferred session identifier at the openjiuwen runner layer
- `sessionId`: accepted for compatibility and normalized to the same result session value
- extra metadata fields are passed through when possible
- `conversation_id` is the preferred upstream session key; `sessionId` remains supported for compatibility
- `task_id` is not created on the client side; the remote A2A server usually generates it

The client delegates request conversion to `A2ATransformer.to_a2a_request()`.

## Output Convention

Both `invoke()` and `stream()` return openjiuwen `AgentResult` objects after converting A2A payloads back through `A2ATransformer.from_a2a_response()`.
`sessionId` on the returned result is normalized from the caller's `conversation_id` when present, while `sessionId` remains a compatibility fallback input.
`invoke()` returns the first available event, while `stream()` yields the event stream chunk by chunk.

## Payload and Card Mapping

`A2AClient` relies on two conversion helpers:

- `A2ATransformer` for request and response conversion
- `A2AAgentCardAdapter` for converting a local openjiuwen card into an A2A card

### Request mapping

| openjiuwen input field | A2A field | Notes |
| --- | --- | --- |
| `query` | `message.parts[0].text` | The first text part becomes the user message text. |
| `conversation_id` / `sessionId` | `message.context_id` | The session identifier is normalized into the A2A context. |
| other top-level keys | `send_request.metadata` | Extra keys are copied into A2A metadata if they are not `query`, `conversation_id`, or `sessionId`. |
| `metadata` | `send_request.metadata` | Merged into A2A metadata when provided as a dict. |

### Response mapping

| A2A source object / field | openjiuwen `AgentResult` field | Notes |
| --- | --- | --- |
| `Message.task_id` | `task_id` | Preserved as the result task identifier. |
| `Message.context_id` | `sessionId` | Preserved as the result session identifier. |
| `Message.parts` | `artifacts[0].parts` | The message content becomes a single artifact named `message`. |
| `Task.id` | `task_id` | Preserved as the result task identifier. |
| `Task.context_id` | `sessionId` | Preserved as the result session identifier. |
| `Task.status.state` | `status` | Mapped to openjiuwen `TaskStatus`. |
| `Task.artifacts` | `artifacts` | Converted artifact by artifact. |
| `TaskStatusUpdateEvent` / `TaskArtifactUpdateEvent` | `AgentResult` | Stream events are converted into incremental openjiuwen results. |

### Agent card mapping

| openjiuwen `AgentCard` field | A2A `AgentCard` field | Notes |
| --- | --- | --- |
| `name` | `name` | Copied directly. |
| `description` | `description` | Copied directly, with input/output params appended when present. |
| `input_params` | `description` | Serialized into the description text as `[input_params] ...`. |
| `output_params` | `description` | Serialized into the description text as `[output_params] ...`. |
| `interface_url` | `supported_interfaces[0].url` | Added when provided and no explicit `supported_interfaces` are supplied. |
| `protocol_binding` | `supported_interfaces[0].protocol_binding` | Defaults to `HTTP+JSON`, unless overridden. |
| `protocol_version` | `supported_interfaces[0].protocol_version` | Defaults to `1.0`, unless overridden. |
| `tenant` | `supported_interfaces[0].tenant` | Preserved when provided. |
| `supported_interfaces` | `supported_interfaces` | Explicit interface entries take priority over `interface_url`. |

### Part mapping

| openjiuwen `Part` field | A2A `Part` field | Notes |
| --- | --- | --- |
| `text` | `text` | Copied directly. |
| `raw` | `raw` | Copied directly. |
| `url` | `url` | Copied directly. |
| `data` (dict) | `data.struct_value` | Dicts are serialized into protobuf struct values. |
| `data` (other) | `data.string_value` | Non-dict values are stringified. |
| `filename` | `filename` | Copied directly. |
| `media_type` | `media_type` | Copied directly. |
| `metadata` | `metadata` | Serialized into protobuf struct values. |

## Plugin usage

In runner-based integrations you usually do not instantiate `A2AClient` directly.
Instead:

- `RemoteAgent(protocol=ProtocolEnum.A2A, ...)` resolves the `A2A` remote client plugin
- `openjiuwen.extensions.a2a` registers the factory via the runner registry
- `pip install "openjiuwen[all-a2a]"` or `uv sync --extra all-a2a` installs the A2A SDK dependency bundle

If you need to use the client outside `RemoteAgent`, instantiate `A2AClient` directly and pass an A2A agent card.

## Direct Protocol Usage

Use this path when you want to create an A2A remote agent explicitly:

```python
from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner.drunner.remote_client.remote_client_config import ProtocolEnum
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

agent = RemoteAgent(
    agent_id="a2a-agent",
    protocol=ProtocolEnum.A2A,
    config={
        "url": "http://127.0.0.1:41241",
        "kwargs": {
            "card": AgentCard(id="a2a-agent", name="a2a-agent"),
            "polling": True,
        },
    },
)
```

The call chain is:

`RemoteAgent -> create_remote_client("A2A") -> A2ARemoteClient -> A2AClient -> A2A SDK`

### Parameters

| Parameter | Meaning |
| --- | --- |
| `card` | Required by the remote client path. Here it is the openjiuwen `AgentCard` that gets converted into an A2A card. |
| `polling` | Optional, defaults to `False`. It is forwarded to the underlying A2A SDK client configuration. |

### Polling behavior

`polling` controls whether the underlying A2A SDK client performs automatic task polling after the message is sent.

| Setting | Behavior | Result shape |
| --- | --- | --- |
| `polling=True` | The client keeps polling the A2A task until it reaches a terminal state. | Usually the final result, such as a completed `Message` or `Artifact`. |
| `polling=False` | The client does not auto-poll after sending the request. | A task-oriented response may be returned first, and you take over lifecycle management manually. |

When you manage the lifecycle yourself, use the returned task identifier to query task state or resubscribe to the task stream with the SDK's task APIs.

## Example

```python
from openjiuwen.extensions.a2a.a2a_client import A2AClient

client = A2AClient(card=a2a_agent_card)
result = await client.invoke({"query": "hello", "conversation_id": "conv-1"})
```
