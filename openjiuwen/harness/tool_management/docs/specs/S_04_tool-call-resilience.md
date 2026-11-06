# ToolCallResilienceRail

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | openjiuwen/harness/rails/tool_call_resilience_rail.py |
| 最近一次修订日期 | 2026-10-26 |
| 关联 feature | — |

## 角色

对 **可重试** 的工具调用失败做有界预算重试，复用 `AbilityManager._railed_execute_single_tool_call` 上 `@rail` 装饰器的重试环。本 rail 只负责 **决策 + 记账**；在 `ON_TOOL_EXCEPTION` 上通过 `ctx.request_retry()` 触发再执行。

## 接口契约

### 类契约

| 属性 | 值 |
|------|-----|
| 基类 | `DeepAgentRail` |
| `priority` | **70** |
| 默认挂载 | `DeepAgentConfig.enable_tool_resilience_rail` 为 `True`（默认 **True**）时 `create_deep_agent_parts` 挂载 |
| `DEFAULT_MAX_ATTEMPTS` | `3`（构造 `max_attempts >= 1`） |

个人上下文 agent 支持路径在组装默认 harness 栈时也会挂载（见 `agent_support.py`）。

### 配置

| 开关 | 位置 | 默认 |
|------|------|------|
| `enable_tool_resilience_rail` | `DeepAgentConfig` | `True` |

未来可按工具读 `ToolCard.properties["resilience"]["max_attempts"]`（`_resolve_max_attempts` 预留；当前返回 rail 默认）。

## 执行路径或数据结构

### 重试决策层（顺序）

1. **非幂等工具 — 永不重试。** 若 invoked name 对应 `ToolCard` 的 `idempotent is False`，记录日志并 return，不重试。安全默认：未注册或非 card ability **不** 经此检查当作非幂等（仅显式 `ToolCard` 且 `idempotent=False`）。
2. **异常类型 / 消息 — 是否可重试？** 不可重试类型含 `ValueError`、`TypeError`、`KeyError`、`AttributeError`、`PermissionError`、`FileNotFoundError`、`IsADirectoryError`。可重试类型含常见 timeout/connection；类名或消息子串另匹配传输/MCP/session closed 等（见 rail 源码）。
3. **预算 —** 若 `ctx.retry_attempt + 1 >= max_attempts`，停止；可选把重试摘要附到 tool result/message 供 LLM。

可重试且预算剩余：递增 invoke 计数、打日志、`ctx.request_retry()`。

### 钩子

| 钩子 | 行为 |
|------|------|
| `before_tool_call` | 重置单次 invoke 重试计数 |
| `on_tool_exception` | 缺省时填充 timeout 风格 result；应用上述决策层 |

### 与 timeout 的交互

调用级 timeout 与幂等性 **解耦**：timeout 解析在 `AbilityManager`；重试抑制的幂等性由本 rail 经 `ToolCard.idempotent` 负责。

## 不变量

1. 有副作用工具（`idempotent=False`）**不得**静默重试。
2. 参数、权限、逻辑类错误 **不得** 重试。
3. 传输/超时类失败可在单次 invoke 内重试至多 `max_attempts` 次。
4. 支持关闭韧性（`enable_tool_resilience_rail=False`），供自备重试策略或希望 raw exception 的部署。

## 修改检查单

- [ ] 扩展可重试异常列表是否避免把逻辑错误标为可重试？
- [ ] 改 `max_attempts` 默认是否跑 `test_tool_call_resilience_rail.py`？
- [ ] 新增自定义 retry rail 是否文档化与 `@rail` 环的交互、避免双重重试？
- [ ] 非幂等判定仍只依赖显式 `ToolCard.idempotent=False`？

## 测试

- `tests/unit_tests/core/single_agent/test_tool_call_resilience_rail.py`

## 与其它 spec 的关系

- [`S_06_factory-and-manifest-wiring.md`](S_06_factory-and-manifest-wiring.md)
