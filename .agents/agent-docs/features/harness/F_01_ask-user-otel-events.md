# F_01 ask_user 跨中断 OTel 事件

## 元信息

| 项 | 值 |
|---|---|
| 类型 | feature |
| 日期 | 2026-09-01 |
| 关联 spec | `S_04_rails-contract.md` |

## 问题

`ask_user` 由 interrupt rail 拦截，不进入普通 Tool 执行生命周期，因此不会触发
`on_tool_call_started/finished/error`。把一个 Tool Span 跨用户中断长期保存在内存里又无法覆盖
进程重启、任务恢复和上下文切换。

## 决策

- ask_user 不创建跨中断 Tool Span。
- 首次中断前，在当前 Agent/Step Span 写入 `ask_user.requested` OTel event；随后当前 Span按
  既有中断收尾路径闭合。
- 用户答案恢复后，在新 Span 写入 `ask_user.resolved` OTel event；恢复 Span同样正常闭合。
- 两条事件使用同一个 `interaction_id` / `tool_call_id`，并依赖 schema-v2 envelope 的 session、
  execution subject、sequence 和 recorded-at 字段确定性关联。
- requested 负载携带 arguments、完整 Schema 和 pending 状态；resolved 负载携带 answers、
  result、outcome 和 completed 状态。
- 前端只消费这两条权威事件，不从 `llm.call` 输出、工具目录或下一次模型输入补齐，也不保留
  历史兼容分支。

## 拒绝的方案

1. **跨中断保持开放 Tool Span**：依赖进程内对象，无法安全跨恢复边界。
2. **前端拼接 LLM 输出与下一次请求**：下一次请求可能不存在，时间只是请求边界估算，且职责
   越过 Core 事实层。
3. **同时保留旧数据回退**：会形成两套真相来源和重复记录，明确不采用。

## 验证基线

- Core 测试验证 requested/resolved 分别落在两个已闭合 Span 中，call id、Schema、answers 和
  outcome 完整。
- 前端 reducer 测试验证只含 LLM tool_call 时不生成 ask_user 行；两条事件齐备时生成一条完整
  TOOL 行并计算事件时间差。
