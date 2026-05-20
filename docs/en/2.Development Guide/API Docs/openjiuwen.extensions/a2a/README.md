# openjiuwen.extensions.a2a

`openjiuwen.extensions.a2a` is the A2A integration layer for openjiuwen.
It covers two pieces:

- `A2AClient` for calling a remote A2A agent from openjiuwen
- `A2AServer` / `A2AServerAdapter` for exposing an openjiuwen agent as an A2A service

The `client.md` page also includes the request, response, and agent-card mapping details.

## How the plugin is used

A2A is shipped as an optional plugin bundle:

- Install the dependency bundle with `pip install "openjiuwen[all-a2a]"` or `uv sync --extra all-a2a`
- Importing `openjiuwen.extensions.a2a` registers the `A2A` protocol factories in the runner registries
- `RemoteAgent(protocol=ProtocolEnum.A2A, ...)` resolves `A2ARemoteClient`
- `RunnerConfig(enable_a2a=True)` makes `AgentAdapter` create and start the A2A server adapter
- The same plugin can also be registered through the `openjiuwen.remote_clients` and `openjiuwen.server_adapters` entry-point groups

## Flow at a glance

### Client side

1. A caller sends an openjiuwen-style payload, usually with `query` and `conversation_id`
2. `A2ATransformer.to_a2a_request()` turns the payload into an A2A `SendMessageRequest`
3. `A2AClient` sends the request through the A2A SDK client
4. The first response event is converted back into an openjiuwen `AgentResult` for `invoke()`
5. `stream()` yields every converted A2A event in order

### Server side

1. `AgentAdapter` decides whether A2A should be enabled from `RunnerConfig.enable_a2a`
2. `create_server_adapter("A2A", ...)` resolves the plugin implementation
3. `A2AServerAdapter` builds an `A2AServer` and, when possible, starts a uvicorn thread
4. `A2AServer` builds the A2A card, request handler, task store, and ASGI routes
5. `A2AAgentExecutor` converts A2A requests back into openjiuwen payloads, calls the local handlers, and writes `Task` / status / artifact events back to the A2A stream

## Page index

- [Client](client.md): `A2AClient`, remote client flow, and all A2A payload/card mappings.
- [Server](server.md): `A2AServer` and `A2AServerAdapter`.

## Practical notes

- `conversation_id` is the preferred openjiuwen input-session key; `sessionId` is kept for compatibility
- `A2AClient.invoke()` returns the first available event, not necessarily the final task snapshot
- `A2AClient.stream()` is the best fit when you want the full task lifecycle
- JSON-RPC mounts are normalized with a trailing slash to avoid redirect issues in streaming clients
