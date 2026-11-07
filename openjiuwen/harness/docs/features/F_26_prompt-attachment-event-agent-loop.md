# Prompt Attachment Agent Loop 事件接入

## 元信息

| 项 | 值 |
|---|---|
| 类型 | feature |
| 日期 | 2026-11-07 |
| 范围 | `PromptAttachmentManager`、`DeepAgent`、factory、`BuildContext`、`DeepAgentSpec` |
| 关联 spec | [S_06 Prompts 与 i18n](../specs/S_06_prompts-and-l10n.md) |
| Core 契约 | [F_16 `prompt.attachment`](../../core/events/docs/features/F_16_prompt-attachment-event.md) |
| 设计依据 | `2026-11-03-openjiuwen-prompt-attachment-event-agent-loop-plan` |

## 背景

Harness 的 attachment 不是直接写入模型窗口，而是先作为带 metadata 的 `UserMessage`
写入 context history。为了让扩展在这个边界观察或改写 attachment，manager 需要获得
一个显式的 live EventBus，同时不能把 bus 塞进可序列化的 `DeepAgentConfig` 或 Spec。

## 接入决策

依赖沿以下路径传递：

```text
BuildContext.extension_bus / extras
  → DeepAgentSpec.resolve_parts()
  → DeepAgentParts.extension_bus
  → apply_deep_agent_parts()
  → DeepAgent.set_extension_bus()
  → PromptAttachmentManager.event_bus
```

`BuildContext` 增加 live-only `extension_bus` 字段；为兼容已有 team runtime，读取 helper
也识别 `openjiuwen.extension_bus`、`extension_bus`、`_extension_bus` 和既有
`team.extension_bus` extras。`DeepAgentParts` 和 factory 参数只携带这个运行时句柄，
不改变 `DeepAgentConfig` / `DeepAgentSpec` 的序列化形状。

manager 在 `sync_to_context()` 内完成候选判断和事件批处理，保持原 renderer、稳定排序、
snapshot/delta hash、history metadata、ContextEngine 选窗及 provider role projection。
事件结果只影响本次待渲染的 attachment 副本：改写文本写入本次 history，状态仍按原始
attachment 内容计算；返回 `None` 只省略该片段。

## 无扩展兼容性

`DeepAgent(card)`、未带 bus 的 `create_deep_agent()`、以及没有 live BuildContext 的冷恢复
均令 `manager.event_bus is None`。此时事件阶段直接返回原候选集合，不调用 `trigger()`、
不创建事件任务，现有 attachment 结果和逻辑保持不变。

有 bus 但没有用户或内置扩展 handler 时，Core event pass-through 返回原文；这条路径只
增加事件契约的空载层，不改变原有 message 内容、顺序、状态和投影结果。

## 拒绝的方案

- 在 ReActAgent 的两个 attachment 同步调用点分别接入事件：会造成重复触发和两处逻辑漂移。
- 从 `Runner.get_extension_bus()` 隐式读取：manager 作为可复用组件无法明确区分 agent 实例，
  也会让无 bus 测试路径出现隐藏全局依赖。
- 把 event bus 放入 Pydantic config/spec：bus 含异步运行时状态，不能安全序列化或跨进程恢复。
- 用事件改写结果更新 manager 的 source hash：扩展显示变换不应伪造 attachment 源变化。

## 验证

新增测试覆盖：

- snapshot 每个候选触发一次，批内并发，全部完成前没有 history message；
- delta 只触发 changed，origin/metadata 等内部字段不制造新 hash；
- handler 改写、省略、异常回退和取消传播；
- `DeepAgent`、`BuildContext`、`DeepAgentSpec` 到 manager 的显式 bus 传递。

既有 `test_prompt_attachment_manager.py` 全部回归，确保无 bus 的旧路径继续通过。

## 已知遗留

跨进程冷恢复不能自动携带 live EventBus；宿主需要在恢复时重新构造并注入。未注入时按
设计回退为无扩展路径。
