# tool

`openjiuwen.core.foundation.tool`是openJiuwen的工具模块，支持将开发者自定义工具转换成可被LLM识别与调用的工具。

**详细 API 文档**：[tool.md](./tool/tool.md)

**Classes**：

| CLASS | DESCRIPTION |
|-------|-------------|
| **Tool** | 工具基类。 |
| **LocalFunction** | 本地函数工具类。 |
| **RestfulApi** | RESTful API工具类。 |
| **MCPTool** | MCP工具类。 |
| **ToolCard** | 工具卡片类。 |
| **RestfulApiCard** | RESTful API工具卡片类。 |
| **McpToolCard** | MCP工具卡片类。 |
| **McpServerConfig** | MCP服务器配置类。 |
| **ToolInfo** | 工具信息类。 |
| **McpClient** | MCP客户端基类。 |
| **StdioClient** | 标准输入输出MCP客户端。 |
| **SseClient** | SSE MCP客户端。 |
| **PlaywrightClient** | Playwright MCP客户端。 |

**Functions**：

| FUNCTION | DESCRIPTION |
|----------|-------------|
| **tool** | 工具装饰器，用于快速定义工具。 |
| **Input** | 输入类型别名。 |
| **Output** | 输出类型别名。 |
