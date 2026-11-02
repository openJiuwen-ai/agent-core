# `ops.mcp.register` / `mcp.register` 特性实施记录

## 1. 范围与状态

本文记录 `ops.mcp.register` 的首版 Events 实现。该方法的职责是为当前会话绑定的
`DeepAgent` 添加并连接一个 MCP server：CORE 创建原生 `McpServerConfig`，调用
`Runner.resource_mgr.add_mcp_server()` 完成连接和工具发现，再把同一个配置加入当前
Agent 的 `AbilityManager`。本特性不执行发现出的 MCP 工具，也不自动广播到其他 Agent。

当前实现不使用 `ContextVar`。事件输入只保存不可变的纯声明值对象，不保存
`McpServerConfig`、MCP client、Agent 或 Manager 实例；运行时对象只在 CORE 内物化。

## 2. 调用契约

```python
await ops.mcp.register(
    McpRegisterSpec(
        name="notes",
        transport="stdio",
        connection=McpStdioSpec(
            command="python",
            args=("server.py",),
        ),
    )
)  # -> McpRegisterValue(server="notes")
```

`transport` 的类型为 `str`，当前按运行时校验支持的值；它不是一个额外的传输类型别名。
支持的声明为：

- `stdio` + `McpStdioSpec(command, args, cwd, env)`；
- `sse` 或 `streamable_http` + `McpHttpSpec(url, headers)`；
- `include_image_content` 和正整数 `timeout_seconds`。

Ops 将 `McpRegisterSpec` 快照为 `McpRegisterInput` 后触发 `mcp.register` 事件。USER /
BUILTIN Handler 可以使用同类型的新输入继续、改写或返回合法的 `McpRegisterOutput`
短路；事件输入不允许透传原生 `McpServerConfig`。

## 3. CORE 行为

CORE 从 `ops.session_context.session_id` 查询 `AgentRuntime`，只接受绑定的
`DeepAgent`。它按声明映射 native transport：`stdio` 映射为 `stdio`，
`streamable_http` 映射为 `streamable-http`；随后：

1. 检查当前 Agent 的 AbilityManager 是否已有同名能力；
2. 在 CORE 内创建 `McpServerConfig`；
3. 等待 `Runner.resource_mgr.add_mcp_server(config)` 的连接与工具发现结果；
4. 检查 ResourceManager `Result` 成功后调用 `agent.ability_manager.add(config)`；
5. 任一步失败时只清理本次 server ID 对应的原生资源，不报告虚假的成功。

`timeout_seconds` 对整个 ResourceManager 注册操作施加超时。凭据引用字段当前明确拒绝，
因为本仓库没有可复用的扩展注册 Host 或凭据解析授权接口；这避免把未授权的引用当成
明文运行时配置。插件归属、服务名加前缀、跨 Agent 生命周期和独立卸载能力留待宿主层
提供后再扩展，当前 public name 使用规范化后的 `name`。

## 4. 实现与登记位置

| 层 | 实现 |
| --- | --- |
| EventType / CORE | `builtins/event_types/mcp_register.py` |
| Ops Build | `builtins/handler_ops/mcp_register.py`，向已有 Builder 加入 `mcp.register` |
| Agent 查找 | `get_agent_runtime().get(SessionContext.session_id)` |
| 原生连接与发现 | `Runner.resource_mgr.add_mcp_server()` |
| 当前 Agent 能力登记 | `DeepAgent.ability_manager.add(McpServerConfig)` |
| 回归测试 | `tests/unit_tests/core/events/test_mcp_register_ops.py` |

`ExtensionEventBus` 的 builtin bootstrap 会自动发现两个 `register(bus)` 模块；宿主仍需
按现有流程触发一次 `ops.build`。

## 5. 验证记录

2026-11-02，Windows / Python 3.11 目标 worktree：

```text
uv run --frozen pytest tests/unit_tests/core/events/test_mcp_register_ops.py -q -o addopts=
8 passed, 2 warnings

uv run --frozen pytest tests/unit_tests/core/events -q -o addopts=
156 passed, 2 warnings
```

已验证声明到 native config 的转换、事件输入不携带运行时配置实例、重复名拒绝、
ResourceManager 失败、AbilityManager 绑定失败后的补偿、USER 短路和未绑定 Agent。
尚未在真实 stdio/SSE/streamable HTTP MCP 服务上联调，也未实现宿主权限、凭据解析、
插件卸载和跨 Agent 传播。
