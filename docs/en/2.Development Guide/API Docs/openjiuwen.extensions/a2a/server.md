# A2A Server

`A2AServer` and `A2AServerAdapter` expose an openjiuwen agent as an A2A service.
This page only keeps the startup path and the request flow.
Before using them, install `openjiuwen[all-a2a]`.

## Startup

```python
from openjiuwen.extensions.a2a.a2a_server import A2AServer
from openjiuwen.extensions.a2a.a2a_server_adapter import A2AServerAdapter
```

`A2AServer` can be started directly:

```python
server = A2AServer(agent_card=agent_card, interface_url="http://127.0.0.1:8000/a2a/jsonrpc/")
await server.start()
```

`A2AServerAdapter` is the runner-side bridge. When `RunnerConfig.enable_a2a=True`, `AgentAdapter`
resolves it through `create_server_adapter("A2A", ...)` and starts the A2A server for the local agent.

## Standard Runner Path

This is the framework-native path for exposing a local agent through A2A:

1. Set `distributed_mode=True`, otherwise `AgentMgr.add_agent()` will not create `AgentAdapter`
2. Set `enable_a2a=True`, otherwise `AgentAdapter` will not create the A2A server adapter
3. Call `add_agent(..., card=AgentCard(...), interface_url=...)`; the card is required, and `interface_url`
   can override the value stored on the card

When `AgentAdapter.start()` runs, it starts the MQ server first and, when A2A is enabled, also starts
`A2AServerAdapter`. If the `interface_url` can be parsed into a host and port, the adapter starts a
background uvicorn thread for the A2A server.

`AgentAdapter` binds the A2A handlers directly to `Runner.run_agent` and `Runner.run_agent_streaming`,
so the A2A server always delegates back to the agent registered under the same `agent_id`.

## Request Flow

Incoming A2A requests flow through:

1. A2A SDK request handler
2. `A2AAgentExecutor`
3. `A2ATransformer`
4. openjiuwen business logic
5. `TaskUpdater` event write-back

`A2AAgentExecutor` writes a `Task` snapshot before status updates so the current a2a-sdk client
can consume the stream correctly.
