# EV-13 MCP 事件与 `ops.mcp` 能力

> 建档日期：2026-10-29
>
> 方案状态：已实现并验证
>
> 计划条目：[Events 特性开发计划 EV-13](../designs/events-development-plan.md#ev-13-mcp-事件与-opsmcp-能力)
>
> 前置特性：[F_03 Builder/Ops](F_03_ev03-builder-ops.md)、[F_04 EventBus Build 与业务调用集成](F_04_ev04-event-bus-integration.md)
>
> 公共契约：[S_01 Events 公共契约规约](../specs/S_01_events-contract.md)
>
> 验证摘要：`mcp.call`、`mcp.connect` 及 `ops.mcp.*` 已实现；MCP 专项 65 passed、Events 回归 207 passed（见第 6 节）。

## 1. 问题

插件除了在 Handler 中推进事件链，还需要以平台受控的方式访问外部 MCP 服务：让服务对自己的 Agent 可用、获取工具清单、调用工具。直接复用 Agent Loop 的工具调用入口会把插件调用混入模型工具语义（工具卡呈现、`tool.call` 拦截），插件也无法在独立于模型工具面的情况下调用一次工具。

因此需要一对独立事件承载插件侧 MCP 访问，并以 `ops.mcp.*` 的形式暴露给 Handler。

## 2. 目标与范围

### 2.1 目标

1. 提供 `mcp.call` 与 `mcp.connect` 两个内建事件，由 CORE 真实执行。
2. 以 `ops.mcp.call` / `ops.mcp.connect` 暴露能力，与 `tool.call` 工具调用互不干扰。
3. 插件服务由包内 `mcp.json` 声明；平台已预登记的服务仍可直接使用。连接在首次 `mcp.connect` 时由 `ResourceMgr` 建立，后续调用复用。
4. `mcp.connect` 把服务发布到调用方 Agent，使其工具进入模型工具列表，并返回工具描述。
5. `mcp.call` 调用已登记服务的工具，始终不改变模型工具列表。
6. 失败以 `status` + `error` 返回：服务不存在、工具不存在、参数不合法、超时、调用失败。
7. 单次调用带超时预算：`mcp.connect` 默认 60 秒，`mcp.call` 默认 120 秒；`timeout_ms` 覆盖默认值。`mcp.connect` 将预算透传到连接与列工具；`mcp.call` 由 CORE 预算与 transport 超时共同约束。同名能力调用在 `ops.*` 入口不可自递归。

### 2.2 非目标

- 不接入 `tool.call`，不修改 `AbilityManager` 的工具调用流程。
- 不提供 `mcp.disconnect`、资源读取、提示词等其余 MCP 方法。
- 不新增 `ResourceMgr`：连接、工具卡与锁只有 `Runner.resource_mgr` 一份。
- 不定义服务连接参数：参数来自包内 `mcp.json`，事件只解析声明并建连。

## 3. 设计

### 3.1 事件与 Handler

| 事件 | 层级 | 触发方 | CORE 实现 |
| --- | --- | --- | --- |
| `mcp.call` | CORE | `ops.mcp.call`、业务直接 `trigger` | `McpService.call` → `ResourceMgr.get_mcp_tool` → `MCPTool.invoke` |
| `mcp.connect` | CORE | `ops.mcp.connect`、平台代码直接 `trigger` | `McpService.connect` → 解析包声明 → `ResourceMgr.add_mcp_server` → `get_mcp_tool_ids` / `get_tool_infos` → `AbilityManager.add(config)` |

两个事件不使用 Handler 级匹配条件，注册时声明输入/输出 Validator，非法输入在 `trigger` 入口即被拒绝。

### 3.2 数据模型

- `McpCallInput(server_id, tool, arguments, timeout_ms)` / `McpCallOutput(status, result, is_error, error)`
- `McpConnectInput(server_id, package_id, server_name, timeout_ms)` / `McpConnectOutput(status, server_id, server_name, connected, agent_visible, tools, tool_count, error)`
- `McpConnectInput` 先按 `server_id` 定位，其次按 `package_id + server_name` 查包声明；`package_id` 由 `McpOpsFacade` 从 Handler 的 `package_metadata` 自动填充
- `McpToolDescriptor(name, description, input_schema)`：由 `ResourceMgr.get_tool_infos` 返回的 `ToolInfo.parameters` 转换
- `McpStatus`：`ok` / `invalid_input` / `server_not_found` / `tool_not_found` / `timeout` / `denied` / `error`
- `OpsError(kind, message)`：业务失败原因，与 `status` 分离

`arguments` 先按工具卡 `input_params` 补默认值并校验，再调用工具；空值不下发，校验失败返回 `invalid_input`。

`timeout_ms` 单位为毫秒，`None` 表示使用事件默认预算，上限 10 分钟，与其余 `ops.*` 共享 `MAX_OPS_TIMEOUT`。

`mcp.connect` 的顺序是“解析服务 → 必要时建连 → 读取工具 → 转换工具描述 → 发布到 Agent”。`connected=true` 表示服务已登记在 `ResourceMgr`；`agent_visible` 表示调用结束后该服务是否真的挂在目标 Agent 上。发布失败只影响模型工具面，不改变事件结果。

Agent 由 `get_agent_runtime().get(session_id)` 解析，`AbilityManager` 取自该 Agent；未绑定 Agent 时只返回工具描述，`agent_visible=false`。

### 3.3 能力装配

`handler_ops/mcp_ops.py` 在 `ops.build` 的 BUILTIN 阶段向 Builder 添加 `mcp` 命名空间及两个方法。能力函数收到当前 Handler 的 `EventHandlerOps`，由 `McpOpsFacade.from_ops` 通过 `runtime.build_context` 生成调用上下文后触发对应事件。

`McpOpsFacade` 负责 `OpsCallGuard` 重入与深度保护、结果类型校验；同名重入返回 `status=denied`（`error.kind=reentrant_call`）。字段校验只在事件入口执行一次。

`mcp.call` 的预算由 CORE Handler 包裹并透传给 `MCPTool.invoke`，传输层按同一上限取消；`mcp.connect` 将预算透传给 `ResourceMgr.add_mcp_server()` 的连接与列工具。超时返回 `status=timeout`（`error.kind=timeout`）。

```python
async def call_audit_tool(ops: EventHandlerOps, path: str) -> object | None:
    result = await ops.mcp.call(
        McpCallInput(server_id="srv-1", tool="record_change", arguments={"path": path})
    )
    if result.status is not McpStatus.OK:
        message = result.error.message if result.error else "mcp.call failed"
        raise RuntimeError(message)
    return result.result
```

### 3.4 调用约束

| 约束 | 行为 |
| --- | --- |
| 资源解析 | `runtime.py` 默认惰性解析 `Runner.resource_mgr`，测试或宿主可用 provider 覆盖 |
| Agent 解析 | `runtime.resolve_agent(session_id)` → `agent.ability_manager`；`TeamAgent` 尚未绑定，team 模式下 `agent_visible=false` |
| 服务来源 | `ResourceMgr` 已登记的服务，或包声明；两者都没有即 `server_not_found` |
| 模型工具面 | `mcp.connect` 发布后工具才进入 `list_tool_info()`；`mcp.call` 永不改变工具面 |
| 连接复用 | 两条链共用 `ResourceMgr` / `ToolMgr` / `MCPTool` / `McpClient`；不新建 `ResourceMgr` |
| 重入 | `OpsCallGuard` 在 `ops.*` 入口按 Task 记录活跃能力，同名能力返回 `denied`，嵌套深度上限 8；直接 `trigger` 不受该 guard 约束 |

### 3.5 包声明

包根目录的 `mcp.json` 与 `manifest.yaml` 同级声明服务：

```json
{"mcpServers": {"weather": {"client_type": "stdio", "params": {"command": "npx", "args": ["-y", "weather-mcp"]}}}}
```

- 顶层只允许 `mcpServers`；键为包内服务名，匹配 `^[a-z][a-z0-9_-]{0,63}$`；未知字段使安装失败。
- `server_name = <package>_<hash6>__<name>`；`server_id = mcp_<package>_<hash6>_<name>`；`hash6` 为 package_id 的 SHA-256 前 6 位；`digest` 只用于 reload 比对，不进入 id。
- `client_type` 由 MCP client registry 在连接时解析；`stdio` 必须提供非空的 `params.command`，其余类型必须提供 `server_path`。
- 安装和启动加载只登记声明；首次 `mcp.connect` 调用 `ResourceMgr.add_mcp_server()` 建连，再次调用命中 `ToolMgr` 缓存。
- reload 时声明变化的服务先移除旧连接，再发布新 Handler；uninstall 按声明逐个移除连接。
- 安装与 reload 只登记声明；`mcp.connect` 需要已安装的 `ResourceMgr`，缺失时抛出 `RuntimeError`。

## 4. 实现清单

| 文件 | 内容 |
| --- | --- |
| `extension/schema/mcp.py` | `mcp.json` 解析、服务命名、进程级声明表 |
| `extension/orchestrator.py` | 构建计划时解析包声明 |
| `extension/management/handlers.py` | 安装登记声明、reload 替换连接、uninstall 移除连接 |
| `builtins/event_types/mcp_events.py` | 事件类型注册、输入输出 Validator、超时预算与 CORE Handler |
| `builtins/handler_ops/mcp_ops.py` | `ops.build` BUILTIN Handler，注册 `ops.mcp.*` |
| `ops_impl/mcp/models.py` | 输入输出模型与 `McpStatus` |
| `ops_impl/mcp/facade.py` | 插件入口：重入保护、事件触发、结果类型校验 |
| `ops_impl/mcp/service.py` | 两个事件的执行：工具描述、Agent 发布、工具卡校验与工具调用 |
| `ops_impl/mcp/runtime.py` | `ResourceMgr` 来源解析与 provider 覆盖 |
| `ops_impl/{common,runtime,guard}.py` | 共享错误与超时上限、Agent / 上下文解析、重入保护 |
| `core/foundation/tool/mcp/base.py` | `MCPTool.invoke` 透传 `timeout` 到 `McpClient.call_tool` |
| `core/runner/resources_manager/resource_manager.py` | `add_mcp_server()` 透传连接预算 |
| `core/runner/resources_manager/tool_manager.py` | 连接与列工具预算透传 |

`ops_impl` 不在 `builtins` 下，`bootstrap` 不会自动注册；只有 `event_types` 与 `handler_ops` 中的模块会被发现。

`ops_impl` 是通用能力层：新增命名空间只需增加 `*_events.py`、`*_ops.py` 与对应包目录，事件总线与已有命名空间不需要修改。`fs.*` 复用 `ops_impl/{common,runtime,guard}.py` 与 `ops.build` 装配方式，后端由 `runtime.resolve_sys_operation(agent)` 解析。

## 5. 关键决策

1. `mcp.*` 走 CORE 真实执行，与 Mods 的 `core` 层一致；不注册默认 BUILTIN 策略 Handler。
2. 工具名使用服务端返回的底层名；工具定位通过 `ResourceMgr.get_mcp_tool` 完成，不接受模型侧的 `mcp_<server>_` 前缀。
3. 不再维护会话级能力绑定与工具白名单：Agent 与 `AbilityManager` 统一由 `AgentRuntime` 解析，避免两套状态不一致。
4. 不新增 `CallContext`；调用上下文由 `runtime.build_context` 从 `SessionContext` 派生。
5. 能力层只依赖 `ResourceMgr` 的公开 MCP 方法，不直接读取私有字段。
6. `mcp.runtime` 在调用期惰性解析 `Runner.resource_mgr`，不修改 Runner 的初始化流程；测试或宿主可用 provider 覆盖。
7. `mcp.*` 与工具调用共用 MCP 资源面，但控制面分离：`mcp.call` 不经过 `AbilityManager`，不产生 `tool.call`。
8. `mcp.connect` 负责建连与发布，`mcp.call` 是已连接服务上的临时通道；包声明是服务配置的唯一来源。
9. `AbilityManager` 解析不经过 `ResourceMgr.get_agent()`：单 Agent 未注册到资源管理器，只有 team 成员注册。

## 6. 验证

```text
python -m pytest tests/unit_tests/core/events/test_mcp_ops_events.py -q -o addopts=""
python -m pytest tests/unit_tests/core/extension/test_mcp_declarations.py -q -o addopts=""
python -m pytest tests/unit_tests/core/extension/test_mcp_package_lifecycle.py -q -o addopts=""
python -m pytest tests/unit_tests/core/events -q -o addopts=""
```

结果：MCP 专项 65 passed；Events 回归 207 passed。用例覆盖 `ops.build` 命名空间、`ResourceMgr` 来源解析、connect 工具描述与幂等发布、无 Agent 降级、发布异常降级、同名不同 `server_id` 的服务、服务不存在、工具列表失败、connect/call 默认预算与显式覆盖、call 正常、按工具卡整理参数、工具卡 schema 拒绝、工具不存在、超时原因还原、工具错误、同事件重入拒绝、直接 `trigger` 的默认预算与非法输入、`cwd` 回填与显式值保留、输出 JSON 序列化、`mcp.json` 解析、首次建连、声明复用、配置替换、安装/卸载与重载清理。

## 7. 待确认

- 声明变更后到下一次 `mcp.connect` 之间，新工具尚不可用；这是事件期建连的预期行为。
- reload 删除声明时，已挂在 Agent 上的旧 `AbilityManager` 条目保留到该 Agent 销毁；同名服务再次 `mcp.connect` 时按新声明替换。
- `TeamAgent` 尚未 bind `AgentRuntime`，team 模式下 `mcp.connect` 只返回工具描述。
- 工具白名单能力已移除；若需要按插件限制可见工具，应在发布路径上重新引入，并与 `AbilityManager` 同源。
- 服务登记阶段的连接由平台 `add_mcp_server()` / `ToolMgr` 负责；若取消恰好发生在连接建立过程中，`ToolMgr` 无法确认连接成功，可能留下未关闭的半连接进程。
- reload 删除声明或 uninstall 移除连接后，已绑定 Agent 的工具卡保留到 Agent 销毁，模型再次调用会因服务已移除而失败。
- 结果返回 MCP 工具解包后的内容；多模态附件的统一表达留待与 `fs.*` 一并确定。
