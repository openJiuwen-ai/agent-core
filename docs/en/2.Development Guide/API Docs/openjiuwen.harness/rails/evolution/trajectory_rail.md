# Trajectory Rail

`TrajectoryRail` archives one canonical execution trajectory for each completed Agent invoke. It consumes completed
OpenTelemetry spans from a shared `TrajectorySpanProcessor` and writes synchronously to a `TrajectoryStore`.
Its inherited subscription currently selects the `llm` and `tool` categories; run-root and agent-tier spans remain
in the observability exporter and are not copied into this archive.

Install the observability dependencies before importing the Rail:

```bash
uv sync --extra observability
```

## Import

```python
from openjiuwen.agent_evolving.trajectory import FileTrajectoryStore, TrajectorySpanProcessor
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.rails import TrajectoryRail
```

## Construction

```text
class TrajectoryRail(
    *,
    trajectory_span_processor: TrajectorySpanProcessor,
    trajectory_store: TrajectoryStore,
    max_trajectory_spans: int | None = 200,
)
```

- `trajectory_span_processor` is the process-wide processor returned by
  `get_trajectory_span_processor()`. The demand coordinator registers this same object for Agent and Team
  observability; reuse it for trajectory, Skill evolution, and Team Skill Rails.
- `trajectory_store` is a synchronous canonical archive such as `FileTrajectoryStore` or
  `InMemoryTrajectoryStore`.
- `max_trajectory_spans` bounds the archive accumulated for one invoke. Use `None` for no span-count limit.

`priority = 10`, so the recorder performs its final drain after higher-priority behavior Rails have observed the
invoke. A store failure is logged and isolated from the Agent result.

## End-to-end setup

See the runnable `examples.agent_evolving.trajectory_rail_example` module for a focused file-persistence example
without Skill evolution behavior.

```python
from openjiuwen.agent_evolving.trajectory import FileTrajectoryStore
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.demand import get_trajectory_span_processor
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.observability import (
    AgentObservabilityRail,
    acquire_observability,
    close_agent_run_span,
    open_agent_run_span,
    release_observability,
)
from openjiuwen.harness.rails import TrajectoryRail

acquire_observability(ObservabilityConfig(exporter="file", traces_dir="./traces"))
processor = get_trajectory_span_processor()

store = FileTrajectoryStore("./trajectory-archive")
agent = create_deep_agent(
    model=model_client,
    rails=[
        AgentObservabilityRail(),
        TrajectoryRail(
            trajectory_span_processor=processor,
            trajectory_store=store,
        )
    ],
)
session_id = "trajectory-example-1"
session = Session(session_id=session_id, card=agent.card)

# Open one canonical run root for this Agent turn. The helper also registers
# the session-keyed fallback used by callbacks that finish in detached tasks.
root_span = open_agent_run_span(session_id=session_id, mode="trajectory.archive")
try:
    result = await Runner.run_agent(agent, {"query": "Summarize this task."}, session=session)
except BaseException as exc:
    close_agent_run_span(root_span, session_id=session_id, exception=exc)
    raise
else:
    close_agent_run_span(root_span, session_id=session_id, output=result.get("output", result))

# Release after Runner shutdown and after every run root has been closed.
release_observability()
```

After an invoke completes, query by stable resource metadata:

```python
trajectories = store.query(session_id=session_id)
```

The observability file exporter and `TrajectoryStore` are separate outputs: the exporter records raw spans for
observability, while `TrajectoryRail` writes one canonical invoke archive for application use. Do not read an
exporter file back into the Rail.

## Property

### trajectory_store -> TrajectoryStore

Returns the configured synchronous archive.

## Trace requirements

For a single Agent, acquire the Agent observability runtime before building the Agent, mount
`AgentObservabilityRail` before `TrajectoryRail`, and open one run root with `open_agent_run_span()` for each Agent
turn. Use the same session ID for the run root, `Session`, and store query. Close the run root only after the invoke
finishes, then release observability after Runner shutdown. Do not manually create a second
`TrajectorySpanProcessor` or manage `set_root_span()` in application code.

Team Rails route subscriptions through the active Team root trace. Acquire Team observability, mount the pair from
`maybe_observability_rails()`, and create or attach a recording root with a non-zero trace ID and `AT_TEAM_NAME`.
Finalize it only after all invokes that should share the Team trajectory have completed, then release the Team
observability demand.
