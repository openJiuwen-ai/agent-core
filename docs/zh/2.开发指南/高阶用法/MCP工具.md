# MCP工具

MCP（Model Context Protocol）是一种用于在AI智能体与外部工具/服务之间进行通信的协议。openjiuwen框架提供了完整的MCP客户端实现，支持多种传输协议，使Agent能够调用任何符合MCP协议的工具服务。

## 1. 核心能力

| 能力 | 说明 |
|------|------|
| **多传输协议** | 支持 SSE、Stdio、Streamable-HTTP 三种标准协议 |
| **本地进程对接** | 通过 Stdio 与本地 MCP Server 通信 |
| **远程服务对接** | 通过 SSE/Streamable-HTTP 对接远程 MCP 服务 |
| **OpenAPI 转换** | 将 OpenAPI 规范自动转换为 MCP 工具 |
| **Playwright 支持** | 提供浏览器自动化 MCP 客户端 |
| **认证机制** | 支持 Header/Query 参数认证 |
| **工具发现** | 自动列出并注册 MCP 服务器提供的所有工具 |
| **生命周期管理** | MCP 服务器的添加、刷新、移除 |
| **工具缓存** | 支持 `expiry_time` 过期机制，定时刷新工具列表 |

## 2. 环境准备与核心依赖

### 安装依赖

```bash
pip install mcp httpx anyio
```

### 核心导入

```python
from openjiuwen.core.foundation.tool import (
    McpServerConfig,   # MCP服务器配置
    McpToolCard,       # MCP工具卡片
    MCPTool,           # MCP工具实例
)
from openjiuwen.core.runner import Runner
from openjiuwen.harness.schema.config import DeepAgentConfig
```

---

## 3. 快速接入指南

### 3.1 创建一个简单的MCP Server示例

本节演示一个最简单的 MCP Server：天气查询服务。该服务暴露一个 `get_weather` 工具，Agent 可以通过自然语言调用它来查询任意城市的天气。

**实现功能：**
- 工具名称：`get_weather`
- 输入参数：`city`（字符串，城市名）
- 返回内容：天气描述和温度

**代码示例：**

```python
# server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-server")

@mcp.tool()
def get_weather(city: str) -> str:
    """获取城市天气"""
    return f"{city}的天气是晴天，25°C"

if __name__ == "__main__":
    mcp.run(transport="stdio")  # 使用stdio传输
```

### 3.2 在Agent中配置并调用MCP工具

本节演示如何在 DeepAgent 中配置并调用 3.1 创建的 MCP Server。通过在 `DeepAgentConfig` 中传入 `McpServerConfig`，Agent 启动时会自动连接 MCP 服务器并注册其提供的工具。

**实现功能：**
- 配置 MCP 服务器连接参数（server_id、server_name、server_path、client_type）
- 将 MCP 服务器挂载到 Agent
- 通过自然语言让 Agent 调用 MCP 工具

**代码示例：**

```python
# agent_example.py
import asyncio
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

# 注意：Model 需要提前配置好，参考 ModelClientConfig + ModelRequestConfig + Model 的标准创建流程

async def main():
    # 配置 MCP 服务器（对应 3.1 启动的本地 MCP Server）
    mcp_config = McpServerConfig(
        server_id="my-weather-server",
        server_name="my-server",
        server_path="python server.py",  # stdio 传输，指向 3.1 的 server.py
        client_type="stdio",
    )

    # 创建 Agent 配置并将 MCP 服务器挂载上去
    agent_config = DeepAgentConfig(
        model=model,  # 此处传入已配置好的 Model 实例
        card=AgentCard(name="assistant", description="助手"),
        mcps=[mcp_config],  # 传入 MCP 配置列表
    )

    agent = DeepAgent(agent_config.card).configure(agent_config)

    # Agent 启动后会：
    # 1. 自动连接 MCP 服务器（通过 stdio 启动 server.py 进程）
    # 2. 调用 list_tools() 获取工具列表（get_weather）
    # 3. 将工具注册为 Agent 可用能力

    result = await agent.invoke("北京今天的天气怎么样？")
    print(result)

asyncio.run(main())
```


## 4. 通讯协议 (Transports) 配置指南

### McpServerConfig 核心字段

McpServerConfig 包含以下核心配置项：

| 字段 | 类型 | 说明 |
|------|------|------|
| `server_id` | str | 服务器唯一标识（默认自动生成） |
| `server_name` | str | 服务器名称 |
| `server_path` | str | 服务器路径/地址/文件路径 |
| `client_type` | str | 传输类型：sse/stdio/streamable-http/openapi/playwright |
| `params` | dict | 额外参数（如 StdioServerParameters 的 command/args） |
| `auth_headers` | dict | HTTP认证Header（仅SSE/StreamableHttp） |
| `auth_query_params` | dict[str, str] | HTTP认证Query参数 |

### 4.1 基于 SSE 的远程服务对接

SSE（Server-Sent Events）适用于可通过 HTTP GET 请求建立长连接的远程 MCP 服务。

```python
# SSE 配置示例
mcp_config = McpServerConfig(
    server_id="remote-weather-api",
    server_name="weather-service",
    server_path="https://api.example.com/mcp/sse",  # SSE端点
    client_type="sse",
    auth_headers={"Authorization": "Bearer xxx"},    # 可选：认证
)
```

### 4.2 基于 stdio 的本地进程对接

Stdio 适用于本地进程通信，启动子进程并通过标准输入输出通信。

```python
# stdio 配置示例
mcp_config = McpServerConfig(
    server_id="local-git-server",
    server_name="git-tools",
    server_path="python /path/to/mcp-git-server.py",  # 本地命令
    client_type="stdio",
    # params={"args": ["--verbose"]},  # 可选：传递额外参数给进程
)
```

### 4.3 基于 Streamable-HTTP 的对接

Streamable-HTTP 适用于支持流式 HTTP 的 MCP 服务。

```python
# Streamable-HTTP 配置示例
mcp_config = McpServerConfig(
    server_id="http-mcp-server",
    server_name="http-tools",
    server_path="https://api.example.com/mcp/streamable",
    client_type="streamable-http",
    auth_headers={"X-API-Key": "xxx"},  # 认证头
)
```

### 4.4 OpenAPI 转换客户端

将现有的 OpenAPI 规范转换为 MCP 工具：

```python
# OpenAPI 配置示例
mcp_config = McpServerConfig(
    server_id="openapi-converter",
    server_name="openapi-tools",
    server_path="openapi.json",
    client_type="openapi",
)
```

### 4.5 Playwright 浏览器自动化

Playwright 适用于需要浏览器自动化操作的 MCP 服务，如网页抓取、UI 测试等场景。

```python
# Playwright 配置示例
mcp_config = McpServerConfig(
    server_id="playwright-browser",
    server_name="browser-automation",
    server_path="http://localhost:9222",  # Playwright MCP Server 地址
    client_type="playwright",
)
```

---

## 5. 行业客户"最佳实践"完整 Demo

### 场景：银行理财助手 Agent

本节演示金融行业场景：银行智能客服 Agent 通过 MCP 工具调用后台理财系统，帮助用户查询理财产品信息并完成购买。

**业务背景：**
- 用户希望通过自然语言查询理财产品、比较收益率
- 用户希望直接通过对话完成理财产品购买
- 银行后端系统以 MCP 工具形式提供服务能力

**实现功能：**
- 查询理财产品（名称、收益率、风险等级、起投金额）
- 购买指定理财产品（输入产品名称和金额）

**代码示例：**

```python
# financial_agent_demo.py
import asyncio
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

async def financial_agent_demo():
    # MCP 服务器列表（模拟银行理财系统）
    mcps = [
        # 理财产品查询服务（本地stdio）
        McpServerConfig(
            server_id="fin-product-query",
            server_name="financial-product-service",
            server_path="python fin_product_server.py",
            client_type="stdio",
        ),
        # 理财产品购买服务（远程SSE）
        McpServerConfig(
            server_id="fin-product-purchase",
            server_name="financial-purchase-service",
            server_path="https://api.bank.com/mcp/sse/purchase",
            client_type="sse",
            auth_headers={"X-Bank-Token": "bank-auth-token"},
        ),
    ]

    # 配置 Agent（Model 需要提前配置好）
    agent_config = DeepAgentConfig(
        model=model,  # 此处传入已配置好的 Model 实例
        card=AgentCard(
            name="bank-assistant",
            description="银行理财助手：查询理财产品、完成购买"
        ),
        mcps=mcps,
        max_iterations=15,
    )

    agent = DeepAgent(agent_config.card).configure(agent_config)

    # 自然语言调用 MCP 工具
    tasks = [
        "现在有什么稳健型理财产品，收益率是多少？",
        "我想购买50万的稳盈宝理财产品",
    ]

    for task in tasks:
        print(f"\n任务: {task}")
        result = await agent.invoke(task)
        print(f"结果: {result}")

asyncio.run(financial_agent_demo())
```

---

## 6. 常见问题排查 (FAQ)

### Q1: MCP 服务器连接失败

**现象：** Agent 启动时报连接超时、或提示无法建立连接。

**排查步骤：**
1. 检查 `server_path` 是否可访问（浏览器直接打开 URL 测试）
2. 确认 `client_type` 与服务器传输类型匹配（stdio 用 stdio，sse 用 sse）
3. 检查网络连通性（防火墙、代理、VPN）
4. 确认 MCP Server 服务已启动

**示例：**
```python
# 排查：确认 server_path 可访问
# SSE 场景
server_path = "https://api.example.com/mcp/sse"  # 用浏览器直接访问测试

# stdio 场景
server_path = "python /path/to/server.py"  # 手动运行确认无语法错误
```

### Q2: 认证 Token 过期或无效

**现象：** MCP 服务器返回 401/403 错误，或工具调用时返回认证失败。

**排查步骤：**
1. 检查 `auth_headers` 中的 Token 是否正确
2. 确认 Token 未过期
3. 检查 Token 格式是否与服务器要求一致（如 `Bearer xxx` vs 直接传 `xxx`）

**示例：**
```python
# 错误写法：Token 可能已过期
auth_headers={"Authorization": "Bearer old-token"}

# 正确写法：使用环境变量或最新 Token
import os
auth_headers={"Authorization": f"Bearer {os.getenv('MCP_API_TOKEN')}"}
```

### Q3: 如何动态刷新 MCP 工具列表

**现象：** MCP Server 运行时新增或删除了工具，但 Agent 仍然看到旧的工具列表。

**排查步骤：**
1. 确认 MCP Server 已正确启动并注册了目标工具
2. 调用 ResourceManager 的刷新接口，重新获取工具列表
3. 刷新后验证新工具是否已注册

**示例：**
```python
from openjiuwen.core.runner import Runner

# 按 server_id 刷新
result = await Runner.resource_mgr.refresh_mcp_server(server_id="my-server")
print(f"刷新结果: {result}")

# 或按名称刷新
result = await Runner.resource_mgr.refresh_mcp_server(server_name="my-server")

# 刷新后查看当前工具列表
tools = Runner.resource_mgr.get_tool_ids()
print(f"当前工具: {tools}")
```

### Q4: 工具调用返回结果不符合预期

**现象：** 工具调用成功，但返回结果为空、格式异常、或与预期不符。

**排查步骤：**
1. 检查 MCP Server 返回的数据格式是否符合预期
2. 确认 `inputSchema` 参数定义与实际传入参数匹配
3. 查看 MCP Server 日志，定位是 Server 端问题还是 Client 端问题
4. 如果是网络代理场景，确认代理未修改响应内容

**示例：**
```python
# 在调用处添加日志，打印完整返回
result = await agent.invoke("查询天气")
print(f"原始返回: {result}")  # 确认返回内容

# 检查是否是参数传递问题
# MCP Server 的 inputSchema 应与 Agent 传参一致
```

---

## 7. 扩展阅读

### MCP Protocol Specification
**官方协议规范文档**：https://modelcontextprotocol.io/docs/getting-started/intro  
涵盖 MCP 协议的核心概念、架构设计和快速入门指引。

### MCP Transport Specification
**传输层协议规范**：https://modelcontextprotocol.io/specification/2025-06-18/basic/transports  
详细定义 SSE、Stdio、HTTP Streamable 等传输协议的实现规范。

### @openjiuwen/agent-core
**源码仓库**：https://gitcode.com/openJiuwen/agent-core/tree/develop/openjiuwen/core/foundation/tool/mcp  
openjiuwen MCP 相关实现位于 `openjiuwen/core/foundation/tool/mcp/` 目录。

