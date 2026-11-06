---
description: 工具管理模块——渐进式工具披露、MCP 资源工具、工具调用韧性、factory/manifest 装配（agent-core harness）。
---

# Tool Management（Harness）

agent-core 侧 **Coding Agent 工具面管理**：渐进式披露 rail、search/load 元工具、有界工具调用重试、MCP **resource** 工具（非 server 注册），以及 `DeepAgent` / factory / manifest 如何挂接它们。

本 hub 面向 **AI 助手与模块维护者的设计归档**（`AGENTS.md` + `docs/specs/`）。**不替代**仓库 `docs/en/`、`docs/zh/` 下的用户教程（MCP Tool、harness tools/rails API 页、自定义工具 foundation 文档）——用户向内容只引用链接；契约与管辖权写在本 archive。

**L0 全局约定**（命令、布局、安全）：agent-core 根 [`AGENTS.md`](../../../AGENTS.md)。`make test`、lint、模块边界先读 L0。

## 模块地图（代码）

```
openjiuwen/harness/
├── rails/
│   ├── progressive_tool_rail.py   # ProgressiveToolRail, priority 90
│   ├── tool_call_resilience_rail.py
│   └── mcp_rail.py
├── tools/
│   └── tool_discovery/            # SearchToolsTool, LoadToolsTool
├── schema/config.py               # DeepAgentConfig progressive_tool_* , mcps, enable_tool_resilience_rail
├── factory.py                     # default ToolCallResilienceRail when enable_tool_resilience_rail
├── deep_agent.py                  # _queue_pending_rails, _register_pending_mcps
└── manifest/harness_elements.py   # core.progressive_tool, core.mcp manifest elements

openjiuwen/harness/tool_management/   # ← 本 hub（仅文档）
├── AGENTS.md
└── docs/
    ├── AGENTS.md
    ├── specs/S_01 … S_06
    ├── designs/tool_management_overview.md
    ├── features/                  # F_NN 归档（首个 feature 前为空）
    └── bugfix/                    # 可选 fix 笔记
```

## Spec 索引

| Spec | 主题 |
|------|------|
| [`S_01`](docs/specs/S_01_overview-and-boundaries.md) | 范围、双仓管辖、文档边界 |
| [`S_02`](docs/specs/S_02_progressive-tool-rail.md) | `ProgressiveToolRail`、可见性过滤、session 状态 |
| [`S_03`](docs/specs/S_03_meta-tools-search-and-load.md) | `search_tools` / `load_tools` 元工具 |
| [`S_04`](docs/specs/S_04_tool-call-resilience.md) | `ToolCallResilienceRail`、重试策略 |
| [`S_05`](docs/specs/S_05_mcp-rail-vs-server-registration.md) | `McpRail` vs `DeepAgentConfig.mcps` |
| [`S_06`](docs/specs/S_06_factory-and-manifest-wiring.md) | Factory 默认项、manifest `core.*` 元素 |

总览图：[`docs/designs/tool_management_overview.md`](docs/designs/tool_management_overview.md)。

## 双仓管辖（必读）

| 关切 | agent-core（本 archive） | jiuwenswarm（姊妹产品仓） |
|------|--------------------------|---------------------------|
| `ProgressiveToolRail`：`search_tools` → `load_tools`，priority **90** | **OWNS** | 引用；产品侧用 **FORK** |
| `tool_discovery`：`SearchToolsTool` / `LoadToolsTool` | **OWNS** | — |
| `ToolCallResilienceRail`，priority **70**，factory 默认 | **OWNS** | 引用 |
| `McpRail`，priority **95**（仅 resource list/read） | **OWNS** | 引用 |
| `DeepAgentConfig` 的 `progressive_tool_*`、`mcps` 与 `DeepAgent` 注册 | **OWNS** | 产品 MCP 生命周期亦在 swarm `common` |
| Fork：`tools_search` → `invoke_tool`、`DisabledToolsRail` | 引用 | **OWNS** — `agents/harness/common/tool_management/` |

**关键：** jiuwenswarm 提供 **fork**，元工具命名与流程不同（`tools_search`、`invoke_tool`，另有 `DisabledToolsRail`）。agent-core harness 与 core agent **不得**默认假设这些名称或 rail 集合存在。跨仓移植 prompt、测试、skill 时须 grep 工具名与 rail priority——不要把 swarm-only 字符串写进 core 默认配置。

## 关键默认项（agent-core）

| 开关 / rail | 默认 / 触发条件 |
|-------------|-----------------|
| `enable_tool_resilience_rail` | `True` → factory 挂载 `ToolCallResilienceRail` |
| `progressive_tool_enabled` | `False` → `DeepAgent._queue_pending_rails` 在 `True` 时追加 `ProgressiveToolRail` |
| 元工具（core） | `search_tools`、`load_tools` |
| `McpRail` | factory **不**自动挂载；manifest 元素 `core.mcp` 或显式 `add_rail` |
| MCP server | `DeepAgentConfig.mcps` → `_register_pending_mcps`（rail init 之前） |

## 测试（回归锚点）

在 agent-core 根目录执行：

```bash
make test TESTFLAGS="tests/unit_tests/harness/test_progressive_tool_rail.py"
make test TESTFLAGS="tests/unit_tests/harness/rails/test_mcp_rail.py"
make test TESTFLAGS="tests/unit_tests/core/single_agent/test_tool_call_resilience_rail.py"
```

行为以 **代码 + 上述单测** 为真相源；spec 描述意图。冲突时在同一变更集内修文档（见下文双向同步）。

## 用户文档（只引用，勿复制）

- EN：`docs/en/2.Development Guide/Advanced Usage/MCP Tool.md`
- ZH：`docs/zh/.../MCP工具.md`
- Harness tools API：`docs/en/2.Development Guide/API Docs/openjiuwen.harness/tools/tools.md`
- Rails / schema（可能提及 progressive 开关）：`openjiuwen.harness/rails/rails.md`、`config.md`
- Development Guide 下自定义工具 / foundation tool API 文档

## 设计文档归档与双向同步（本模块强制）

把 **`openjiuwen/harness/` 上文列出的 tool-management 代码路径 + 本 `tool_management/docs/` 树 + 本 `AGENTS.md`** 当作**一个一致性单元**。

1. **先设计后改码：** 改渐进披露、韧性、MCP resource 工具前，先读相关 `docs/specs/S_*` 与 `docs/features/F_*`。契约变更时**同步**改 spec——不要把文档当事后补档。
2. **特性归档：** 非平凡特性新增 `docs/features/F_NN_<slug>.md`（见 [`docs/AGENTS.md`](docs/AGENTS.md)）。
3. **双向同步：** hub 内任一处与代码不符（工具名、priority、配置字段名、默认值）→ **同一 PR 修文档**；勿向用户或其它 agent 传播过期文本。
4. **勿占用** `openjiuwen/harness/docs/`（harness 级 feature 归档，如 ask-user OTEL）存放 tool-management spec——规约只放在 `tool_management/docs/`。

## 架构铁律

1. **Core 元工具名固定为 `search_tools` 与 `load_tools`。** 未经明确产品决策与 spec 更新，不要在 agent-core harness 引入 `tools_search` / `invoke_tool`；它们属于 jiuwenswarm fork。
2. **`ProgressiveToolRail.priority` = 90；`McpRail.priority` = 95；`ToolCallResilienceRail.priority` = 70。** 改 priority 顺序影响工具注册与 callback 排序——须与 spec、单测一起更新。
3. **非幂等工具永不重试**（`ToolCard.idempotent is False`）。勿为「方便」在写操作、shell、spawn 类工具上绕过 `ToolCallResilienceRail` 私自重试。
4. **`McpRail` 只暴露 MCP resource 的 list/read 工具。** MCP **server** 注册与 MCP **tool** 挂载留在 `Runner.resource_mgr` + `DeepAgent._register_pending_mcps` / ability manager——不要把 server 生命周期塞进 `McpRail`。
5. **`_register_pending_mcps` 在 rails 初始化之前执行。** 新增 MCP 接线须保持顺序，使 resource 工具能见到已注册 server。
6. **渐进式披露在 `before_model_call` 按轮过滤模型可见工具列表**；除 `progressive_tool_always_visible_tools` 与 meta-tools 外，勿把「隐藏」工具注册为可直接 callable——须经 `load_tools`。
7. **Factory 拥有的 resilience 为 opt-out**（`enable_tool_resilience_rail=False`），非 opt-in。自定义重试 rail 不得与 `@rail` 重试环对同一 invoke 双重重试而不文档化交互。
8. **Manifest 元素 `core.progressive_tool`、`core.mcp` 是 harness 目录项**——`harness_elements.py` 内 builder 须与 `DeepAgentConfig` 字段语义对齐（含 `ProgressiveToolInput` 别名映射）。
9. **Agent 自动化勿改运行时配置文件**（`office-claw-config.json`、`.env`、MCP JSON）——人类持有（继承 L0 安全姿态）。
10. **Spec 与本 `AGENTS.md` 是面向 agent 的模块契约**；公开行为变更时同步用户向 `docs/en|zh` 教程，但架构管辖表只维护在本 archive。

## 提交约定

本区域 tool-management 文档与 harness 改动 commit scope 固定 **`tool-mgmt`**（如 `docs(tool-mgmt): …`、`feat(tool-mgmt): …`）。新增 `F_*` / 修订 `S_*` 时的三连续提交流程见 [`docs/AGENTS.md`](docs/AGENTS.md)。
