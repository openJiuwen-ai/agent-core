# GenAI Semantic Conventions 全链路统一

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-03 |
| 范围 | `extensions/observability`、`extensions/tracer_otel`、`harness/observability`、`agent_teams/observability`、`agent_evolving/trajectory`、`agent_evolving/agent_rl`、jiuwenswarm trajectory UI |
| 测试基线 | 受影响模块组合回归 1,053 项；标准迁移导致的失败均修复，另有 1 项与本改动无关的 PATH 引号断言 |
| Refs | OpenTelemetry `semantic-conventions-genai` revision `fee465db333bdd6a7d2faa320edab5cf3101a4f4` |

## 背景

原实现同时写入不同时期和不同 SDK 约定的 `gen_ai.*`：例如 `tool.output` 与
`tool.call.result`、indexed prompt/completion 与 structured messages、prompt/completion token
与 input/output token。轨迹 UI 展示的 `<truncated ...>` 因而无法单凭字段名判断是事实数据、
兼容投影还是 exporter 派生值，且 Langfuse 与普通 OTLP 获得不同 usage 语义。

## 数据结构

当前写入模型只有三组标准事实：

1. 模型请求：`system_instructions`、`input.messages`、`output.messages`；tool call/response 是消息 part。
2. 工具执行：`tool.name`、`tool.call.id/arguments/result`。
3. usage/response：input、output、cache read/write、reasoning output token，TTFC 秒值和 finish reasons 数组。

业务关联、消息增量计数和 reasoning duration 放入 `openjiuwen.*`。历史轨迹旧键集中在
`trajectory/legacy_semconv.py`，只读、永不由当前 producer 写出。

## 决策

- Python 标准键集中在可整体替换的 `extensions/observability/gen_ai_semconv.py`，项目 facade
  `semconv.py` 只重新导出标准键并定义 OpenJiuwen 扩展。
- producer 不做双写，exporter 不把标准数据重新展开成旧字段，backend 不改变 token 数值。
- GenAI span 使用标准命名 `chat {model}` / `execute_tool {name}`；生命周期识别看
  `gen_ai.operation.name`，不再依赖历史 span name。
- Codex、Claude、offline trajectory、RL rail 和前端 projector 与主 callback 采用相同形状。
- `gen_ai.tool.type` 仅使用 `function`、`extension`、`datastore` 标准枚举；MCP 等传输协议使用
  `openjiuwen.tool.protocol`，避免将内部分类伪装成标准枚举。
- 前端固定记录所对齐的 upstream revision，避免“latest”成为不可审计的浮动描述。
- 前端标准键集中在可整体替换的 `semconv/gen-ai-semconv.generated.ts`；`constants.ts` 只保留
  OTel core、DSH、OpenJiuwen 定义及旧消费接口映射。

## 拒绝的方案

1. **新旧字段永久双写**：会制造两个权威来源，增加 token 和 attribute budget，并让截断位置依赖
   exporter 顺序。
2. **在 exporter 端按后端重解释 usage**：同一 trace 发往不同后端会得到不同事实，无法复核。
3. **把 request id/message count 塞入 `gen_ai.*`**：这些不是标准字段，污染标准命名空间。
4. **通过 span name 判断类型**：标准 span name 包含 operation/model/tool，名称不再固定。

## 验证

- 全量单测 collect 成功：17,661 项。
- trajectory、callback、tracer、harness、bridge、RL 与前端 projector 组合回归覆盖结构化消息、工具
  correlation、usage、stream timing、redaction、legacy read-only conversion。
- 生产源码扫描确认旧键只剩 `legacy_semconv.py` 的历史读取常量。

## 已知遗留

OpenTelemetry GenAI conventions 仍处于 Development 稳定级别；升级 upstream revision 时必须先更新
标准常量、producer、consumer、fixture 与本文档，再跑相同的全链路扫描和回归。
