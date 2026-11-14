# McpRail 与 MCP server 注册

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | openjiuwen/harness/rails/mcp_rail.py、deep_agent._register_pending_mcps |
| 最近一次修订日期 | 2026-10-26 |
| 关联 feature | — |

## 关注点分离

agent-core 中 MCP 集成分两层：

| 层 | 职责 | 位置 |
|----|------|------|
| **Server 注册** | 向 `Runner.resource_mgr` 添加 MCP server、用 agent id 标记工具、在 `ability_manager` 注册 MCP config | `DeepAgent._register_pending_mcps`，由 `DeepAgentConfig.mcps` 驱动 |
| **Resource 发现工具** | 将 MCP **resource** 的 list/read 暴露为普通 agent tools | `McpRail` |

`McpRail` docstring 明确：**不在** rail 内处理 MCP server 注册。

## 接口契约

### McpRail

| 属性 | 值 |
|------|-----|
| `priority` | **95** |
| `init` 注册工具 | `ListMcpResourcesTool`、`ReadMcpResourceTool`（`openjiuwen.harness.tools.mcp_tools`） |
| `uninit` | 按 name 从 `ability_manager` 移除上述 ability |

`language` 与 `agent_id` 来自 host agent（`system_prompt_builder.language`、`agent.card.id`）。

### DeepAgent MCP 注册

`_register_pending_mcps`（async，rail init 之前）：

- config 存在且非空时遍历 `cfg.mcps`。
- 经 `Runner.resource_mgr.add_mcp_server` 添加，或 reconcile 已有 `server_id`（config 不匹配则抛 resource error）。
- 用 agent card id 标记 server 与 MCP tool id。
- 对 MCP-backed ability 调用 `ability_manager.add(mcp_config)`。

Sub-agent 可在 `SubAgentConfig` 上携带独立 `mcps` 列表；父子 MCP 接线遵循 DeepAgent/subagent 构建路径。

### 挂载 McpRail

- **不在** `factory.py` 的 `default_rails` 列表中（与 `ToolCallResilienceRail` 不同）。
- 可作为 harness manifest 元素 **`core.mcp`**（`harness_elements.py`，builder `McpRail`）。
- 亦可通过显式 `config.rails`、`add_rail` 或 Spec 冷路径元素解析添加。

## 不变量

1. Resource list/read 工具要求 `resource_mgr` 上 **已** 注册 MCP server。
2. **勿** 在 `McpRail.init` 实现 server 传输生命周期。
3. Priority 95 与其它自注册工具的 rail 对齐（如 skill rail 注释中引用的 subagent 模式）。

## 修改检查单

- [ ] 新 MCP 接线是否保持 `_register_pending_mcps` 先于 rail init？
- [ ] 未把 server 启动/关停逻辑移入 `McpRail`？
- [ ] 与 jiuwenswarm MCP 注册文档区分（swarm common / S_09）？
- [ ] 改 priority 或工具名是否更新 `test_mcp_rail.py`？

## 用户文档

MCP 搭建教程在 Development Guide **MCP Tool**（EN/ZH）。本 spec 只定义 harness 分层。

## 测试

- `tests/unit_tests/harness/rails/test_mcp_rail.py`

## 与其它 spec 的关系

- [`S_01_overview-and-boundaries.md`](S_01_overview-and-boundaries.md)
- [`S_06_factory-and-manifest-wiring.md`](S_06_factory-and-manifest-wiring.md)
