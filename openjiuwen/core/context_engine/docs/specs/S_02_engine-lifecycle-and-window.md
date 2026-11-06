# Engine lifecycle and context window

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/core/context_engine/context_engine.py`、`context/` |
| 最近一次修订日期 | 2026-10-26 |

## 范围 / 边界

- `ContextEngine` 生命周期：`create_context`、context 池、processor 实例化。
- `ModelContext` / `SessionModelContext` 上消息读写与 **get_context_window** 路径（触发 processor 链）。
- `ContextEngineConfig` 中与窗口相关的字段：`max_context_message_num`、`default_window_message_num`、`default_window_round_num`、`context_window_tokens`、`model_name` / `model_context_window_tokens`、OpenRouter 窗口拉取开关。
- 实例级 `register_window_mutator` / `clear_window_mutators`。

不管：单个 processor 内部压缩算法（见 [[S_03]]、[[S_04]]）。

## 不变量

1. **默认 token 计数**：`create_context` 未传 `token_counter` 时使用 `TiktokenCounter`；引擎构造时后台 warm-up tiktoken 词表（不阻塞 event loop 首次 HTTPS）。
2. **窗口 GET 是副作用入口**：processor 链在组装 `ContextWindow` 时运行；依赖「下一次 GET」的 API（如 FullCompact force）以此为准（[[S_04]]）。
3. **硬消息上限**：`max_context_message_num` 非空时，上下文消息数不得超过该值（ enforcement 在 context 层）。
4. **轮次窗口优先于消息数窗口**：当 `default_window_round_num` 与 `default_window_message_num` 同时有意义时，轮次语义优先（见 `ContextEngineConfig` docstring）。
5. **Callback 钩子**：`create_context` 等路径通过 runner callback 框架发射 `ContextEvents`；rail 在 `before_invoke` / `on_model_exception` 修补 tool 上下文，不替代 engine 内 window 构建。

## 接口契约

| 操作 | 契约 |
|------|------|
| `create_context(context_id, session, processors=..., history_messages=..., token_counter=...)` | 创建或返回池化 context；processors 为 `(name, config)` 列表，由 engine 解析为 `ContextProcessor` 实例 |
| `get_context_window`（ModelContext 上） | 顺序执行已注册 processor → 应用 engine/window mutators → 返回 `ContextWindow` |
| `enable_reload`（config） | 为真时引擎监控 `[[HANDLE:...]]` 并重载 offload 内容（[[S_05]]）；为假时 hint 保留在对话中 |

## 数据结构

- **`ContextWindow`**：面向 LLM 的消息切片 + token 统计快照（见 `base.py`）。
- **`ContextStats` / usage 类型**：与窗口伴生的遥测；改字段语义须同步 usage 单测。

## 与其它 spec 的关系

- Processor 顺序与 forked：[[S_03]]
- FullCompact 触发：[[S_04]]
- Recall 归档存储：[[S_05]]
