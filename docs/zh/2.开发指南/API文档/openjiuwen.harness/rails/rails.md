# rails

DeepAgent 护栏扩展。Rails 在 `BEFORE_MODEL_CALL`、`AFTER_MODEL_CALL`、`BEFORE_TOOL_CALL`、`AFTER_TOOL_CALL` 等生命周期事件上注入行为，由 `DeepAgent._register_rail_selective` 自动路由到内层 ReActAgent 或外层 DeepAgent。

---

## 基类

### class DeepAgentRail

```python
class DeepAgentRail(AgentRail): ...
```

所有 DeepAgent 专用 Rail 的基类。提供 `set_sys_operation()` 和 `set_workspace()` 注入方法。

---

## 内置 Rails 一览

| Rail | 说明 |
|---|---|
| `SecurityRail` | 安全护栏，强制执行文件路径作用域、输入清理和安全策略。工厂函数默认注入 |
| `TaskPlanningRail` | 任务规划护栏，在 `BEFORE_TASK_ITERATION` 时生成/更新 `TaskPlan` |
| `TaskCompletionRail` | 任务完成护栏，构建 `StopConditionEvaluator` 链，决定外层任务循环何时停止 |
| `TaskMemoryRail` | 任务记忆护栏，在迭代后总结轨迹并写入长期记忆 |
| `MemoryRail` | 记忆护栏，管理工作区内的记忆读写和日常记忆归档 |
| `SubagentRail` | 子智能体护栏（同步模式），注册 `TaskTool` 并管理子智能体的同步调用 |
| `SessionRail` | 会话护栏（异步模式），注册 `SessionSpawnTool` 实现子智能体的异步并发执行 |
| `SkillUseRail` | 技能使用护栏，在模型调用前注入可用技能列表到提示词 |
| `SkillEvolutionRail` | 技能进化护栏，在任务完成后从对话轨迹中提取可复用技能 |
| `AskUserRail` | 用户交互护栏，拦截 `ask_user` 工具调用并生成 HITL 中断 |
| `ConfirmInterruptRail` | 确认中断护栏，在危险操作前请求用户确认 |
| `BaseInterruptRail` | 中断基类护栏，`AskUserRail` 和 `ConfirmInterruptRail` 的公共基类 |
| `FileSystemRail` | 文件系统护栏，注册文件系统工具（ReadFile、WriteFile、EditFile、Glob、Grep、ListDir） |
| `ContextEngineeringRail` | 上下文工程护栏，在模型调用前动态调整上下文窗口 |
| `HeartbeatRail` | 心跳护栏，周期性写入 HEARTBEAT.md 状态文件 |
| `ProgressiveToolRail` | 渐进式工具护栏，根据需要动态暴露/隐藏工具，控制可见工具数量 |

---

## 事件路由

Rails 的回调根据事件类型自动路由：

| 事件类型 | 路由目标 |
|---|---|
| `BEFORE_MODEL_CALL`、`AFTER_MODEL_CALL`、`ON_MODEL_EXCEPTION` | 内层 ReActAgent |
| `BEFORE_TOOL_CALL`、`AFTER_TOOL_CALL`、`ON_TOOL_EXCEPTION` | 内层 ReActAgent |
| `BEFORE_INVOKE`、`AFTER_INVOKE` | 外层 DeepAgent |
| `BEFORE_TASK_ITERATION`、`AFTER_TASK_ITERATION` | 外层 DeepAgent |
