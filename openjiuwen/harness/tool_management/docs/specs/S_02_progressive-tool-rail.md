# ProgressiveToolRail

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | openjiuwen/harness/rails/progressive_tool_rail.py |
| 最近一次修订日期 | 2026-10-26 |
| 关联 feature | — |

## 角色

`ProgressiveToolRail` 实现大工具库的 **渐进式披露**：模型 initially 看到较小工具集 + 用于 search/load 的 meta-tools；每轮 model call 的可调用工具在 `before_model_call` 中过滤。

## 接口契约

### 类契约

| 属性 | 值 |
|------|-----|
| 基类 | `DeepAgentRail` |
| `priority` | **90**（类属性固定） |
| 构造 | 需要 `DeepAgentConfig`（读 progressive 字段 + `language`） |
| `init` 注册的 meta-tools | `SearchToolsTool`、`LoadToolsTool`（见 [[S_03]]） |

### 配置（`DeepAgentConfig`）

| 字段 | 默认 | 含义 |
|------|------|------|
| `progressive_tool_enabled` | `False` | `True` 时 `DeepAgent._queue_pending_rails` 追加本 rail |
| `progressive_tool_default_visible_tools` | `[]` | invoke 开始时 session 基线可见集 |
| `progressive_tool_always_visible_tools` | `[]` | 每轮始终展示（除 meta-tools 外） |
| `progressive_tool_max_loaded_tools` | `12` | 并发 loaded 工具上限（load 路径 enforcement） |

Manifest / Spec 别名：`ProgressiveToolInput`（`harness_elements.py`）将 `planning_tool_names` → `default_visible`，`executor_tool_names` → `always_visible`（见 [[S_06]]）。

## 执行路径或数据结构

### Session 状态键

实现细节、调试时稳定：

- `__progressive_visible_tool_names__` — session 内已 load 的工具名
- `__progressive_tool_discovery_trace__` — search/load 追踪（可观测性）

### 生命周期钩子

| 钩子 | 行为 |
|------|------|
| `init` | 在 `ability_manager` 注册 meta-tools；跟踪拥有的 `ToolCard` |
| `uninit` | 仅当本 rail 仍拥有 name/card 时移除 meta-tools |
| `before_invoke` | 缓存完整工具清单；用 default 初始化可见集 |
| `before_model_call` | 注入 prompt sections（导航 + 规则）；**过滤** `inputs.tools` 为 meta + always-visible + session-visible |
| `after_invoke` | 占位 no-op |

### 可见性规则

对每个进入 model call 的候选工具：

1. 始终允许 **meta-tools**（`search_tools`、`load_tools`）。
2. 始终允许 **`progressive_tool_always_visible_tools`**。
3. 允许 **session 可见集** 内工具（由 `load_tools` 更新）。
4. 其余已注册 ability 在该轮 **不** 进入模型 facing 列表。

被过滤的 ability 仍在 `AbilityManager`，直到 load 后才对模型可见。

### Prompt 集成

使用 `openjiuwen/harness/prompts/sections/progressive_tool_rail.py` 的 `SystemPromptBuilder` sections（多语言导航 + 规则）。Rail 须在暴露 `system_prompt_builder`（或经 `_get_prompt_builder`）的 agent 上运行。

### jiuwenswarm fork（非 core）

Swarm 管辖 fork 替换元工具流（`tools_search`、`invoke_tool`），可能加 `DisabledToolsRail`。**勿** 把这些名称写成 core 默认。跨仓复制 prompt 须改名为 core 等价物。

## 不变量

1. `priority` 固定 **90**；变更须更新测试与 [[S_06]] 装配说明。
2. Meta-tools 在 progressive rail 激活期间始终在 filter allowlist 内。
3. 过滤只影响模型可见列表，不 detach `AbilityManager` 全局注册（与 swarm `DisabledToolsRail` 不同）。

## 修改检查单

- [ ] 改 visibility 规则是否同步 `test_progressive_tool_rail.py`？
- [ ] 改 session 键名是否评估观测/日志兼容性？
- [ ] prompt section 变更是否覆盖多语言路径？
- [ ] 文档与 prompt 中工具名仍为 `search_tools` / `load_tools`？

## 测试

- `tests/unit_tests/harness/test_progressive_tool_rail.py`

## 与其它 spec 的关系

- [`S_03_meta-tools-search-and-load.md`](S_03_meta-tools-search-and-load.md)
- [`S_06_factory-and-manifest-wiring.md`](S_06_factory-and-manifest-wiring.md)
