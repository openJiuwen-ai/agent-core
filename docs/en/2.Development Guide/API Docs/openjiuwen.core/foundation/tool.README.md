# tool

`openjiuwen.core.foundation.tool` is the tool module of openJiuwen, supporting conversion of developer-defined tools into tools that can be recognized and invoked by LLMs.

**Detailed API Documentation**:

[tool.md](./tool/tool.md)
[auth.md](./tool/auth/auth.md)
[handler.md](tool/form_handler/form_handler.md)

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
| **ToolAuthConfig** | Tool authentication configuration data class. |
| **ToolAuthResult** | Tool authentication result data class. |
| **AuthType** | Authentication type enum. |
| **AuthStrategy** | Authentication strategy abstract base class. |
| **SSLAuthStrategy** | SSL authentication strategy. |
| **HeaderQueryAuthStrategy** | Custom Header and Query parameter authentication strategy. |
| **AuthStrategyRegistry** | Authentication strategy registry. |
| **AuthHeaderAndQueryProvider** | Custom Header and Query parameter authentication provider. |
| **FormHandler** | Form handler abstract base class. |
| **DefaultFormHandler** | Default form handler. |
| **FormHandlerManager** | Form handler manager. |

**Functions**:

| FUNCTION | DESCRIPTION |
|----------|-------------|
| **tool** | Tool decorator for quickly defining tools. |
| **Input** | Input type alias. |
| **Output** | Output type alias. |
