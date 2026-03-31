# tool

`openjiuwen.core.foundation.tool`是openJiuwen的工具模块，支持将开发者自定义工具转换成可被LLM识别与调用的工具。

**详细 API 文档**：

[tool.md](./tool/tool.md)
[auth.md](./tool/auth/auth.md)
[handler.md](tool/form_handler/form_handler.md)

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
| **ToolAuthConfig** | 工具认证配置数据类。 |
| **ToolAuthResult** | 工具认证结果数据类。 |
| **AuthType** | 认证类型枚举。 |
| **AuthStrategy** | 认证策略抽象基类。 |
| **SSLAuthStrategy** | SSL认证策略。 |
| **HeaderQueryAuthStrategy** | 自定义Header和Query参数认证策略。 |
| **AuthStrategyRegistry** | 认证策略注册表。 |
| **AuthHeaderAndQueryProvider** | 自定义Header和Query参数认证提供器。 |
| **FormHandler** | 表单处理器抽象基类。 |
| **DefaultFormHandler** | 默认表单处理器。 |
| **FormHandlerManager** | 表单处理器管理器。 |

**Functions**：

| FUNCTION | DESCRIPTION |
|----------|-------------|
| **tool** | 工具装饰器，用于快速定义工具。 |
| **Input** | 输入类型别名。 |
| **Output** | 输出类型别名。 |
