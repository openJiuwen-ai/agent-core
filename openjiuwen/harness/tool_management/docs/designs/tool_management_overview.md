# 工具管理总览（design）

**状态：** 与 spec `S_01`–`S_06` 配套的叙事文档。规范契约在 `docs/specs/`；本文件用于快速定向。

**最近更新：** 2026-10-26

## 问题

Coding Agent 可能注册数十至上百个工具（filesystem、MCP、skill、subagent 等）。每轮 model call 全量下发 schema 浪费上下文并增加误调用。MCP/HTTP 等瞬时失败不应立刻结束整轮。MCP **resource**（文件、URI）的可见性需要与 raw server 接线分离。

## 方案形态（agent-core）

三种互补机制：

1. **渐进式披露** — `ProgressiveToolRail`（priority 90）按轮过滤工具列表，并注册 **`search_tools`** / **`load_tools`** 元工具。
2. **调用韧性** — `ToolCallResilienceRail`（priority 70）对传输/超时类失败做有界重试；**永不**重试显式标记非幂等的工具。
3. **MCP resource** — `McpRail`（priority 95）提供 list/read resource 工具；**`DeepAgent._register_pending_mcps`** 从配置注册 server。

```text
                    ┌─────────────────────────────────────┐
                    │         DeepAgentConfig              │
                    │  mcps | progressive_tool_* |        │
                    │  enable_tool_resilience_rail         │
                    └──────────────┬──────────────────────┘
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         ▼                         ▼                         ▼
  _register_pending_mcps    factory default rails    _queue_pending_rails
  (resource_mgr + AM)       ToolCallResilience 70     ProgressiveTool 90
         │                         │                  (if enabled)
         │                         │                         │
         └─────────────┬───────────┴─────────────┬───────────┘
                       ▼                         ▼
                 AbilityManager            before_model_call
                 (all abilities)           (filtered tool list)
                       │
                       ▼
                 Model tool_calls ──► @rail retry loop (resilience)
```

## 双仓说明

基于 **jiuwenswarm** 的产品构建可能替换渐进式元工具（`tools_search`、`invoke_tool`）并增加 **`DisabledToolsRail`**。该栈维护在 swarm `agents/harness/common/tool_management/`。agent-core 文档与默认值只描述 **`search_tools` / `load_tools`** 路径；集成指南须标明部署使用的是哪条 fork。

## 接线入口

| 入口 | Progressive | Resilience | MCP server | MCP resource rail |
|------|-------------|------------|------------|-------------------|
| `create_deep_agent(...)` + config 开关 | `progressive_tool_enabled` | `enable_tool_resilience_rail`（默认开） | `mcps=` | 手动 / Spec `core.mcp` |
| Spec harness 元素 | `core.progressive_tool` | factory 路径 | context 上 config | `core.mcp` |
| 测试 | 直接 `ProgressiveToolRail` / config | rail 单测 | mock `resource_mgr` | `McpRail` init 测试 |

## 验证

在 agent-core 根目录：

```bash
make test TESTFLAGS="tests/unit_tests/harness/test_progressive_tool_rail.py"
make test TESTFLAGS="tests/unit_tests/harness/rails/test_mcp_rail.py"
make test TESTFLAGS="tests/unit_tests/core/single_agent/test_tool_call_resilience_rail.py"
```

## 延伸阅读

- 模块索引：[`../../AGENTS.md`](../../AGENTS.md)
- Spec：[`../specs/S_01_overview-and-boundaries.md`](../specs/S_01_overview-and-boundaries.md)
- L0 仓库：[`../../../../../AGENTS.md`](../../../../../AGENTS.md)
