# EvolutionRail trajectory integration

`EvolutionRail` is the base class for Rails that consume canonical execution trajectories. It owns processor
subscription, draining, quality gating, scope-local clean windows, and suppression around evolution work.

## Construction

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

The processor must already be registered with the active OpenTelemetry provider. All Rails in one runtime should
share the same processor object.

## Collection lifecycle

```text
completed OpenTelemetry span
  -> TrajectorySpanProcessor
  -> EvolutionRail drain, quality gate, clean-window merge
  -> _on_after_*(ctx, trajectory)
  -> _prepare_evolution_input(trajectory, ctx)
  -> run_evolution(prepared)
```

Do not override `before_invoke()`, `after_model_call()`, `after_tool_call()`, `after_task_iteration()`, or
`after_invoke()` to manage subscriptions. Override the protected extension points instead:

- `_on_before_invoke(ctx)`
- `_on_after_model_call(ctx, trajectory)`
- `_on_after_tool_call(ctx, trajectory)`
- `_on_after_task_iteration(ctx, trajectory)`
- `_on_after_invoke(ctx, trajectory)`
- `_prepare_evolution_input(trajectory, ctx)`
- `run_evolution(prepared)`

Every `_on_after_*` method must accept `Trajectory | None`. `None` means no clean projection is available at that
hook, for example because the expected span has not ended or the increment failed quality checks.

## Trigger points

`EvolutionTriggerPoint` values are:

- `AFTER_MODEL_CALL`
- `AFTER_TOOL_CALL`
- `AFTER_TASK_ITERATION`
- `AFTER_INVOKE`, the default
- `NONE`, for subclass-owned triggering

Override `_allow_evolution_trigger(trigger_point, ctx)` to reject an automatic trigger without changing capture.

## Background input

The default prepared input contains a detached `trajectory`, a tuple of projected `messages`, and optional
`skill_name`. Synchronous and background execution use the same prepared-input path.

If a subclass needs callback data after the callback returns, copy it in `_prepare_evolution_input()`. Do not retain
`ctx` or read a live private window from `run_evolution()`.

## Reading the clean window

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

Agent scope is `session_id + member_id`. Team scope is `session_id + team_id` and must omit `member_id`. The getter
returns a detached clean view and does not consume or reset the window.

## Messages

Subclasses can use `_trajectory_to_messages(trajectory, fields=...)` to request only the fields they consume. The
projection sorts spans, merges overlapping prompts, and normalizes tool calls.

## Clean view versus archive

`EvolutionRail` does not archive trajectories. Use `TrajectoryRail` with a `TrajectoryStore` when one complete Agent
invoke must be saved. The clean view can span multiple invokes in one evolution scope and is not an execution archive.

## Subclass checklist

- Share the processor registered by the runtime.
- Handle `Trajectory | None` at every selected hook.
- Copy background inputs before the callback ends.
- Do not call `subscribe()`, `drain()`, or `unsubscribe()` directly.
- Do not mutate `Trajectory` or create a persistent step/detail model.
- Test the chosen trigger, missing/invalid increments, async cleanup, and Agent or Team route.
