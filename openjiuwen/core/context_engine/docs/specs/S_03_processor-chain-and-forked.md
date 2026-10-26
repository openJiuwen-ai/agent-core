# Processor chain and forked preset

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `processor/`、`processor/forked/`、`harness/rails/context_engineer/context_processor_rail.py` |
| 最近一次修订日期 | 2026-10-26 |

## 范围 / 边界

- `ContextProcessor` 协议：对 `ModelContext` 的就地变换。
- Engine 侧 processor 注册表（类名 key → 实现）。
- **Forked** 实现包：preset 默认链使用的 offloader/compressor 变体。
- `ContextProcessorRail` 的 preset 合并语义与用户 override。

## 不变量

1. **`forked.activate()`** 必须在解析 preset 链（或显式注册 `SessionMemoryCompressor`）之前调用，使 registry 指向 forked 模块。
2. **Preset 顺序（固定语义）**  
   `MessageSummaryOffloader` → `SessionMemoryCompressor(enabled=False)` → `ReasoningToolLoopCompactProcessor` → `DialogueCompressor` → `CurrentRoundCompressor` → `RoundLevelCompressor`。  
   **不含** `FullCompactProcessor`（[[S_04]]）。
3. **合并规则**：user `(key, config)` 按 key 覆盖 preset 同 key；dict override 与 BaseModel 字段 merge；对 preset 不存在的 key 仅接受完整 BaseModel，不接受裸 dict。
4. **Model 注入**：config 上 `model` / `model_client` 为 None 时，rail `init` 从 agent config 注入 `ModelRequestConfig` / client config。
5. **Session memory**：`SessionMemoryCompressor` enabled 时启动 `SessionMemoryManager`；constructor 参数 `session_memory=` 已废弃，忽略。
6. **Loop bailout**：`ReasoningToolLoopCompactProcessor` 在 session state 计数超 `bailout_threshold` / `tool_args_bailout_threshold` 时，rail 在 `before_invoke` 检测并 **必须** `raise AbortError(build_error(..., StatusCode.CONTEXT_EXECUTION_ERROR))`，不可吞掉或换错误码。
7. **Bailout state keys**：`LOOP_COMPACT_BAILOUT_STATE_KEY`、`TOOL_ARGS_LOOP_COMPACT_BAILOUT_STATE_KEY` 为公开常量（`__init__.py` export）。

## 接口契约

| 组件 | 契约 |
|------|------|
| `ContextProcessorRail(processors=..., preset=True)` | `init` 写 `agent.react_agent._config.context_processors` |
| `preset=False` | 仅 user processors；若含 `SessionMemoryCompressor` 仍须 `forked.activate()` |
| `before_invoke` / `on_model_exception` | 修复不完整 tool 上下文；检查 loop bailout |

## 数据结构

- Processor 有效配置在 rail `init` 打 info 日志（类 module + qualname），便于生产排障。
- `_all_processors` 缓存合并结果，供 recall/reload section 与 protected tools 配置。

## 与其它 spec 的关系

- 窗口触发：[[S_02]]
- FullCompact：[[S_04]]
- Recall 保护工具名：[[S_05]]
- Assemble rail 同 priority：[[S_06]]
