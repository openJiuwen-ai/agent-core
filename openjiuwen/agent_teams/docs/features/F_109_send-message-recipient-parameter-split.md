# `send_message` 收件参数分离

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-01 |
| 范围 | `openjiuwen/agent_teams/tools/tool_message.py`、双语工具描述与 teammate policy、`S_08_team-tools-contract.md` |
| 测试基线 | `test_team_tools.py -k SendMessageTool` 26 passed；tools/locales variants 111 passed；prompts 49 passed |
| Refs | #984 |

## 背景

完整形态的 `send_message.to` 原先使用 `anyOf(string, array<string>)` 同时表达单播、广播和多播。
一次真实 Qwen 工具调用已经在 reasoning 中正确选择“多播”，但参数生成仍选择了 string 分支，
把成员数组二次 JSON 编码成 `"[\"m1\",\"m2\"]"`。工具因此把整段文本当作一个 member_name，
返回 `Member '["m1", "m2"]' not found`。持久化的 `gen_ai.tool.definitions` 证明 anyOf 没有在适配层
丢失；问题是非严格 function calling 下 union 分支选择与值序列化不稳定。

## 决策

### 1. 用两个互斥字段表达两种收件结构

- `to: string`：单个 member_name、`"user"` 或广播 `"*"`。
- `targets: array<string>`：多播成员列表。

完整形态的 schema 不再包含 `anyOf`。因为 JSON Schema 无法只靠扁平 `required` 表达二选一，
`content` 仍是 schema 唯一无条件必填字段，`SendMessageTool._dispatch` 在运行时强制 `to` / `targets`
必须且只能提供一个。该校验也覆盖直接调用 `invoke`、绕过 schema 的 MCP 客户端。

### 2. 旧 `to: [...]` 不做静默兼容

向 `to` 传数组会返回明确错误，要求改用 `targets`；向 `targets` 传字符串、两个字段同时提供、
两个字段同时缺失也都会在写消息之前失败。若 `to` 收到 JSON 数组字符串，工具只解析到足以确认
它是错误形状并返回字段修正提示，不会据此投递。静默接受旧数组会让公开 schema 与实际契约再次
分裂，也无法阻止模型继续生成错误字段。

### 3. scheduled 成员形态不增加 `targets`

`ReportToLeaderTool` 仍只暴露 `to: enum ["leader", "user"]`。它没有 peer 多播能力，故 schema
不出现 `targets`；MCP 偷传 `targets` 时由 invoke 明确拒绝。完整形态与 scheduled 形态继续共享
`_send` / `_multicast` / `_broadcast` 等真实投递行为，不拆分工具名。

### 4. 多播内部语义保持不变

`_multicast` 的去空白、去重、禁止混入 `"*"` / `"user"`、全员覆盖拒绝、批量写与部分失败反馈
全部保持原样。改动只发生在进入 `_multicast` 前的字段选择，不改变消息持久化和事件投递。

## 拒绝的方案

- **只调整 anyOf 分支顺序，把 array 放前面**：只能改变生成倾向，不能消除 union 序列化失败。
- **开启 strict schema**：当前 Qwen 兼容链路未提供可依赖的严格 schema 执法；即使未来支持，
  分离字段仍比 union 更清楚。
- **解析 `to` 中的 JSON 数组字符串并代发**：会掩盖 schema 违规，使模型继续学习错误调用格式，
  且让“单个成员名字符串”承担第二种隐式语法。实现只检测该错误形状并拒绝。
- **拆成 `send_message` / `multicast_message` 两个工具**：两者共享同一内容规则、自动拉起和结果
  映射，仅收件字段不同；增加工具会扩大工具面和选择成本，参数分离已足够消除歧义。

## 验证

- schema 断言 `to.type == string`、`targets.type == array`，且无 `anyOf`；scheduled 形态无 targets。
- 全部多播用例改走 `targets`，既有成功、部分失败、去重、全员覆盖拒绝等行为不变。
- 新增旧 `to` 数组、JSON 数组字符串、`targets` 字符串、双字段、缺字段五类拒绝用例。
- content 上限在单播、多播、广播三条路径继续生效。

## 已知遗留

- 这是有意的 schema 破坏性变更：直接调用工具的旧客户端必须把 `to: [...]` 迁移为
  `targets: [...]`。字符串单播、`to="*"` 广播和 scheduled 成员调用不受影响。
