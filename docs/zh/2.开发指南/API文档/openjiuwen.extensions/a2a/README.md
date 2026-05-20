# openjiuwen.extensions.a2a

这一组文档介绍 `openjiuwen` 的 A2A 集成层。
它覆盖两类能力：

- `A2AClient`，用于从 openjiuwen 调用远程 A2A Agent
- `A2AServer` / `A2AServerAdapter`，用于把 openjiuwen Agent 暴露为 A2A 服务

`client.md` 页面中还包含请求、响应和 AgentCard 的映射说明。

## 插件使用方式

A2A 以可选插件包的方式提供：

- 安装依赖包：`pip install "openjiuwen[all-a2a]"` 或 `uv sync --extra all-a2a`
- 导入 `openjiuwen.extensions.a2a` 后，会把 `A2A` 协议工厂注册到 runner 的注册表中
- `RemoteAgent(protocol=ProtocolEnum.A2A, ...)` 会解析到 `A2ARemoteClient`
- `RunnerConfig(enable_a2a=True)` 会让 `AgentAdapter` 自动创建并启动 A2A 服务适配器
- 也可以通过 `openjiuwen.remote_clients` 和 `openjiuwen.server_adapters` 的 entry point 做注册

## 流程概览

### 客户端侧

1. 调用方传入 openjiuwen 风格载荷，通常包含 `query` 和 `conversation_id`
2. `A2ATransformer.to_a2a_request()` 把载荷转成 A2A `SendMessageRequest`
3. `A2AClient` 通过 A2A SDK 发送请求
4. `invoke()` 取第一个可用事件并转换成 openjiuwen `AgentResult`
5. `stream()` 则逐条返回转换后的 A2A 事件

### 服务端侧

1. `AgentAdapter` 根据 `RunnerConfig.enable_a2a` 决定是否启用 A2A
2. `create_server_adapter("A2A", ...)` 解析到插件实现
3. `A2AServerAdapter` 构建 `A2AServer`，并在可用时启动 uvicorn 线程
4. `A2AServer` 构建 A2A card、请求处理器、任务存储和 ASGI 路由
5. `A2AAgentExecutor` 把 A2A 请求转换回 openjiuwen 载荷，调用本地处理函数，并把 `Task` / 状态 / artifact 事件写回 A2A 流

## 页面索引

- [客户端](客户端.md)：`A2AClient`、远程客户端流程，以及全部 A2A 载荷/卡片映射。
- [服务端](服务端.md)：`A2AServer` 和 `A2AServerAdapter`。

## 使用要点

- `conversation_id` 是更推荐的 openjiuwen 会话输入键，`sessionId` 仅用于兼容
- `A2AClient.invoke()` 返回的是第一个可用事件，不一定是最终任务快照
- 如果你需要完整任务生命周期，优先使用 `A2AClient.stream()`
- JSON-RPC 挂载路径会自动补齐结尾斜杠，避免流式客户端遭遇重定向
