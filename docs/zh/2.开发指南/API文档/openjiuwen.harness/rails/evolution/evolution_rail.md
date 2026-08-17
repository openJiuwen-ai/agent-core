# EvolutionRail 轨迹集成

`EvolutionRail` 是消费 canonical 执行轨迹的 Rail 基类，负责 processor subscription、drain、质量门禁、
scope-local clean window，以及演进任务期间的 suppression。

## 构造

```python
from openjiuwen.agent_evolving.trajectory import TrajectorySpanProcessor
from openjiuwen.harness.rails import EvolutionRail, EvolutionTriggerPoint

processor = TrajectorySpanProcessor()

rail = EvolutionRail(
    evolution_trigger=EvolutionTriggerPoint.AFTER_INVOKE,
    async_evolution=True,
    max_concurrent_evolution=1,
    trajectory_span_processor=processor,
    max_trajectory_spans=200,
)
```

Processor 必须已经注册到当前 OpenTelemetry provider；同一 runtime 的所有 Rails 应共享同一 processor
对象。

## 采集生命周期

```text
已结束 OpenTelemetry span
  -> TrajectorySpanProcessor
  -> EvolutionRail drain、质量门禁、clean-window merge
  -> _on_after_*(ctx, trajectory)
  -> _prepare_evolution_input(trajectory, ctx)
  -> run_evolution(prepared)
```

不要通过覆盖 `before_invoke()`、`after_model_call()`、`after_tool_call()`、`after_task_iteration()` 或
`after_invoke()` 自行管理 subscription。应覆盖以下 protected 扩展点：

- `_on_before_invoke(ctx)`
- `_on_after_model_call(ctx, trajectory)`
- `_on_after_tool_call(ctx, trajectory)`
- `_on_after_task_iteration(ctx, trajectory)`
- `_on_after_invoke(ctx, trajectory)`
- `_prepare_evolution_input(trajectory, ctx)`
- `run_evolution(prepared)`

所有 `_on_after_*` 方法都必须接受 `Trajectory | None`。`None` 表示该 hook 当前没有 clean projection，
例如目标 span 尚未结束或增量未通过质量门禁。

## 触发点

`EvolutionTriggerPoint` 包含：

- `AFTER_MODEL_CALL`
- `AFTER_TOOL_CALL`
- `AFTER_TASK_ITERATION`
- `AFTER_INVOKE`，默认值
- `NONE`，由子类自行触发

可覆盖 `_allow_evolution_trigger(trigger_point, ctx)` 拒绝某个自动触发，不影响轨迹采集。

## 后台输入

默认 prepared input 包含 detached `trajectory`、投影后的 `messages` tuple 和可选 `skill_name`。同步与后台
执行使用同一条 prepared-input 路径。

如果子类需要在 callback 返回后使用 callback 数据，应在 `_prepare_evolution_input()` 中完成复制；
`run_evolution()` 不应保留 `ctx` 或读取 live 私有窗口。

## 读取 clean window

```python
agent_view = rail.get_trajectory(
    session_id="session-1",
    member_id="agent-1",
)

team_view = team_rail.get_trajectory(
    session_id="session-1",
    team_id="research-team",
)
```

Agent scope 是 `session_id + member_id`。Team scope 是 `session_id + team_id`，不得同时传 `member_id`。
Getter 返回 detached clean view，不消费或 reset 窗口。

## 消息

子类可以使用 `_trajectory_to_messages(trajectory, fields=...)` 只请求实际消费的字段。投影会排序 span、
合并重叠 prompt 并规范化 tool call。

## Clean view 与 archive

`EvolutionRail` 不归档轨迹。需要保存一次完整 Agent invoke 时，使用 `TrajectoryRail` 和
`TrajectoryStore`。Clean view 可以跨同一演进 scope 的多个 invoke，不等于 execution archive。

## 子类检查清单

- 复用 runtime 已注册的 processor。
- 每个目标 hook 正确处理 `Trajectory | None`。
- Callback 结束前复制后台输入。
- 不直接调用 `subscribe()`、`drain()` 或 `unsubscribe()`。
- 不修改 `Trajectory`，不建立持久 step/detail 模型。
- 测试目标 trigger、缺失/损坏增量、异步清理和 Agent/Team 路由。
