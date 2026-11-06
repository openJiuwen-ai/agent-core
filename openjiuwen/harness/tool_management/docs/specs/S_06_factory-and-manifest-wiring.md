# Factory 与 manifest 装配

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | openjiuwen/harness/factory.py、deep_agent.py、manifest/harness_elements.py |
| 最近一次修订日期 | 2026-10-26 |
| 关联 feature | — |

## Factory（`create_deep_agent_parts`）

### 默认 rails（tool 相关摘录）

`default_rails` 元组：`(RailClass, should_add, factory_fn)`。

| Rail | 门控 | 默认 |
|------|------|------|
| `ToolCallResilienceRail` | `config.enable_tool_resilience_rail` | **开**（`True`） |

其它默认 rails（security、LLM retry、task planning、skills、subagents 等）见 harness 级材料；它们与工具列表交互但非 tool-management spec 管辖。

`ProgressiveToolRail` 与 `McpRail` **不** 插入此默认列表。

### 工具规范化

Factory 将 `config.tools` 规范为 `DeepAgentParts.tool_cards` / `tool_instances`，在 `apply_deep_agent_parts` 经 `ability_manager` + resource manager 规则应用（见 factory 模块）。

## 执行路径或数据结构

### DeepAgent 配置驱动 rails

`_queue_pending_rails(config)`：

| 条件 | 入队 rail |
|------|-----------|
| `config.rails is not None` | 扩展 pending 为用户/ supplied rails |
| `config.progressive_tool_enabled` | 追加 `ProgressiveToolRail(config=config)` |
| `config.enable_task_loop` | 追加 `TaskCompletionRail`（task loop；非 tool-mgmt） |
| Permission dict enabled | 构建成功时追加 permission interrupt rail |

Pending rails 在 agent 启动时懒初始化（**在 MCP 注册之后**——见下）。

### MCP 注册顺序

DeepAgent 初始化路径上，**`await _register_pending_mcps()`** 在 rails 初始化 **之前** 运行，使 MCP-backed tools 与 server 在 rails/abilities 绑定时已存在。

配置源：`DeepAgentConfig.mcps`（`Optional[List[McpServerConfig]]`，默认 none/empty）。

### Manifest 目录（`harness_elements.py`）

经 `@harness_element` 注册，供 Spec 冷路径构建（`ensure_builtin_elements_registered`）：

| 元素名 | Kind | Builder |
|--------|------|---------|
| `core.progressive_tool` | RAIL | `_build_progressive_tool_rail` |
| `core.mcp` | RAIL | `McpRail` |

#### ProgressiveToolInput → DeepAgentConfig

`_build_progressive_tool_rail`：

- 在合成 `DeepAgentConfig` 上设 `progressive_tool_enabled = True`（model/workspace/language 来自 `BuildContext`）。
- 参数映射：
  - `planning_tool_names` → `progressive_tool_default_visible_tools`
  - `executor_tool_names` → `progressive_tool_always_visible_tools`
  - `default_visible_tools` / `always_visible_tools` / `max_loaded_tools` → 直映射字段

返回 `ProgressiveToolRail(config)`。

Manifest 模块 import 规则：**仅 harness + core**，**never** `agent_teams`。

### 配置参考（tool-management 字段）

来自 `DeepAgentConfig`：

```text
progressive_tool_enabled: bool = False
progressive_tool_always_visible_tools: list[str]
progressive_tool_default_visible_tools: list[str]
progressive_tool_max_loaded_tools: int = 12
enable_tool_resilience_rail: bool = True
mcps: Optional[List[McpServerConfig]]
parallel_tool_calls: bool = True   # inner ReAct batching; not progressive filter
```

## 不变量

1. Factory 经 `_already_provided(ToolCallResilienceRail)` 去重——勿无意 double-mount。
2. Manifest 来源的 progressive rail 总在构造的 config 快照上启用 progressive 模式。
3. Manifest 元素名使用 `core.*` 前缀，避免与 team 目录项冲突。

## 修改检查单

- [ ] 改 `default_rails` 是否更新本 spec 与 resilience 单测？
- [ ] 改 `_queue_pending_rails` 条件是否保持 MCP-before-rails 顺序？
- [ ] `ProgressiveToolInput` 别名与 `DeepAgentConfig` 字段仍一一对应？
- [ ] manifest 模块未引入 `agent_teams` 依赖？

## 测试

- Factory/resilience：personal context / factory 相关测试（适用时）
- Progressive：`tests/unit_tests/harness/test_progressive_tool_rail.py`
- MCP rail：`tests/unit_tests/harness/rails/test_mcp_rail.py`
- Resilience：`tests/unit_tests/core/single_agent/test_tool_call_resilience_rail.py`

## 与其它 spec 的关系

- [`S_02_progressive-tool-rail.md`](S_02_progressive-tool-rail.md)
- [`S_04_tool-call-resilience.md`](S_04_tool-call-resilience.md)
- [`S_05_mcp-rail-vs-server-registration.md`](S_05_mcp-rail-vs-server-registration.md)
