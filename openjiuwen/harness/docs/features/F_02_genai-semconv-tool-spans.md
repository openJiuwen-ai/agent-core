# GenAI 工具 Span 标准化

## 元信息

| 项 | 值 |
|---|---|
| 类型 | feature |
| 日期 | 2026-09-03 |
| 范围 | `openjiuwen/harness/observability/`、`openjiuwen/harness/rails/evolution/` |
| 测试基线 | harness observability 与 evolution rail 回归通过 |
| 关联 spec | `S_04_rails-contract.md` |

## 背景

harness 工具 span 曾同时写 `gen_ai.tool.input/output/id` 与 `gen_ai.tool.call.*`，Evolution rail
也需要知道该清洗哪一套字段。双写扩大 attribute 体积，并让轨迹消费者无法判定权威结果。

## 决策

- 工具 span 统一命名为 `execute_tool {name}`。
- 只写 `gen_ai.tool.name`、`gen_ai.tool.call.id`、`gen_ai.tool.call.arguments`、
  `gen_ai.tool.call.result`；工具资源 ID 使用 `openjiuwen.tool.resource_id`。
- `gen_ai.tool.type` 只使用规范枚举；AbilityManager 服务端执行记为 `extension`，MCP 作为独立传输
  信息记录到 `openjiuwen.tool.protocol=mcp`，不把 `mcp` 当作标准 type。
- Evolution rail 只清洗标准 arguments/result，不再维护第二套字段。
- agent/LLM/tool 识别使用 operation 属性，不绑定固定历史 span name。

## 拒绝的方案

1. **保留旧字段供 UI 使用**：UI 应消费标准事实，兼容投影不能反向约束 producer。
2. **在 rail 内定义字段字符串**：会与共享 semconv 漂移；统一 import 权威常量。

## 验证

- harness observability tool 生命周期、嵌套、同名调用、错误与 redaction 测试通过。
- Evolution rail 轨迹重建和 skill evolution 测试通过。

## 已知遗留

历史已归档轨迹仍由 `agent_evolving/trajectory/legacy_semconv.py` 只读兼容，不在 harness producer
中双写。
