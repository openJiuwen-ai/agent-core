# MCP Examples

Five end-to-end examples — one per MCP transport type supported by openjiuwen.

| Transport | Port | Server tool | Use case |
|-----------|------|-------------|----------|
| SSE | 3001 | `fastmcp` | Calculator (add, subtract, multiply, divide, power) |
| Stdio | — | `fastmcp` | Text processing (word_count, reverse, uppercase …) |
| Streamable HTTP | 3002 | `fastmcp` | Note-taking CRUD |
| Playwright | 3003 | `fastmcp` + `playwright` | Browser automation (navigate, text, links, screenshot) |
| OpenAPI | 3004 | `aiohttp` | Task REST API converted to MCP tools |

Each transport type has **four client patterns**, shown in the table below:

| File | Pattern | Description |
|------|---------|-------------|
| `client_direct.py` | Direct client | Uses the transport client (`SseClient`, `StdioClient`, …) directly |
| `client_as_tool.py` | MCPTool wrapper | Wraps each discovered tool in `MCPTool` for a unified `Tool.invoke()` interface |
| `client_as_workflow.py` | Workflow | Creates an openjiuwen `Workflow`, binds an `MCPTool` to a `ToolComponent`, and executes it |
| `client_as_resources_runner.py` | Runner / ResourceMgr | Registers the MCP server, tools, and workflow through `Runner.resource_mgr`; runs via `Runner.run_workflow()` |

---

## Quick start

### SSE

```bash
# Terminal 1 — server
python sse/server.py

# Terminal 2 — direct client
python sse/client_direct.py

# Terminal 2 — MCPTool client
python sse/client_as_tool.py

# Terminal 2 — Workflow
python sse/client_as_workflow.py

# Terminal 2 — Runner / ResourceMgr
python sse/client_as_resources_runner.py
```

### Stdio

```bash
# No separate server needed — the client launches the server subprocess automatically

# Direct client
python stdio/client_direct.py

# MCPTool client
python stdio/client_as_tool.py

# Workflow
python stdio/client_as_workflow.py

# Runner / ResourceMgr
python stdio/client_as_resources_runner.py
```

### Streamable HTTP

```bash
# Terminal 1 — server
python streamable_http/server.py

# Terminal 2 — direct client
python streamable_http/client_direct.py

# Terminal 2 — MCPTool client
python streamable_http/client_as_tool.py

# Terminal 2 — Workflow
python streamable_http/client_as_workflow.py

# Terminal 2 — Runner / ResourceMgr
python streamable_http/client_as_resources_runner.py
```

### Playwright

```bash
# Install playwright if needed
pip install playwright && playwright install chromium

# Terminal 1 — server
python playwright/server.py

# Terminal 2 — direct client
python playwright/client_direct.py

# Terminal 2 — MCPTool client
python playwright/client_as_tool.py

# Terminal 2 — Workflow
python playwright/client_as_workflow.py

# Terminal 2 — Runner / ResourceMgr
python playwright/client_as_resources_runner.py
```

> **Tip:** You can also connect to the official Node.js Playwright MCP server via Stdio.
> See the commented-out section in `playwright/client_direct.py`.

### OpenAPI

```bash
# Terminal 1 — REST server
python openapi/server.py

# Terminal 2 — direct client (reads openapi.yaml, converts to MCP tools, calls server)
python openapi/client_direct.py

# Terminal 2 — MCPTool client
python openapi/client_as_tool.py

# Terminal 2 — Workflow
python openapi/client_as_workflow.py

# Terminal 2 — Runner / ResourceMgr
python openapi/client_as_resources_runner.py
```

---

## Workflow visualization

`client_as_workflow.py` and `client_as_resources_runner.py` both support an optional `--visualize` flag that
generates a `workflow.png` diagram in the same directory (requires network access to
`https://mermaid.ink`):

```bash
python sse/client_as_workflow.py --visualize
python sse/client_as_resources_runner.py --visualize
```

---

## How each example works

### SSE (`sse/`)

- **server.py** — `FastMCP` server running on `http://127.0.0.1:3001/sse` with calculator tools.
- **client_direct.py** — `SseClient` connects, lists tools, and calls each calculator operation directly.
- **client_as_tool.py** — discovers tools via `SseClient`, wraps each in `MCPTool`, and invokes via `Tool.invoke()`.
- **client_as_workflow.py** — builds a `Workflow` (`Start → ToolComponent[add] → End`), binds the `add` MCPTool to the `ToolComponent`, then invokes the workflow with `{"a": 7, "b": 3}`.
- **client_as_resources_runner.py** — registers the SSE server via `Runner.resource_mgr.add_mcp_server()`, retrieves the `add` tool via `get_mcp_tool()`, invokes it directly, then registers and runs the same workflow through `Runner.run_workflow()`.

### Stdio (`stdio/`)

- **server.py** — `FastMCP` server communicating over stdin/stdout (text processing tools). Launched as a subprocess by the client.
- **client_direct.py** — `StdioClient` launches `server.py` as a child process and calls text processing tools directly.
- **client_as_tool.py** — same subprocess launch, wraps discovered tools in `MCPTool`, invokes via `Tool.invoke()`.
- **client_as_workflow.py** — builds a `Workflow` (`Start → ToolComponent[word_count] → End`), binds the `word_count` MCPTool, and invokes with `{"text": "The quick brown fox …"}`.
- **client_as_resources_runner.py** — registers the Stdio server (subprocess) via `add_mcp_server()` with `client_type="stdio"` and `params`; the resource manager launches and manages the subprocess automatically. Retrieves `word_count` via `get_mcp_tool()`, invokes directly, then registers and runs the workflow.

### Streamable HTTP (`streamable_http/`)

- **server.py** — `FastMCP` server on `http://127.0.0.1:3002/mcp` with an in-memory note-taking service.
- **client_direct.py** — `StreamableHttpClient` connects and exercises the full CRUD API directly.
- **client_as_tool.py** — wraps note tools in `MCPTool` and runs the CRUD workflow via `Tool.invoke()`.
- **client_as_workflow.py** — builds a `Workflow` (`Start → ToolComponent[add_note] → End`), binds the `add_note` MCPTool, and invokes it three times with different note contents.
- **client_as_resources_runner.py** — registers the Streamable HTTP server via `add_mcp_server()` with `client_type="streamable-http"`, invokes `add_note` directly, then registers and runs the workflow three times.

### Playwright (`playwright/`)

- **server.py** — `FastMCP` SSE server on `http://127.0.0.1:3003/sse` wrapping `playwright-python` to expose browser tools.
- **client_direct.py** — `PlaywrightClient` connects via SSE and calls `browser_navigate`, `browser_get_text`, `browser_get_links`, and `browser_take_screenshot` directly.
- **client_as_tool.py** — wraps all four browser tools in `MCPTool` and invokes via `Tool.invoke()`.
- **client_as_workflow.py** — builds a `Workflow` (`Start → ToolComponent[browser_navigate] → End`), binds the `browser_navigate` MCPTool, and invokes with `{"url": "https://example.com"}`.
- **client_as_resources_runner.py** — registers the Playwright server via `add_mcp_server()` with `client_type="playwright"`, invokes `browser_navigate` directly, then registers and runs the workflow.

### OpenAPI (`openapi/`)

- **openapi.yaml** — OpenAPI 3.0 spec describing a Task REST API.
- **server.py** — an `aiohttp` server on `http://127.0.0.1:3004` that implements the spec.
- **client_direct.py** — `OpenApiClient` reads `openapi.yaml`, auto-generates MCP tools for each endpoint (using `fastmcp`), and invokes them — which in turn makes real HTTP calls to `server.py`.
- **client_as_tool.py** — same spec loading, wraps generated tool cards in `MCPTool`, and invokes via `Tool.invoke()`.
- **client_as_workflow.py** — builds a `Workflow` (`Start → ToolComponent[create_task] → End`), binds the `create_task` MCPTool, and invokes three times to create task items via the REST API.
- **client_as_resources_runner.py** — registers the OpenAPI server via `add_mcp_server()` with `client_type="openapi"` (spec path as `server_path`); the resource manager auto-generates MCP tools from the spec. Invokes `create_task` directly, then registers and runs the workflow three times.

---

## Four client patterns explained

### 1. Direct client (`client_direct.py`)

Uses the transport-specific client class directly. Best for scripting or when you need full control over the MCP protocol.

```python
from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.sse_client import SseClient

client = SseClient(McpServerConfig(
    server_name="my-server",
    server_path="http://127.0.0.1:3001/sse",
    client_type="sse",
))
await client.connect()
tool_cards = await client.list_tools()
result = await client.call_tool("add", {"a": 7, "b": 3})
await client.disconnect()
```

### 2. MCPTool wrapper (`client_as_tool.py`)

Wraps each `McpToolCard` in `MCPTool`, giving it the standard `Tool.invoke()` interface. MCP tools become interchangeable with `LocalFunction` and `RestfulApi` tools.

```python
from openjiuwen.core.foundation.tool.mcp.base import MCPTool

tool_cards = await client.list_tools()
tools = {card.name: MCPTool(mcp_client=client, tool_info=card) for card in tool_cards}
result = await tools["add"].invoke({"a": 7, "b": 3})
# → {"result": "10.0"}
```

### 3. Workflow integration (`client_as_workflow.py`)

Binds an `MCPTool` to a `ToolComponent` and wires it into an openjiuwen `Workflow`. The workflow engine handles input resolution, state management, and output formatting.

```python
from openjiuwen.core.workflow import (
    Workflow, WorkflowCard, Start, End,
    ToolComponent, ToolComponentConfig, create_workflow_session,
)

# Build the workflow
workflow = Workflow(card=WorkflowCard(id="my_workflow", name="My Workflow", version="1.0.0"))
workflow.set_start_comp("start", Start(), inputs_schema={"a": "${a}", "b": "${b}"})

tool_comp = ToolComponent(ToolComponentConfig()).bind_tool(mcp_tools["add"])
workflow.add_workflow_comp("tool", tool_comp, inputs_schema={"a": "${start.a}", "b": "${start.b}"})

end = End({"response_template": "Result: {{result}}"})
workflow.set_end_comp("end", end, inputs_schema={"result": "${tool.data}"})

workflow.add_connection("start", "tool")
workflow.add_connection("tool", "end")

# Execute
session = create_workflow_session()
result = await workflow.invoke({"a": 7, "b": 3}, session)
# → {"response": "Result: {'result': '10.0'}"}
```

### 4. Runner / ResourceMgr (`client_as_resources_runner.py`)

Registers the MCP server, tools, and workflow through the global `Runner` and its `ResourceMgr`. The Runner owns all resource lifecycle (connection management, cleanup) and provides a unified execution entry point.

```python
from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.workflow import Workflow, WorkflowCard

await Runner.start()

# Register MCP server — connects and discovers tools automatically
config = McpServerConfig(
    server_id="my-server-01",
    server_name="my-server",
    server_path="http://127.0.0.1:3001/sse",
    client_type="sse",
)
await Runner.resource_mgr.add_mcp_server(config, tag=["mcp"])

# Retrieve and invoke a tool directly
add_tool = await Runner.resource_mgr.get_mcp_tool(name="add", server_name="my-server")
result = await add_tool.invoke({"a": 7, "b": 3})
# → {"result": "10.0"}

# Register and run a workflow by ID
Runner.resource_mgr.add_workflow(
    WorkflowCard(id="my_workflow", name="My Workflow", version="1.0.0"),
    lambda: workflow,
)
result = await Runner.run_workflow("my_workflow", inputs={"a": 7, "b": 3})

# Clean up
await Runner.resource_mgr.remove_mcp_server(server_name="my-server")
Runner.resource_mgr.remove_workflow(workflow_id="my_workflow")
await Runner.stop()
```

**Key differences from the direct workflow pattern:**

| Aspect | `client_as_workflow.py` | `client_as_resources_runner.py`          |
|--------|-------------------|------------------------------------------|
| MCP connection | Manual (`SseClient`, `connect()`, `disconnect()`) | Managed by `ResourceMgr`                 |
| Tool access | Local variable | `Runner.resource_mgr.get_mcp_tool()`     |
| Workflow execution | `workflow.invoke(inputs, session)` | `Runner.run_workflow(id, inputs=inputs)` |
| Cleanup | `client.disconnect()` | `remove_mcp_server()` + `Runner.stop()`  |

**`client_type` values for `McpServerConfig`:**

| Transport | `client_type` | `server_path` |
|-----------|--------------|---------------|
| SSE | `"sse"` | SSE endpoint URL |
| Stdio | `"stdio"` | `""` (use `params` for subprocess command) |
| Streamable HTTP | `"streamable-http"` | HTTP endpoint URL |
| Playwright | `"playwright"` | SSE endpoint URL of the Playwright server |
| OpenAPI | `"openapi"` | Path to the OpenAPI spec file (`.yaml` / `.json`) |
