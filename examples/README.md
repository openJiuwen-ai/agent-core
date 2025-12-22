设计思路：
MCP SDK（ToolServerConfig + Runner + resource_mgr.tool()）已经把MCP Server → Tool 列表 → 调用工具这条链路封好了，要做的就是：

1.把 MCP server 注册进 resource_mgr.tool()。

2.用 Runner.list_tools(server_name) 拿到 McpToolCard。

3.把每个 McpToolCard 转成一个 LocalFunction（名字 / 描述 / 参数来自 JSON-Schema）。

4.在 LocalFunction._func 里，通过 Runner.run_tool(server_name.tool_name, arguments) 实际调用 MCP 工具。

5.把这些 LocalFunction 丢给 SuperReActAgent(tools=...)。

问题：
现在的 LocalFunction 例子都是同步（lambda a,b: a+b），MCP 工具是通过网络调用的，必须 async，所以 SuperReActAgent 在调用工具时需要类似await这样的逻辑