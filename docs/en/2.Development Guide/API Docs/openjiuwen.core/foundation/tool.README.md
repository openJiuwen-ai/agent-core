# tool

`openjiuwen.core.foundation.tool` is the tool module of openJiuwen, supporting conversion of developer-defined tools into tools that can be recognized and invoked by LLMs.

**Detailed API Documentation**: [tool.md](./tool/tool.md)

**Classes**:

| CLASS | DESCRIPTION |
|-------|-------------|
| **Tool** | Tool base class. |
| **LocalFunction** | Local function tool class. |
| **RestfulApi** | RESTful API tool class. |
| **MCPTool** | MCP tool class. |
| **ToolCard** | Tool card class. |
| **RestfulApiCard** | RESTful API tool card class. |
| **McpToolCard** | MCP tool card class. |
| **McpServerConfig** | MCP server configuration class. |
| **ToolInfo** | Tool information class. |
| **McpClient** | MCP client base class. |
| **StdioClient** | Standard input/output MCP client. |
| **SseClient** | SSE MCP client. |
| **PlaywrightClient** | Playwright MCP client. |

**Functions**:

| FUNCTION | DESCRIPTION |
|----------|-------------|
| **tool** | Tool decorator for quickly defining tools. |
| **Input** | Input type alias. |
| **Output** | Output type alias. |
