# A2A Server

`A2AServer` and `A2AServerAdapter` are used to expose an openjiuwen Agent as an A2A service.
This page covers only the startup method and request flow.
Before using, please install `openjiuwen[all-a2a]` first.

## Startup Method

```python
from openjiuwen.extensions.a2a.a2a_server import A2AServer
from openjiuwen.extensions.a2a.a2a_server_adapter import A2AServerAdapter
```

`A2AServer` can be started directly:

```python
server = A2AServer(agent_card=agent_card, interface_url="http://127.0.0.1:8000/a2a/jsonrpc/")
await server.start()
```

`A2AServerAdapter` is the runner-side bridge layer. When `RunnerConfig.enable_a2a=True`, `AgentAdapter`
will parse it via `create_server_adapter("A2A", ...)` and start the A2A service for the local agent.

## Standard Path

This is the standard path for exposing a local agent as A2A within the framework:

1. Set `distributed_mode=True`, otherwise `AgentMgr.add_agent()` will not create `AgentAdapter`
2. Set `enable_a2a=True`, otherwise `AgentAdapter` will not create the A2A service adapter
3. Call `add_agent(..., card=AgentCard(...), interface_url=...)`; `card` is required, `interface_url`
   can override the same-named configuration on the card

When `AgentAdapter.start()` runs, it will first start the MQ server; if A2A is enabled, it will also continue to start
`A2AServerAdapter`. If `interface_url` can resolve host and port, the adapter will also start a uvicorn thread in the background to run the A2A service.

The A2A handler in `AgentAdapter` is directly bound to `Runner.run_agent` and `Runner.run_agent_streaming`,
so the A2A service will callback to the agent registered under the same `agent_id`.

## Request Flow

After an A2A request enters the server, it will pass through in sequence:

1. A2A SDK request handler
2. `A2AAgentExecutor`
3. `A2ATransformer`
4. openjiuwen business logic
5. `TaskUpdater` event write-back

`A2AAgentExecutor` will first write the `Task` snapshot, then send the status update event, so that the current a2a-sdk client can correctly consume streaming results.
