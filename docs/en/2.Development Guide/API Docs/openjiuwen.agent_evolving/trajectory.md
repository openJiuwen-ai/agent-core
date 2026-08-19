# openjiuwen.agent_evolving.trajectory

Canonical trajectory values, in-process span capture, synchronous archives, and stateless projections.

## Public package exports

```python
from openjiuwen.agent_evolving.trajectory import (
    FileTrajectoryStore,
    InMemoryTrajectoryStore,
    Trajectory,
    TrajectorySpanProcessor,
    TrajectoryStore,
)
```

The root package intentionally exports only these five names. Span accessors, Team selectors, message projection,
and offline construction are explicit submodule APIs.

## class Trajectory

```python
class Trajectory:
    @classmethod
    def from_otlp(cls, payload: Mapping[str, object]) -> "Trajectory": ...

    @classmethod
    def from_historical_otlp(cls, payload: Mapping[str, object]) -> "Trajectory": ...

    def to_otlp(self) -> dict[str, object]: ...
    def with_resource_attributes(self, attributes: Mapping[str, Any]) -> "Trajectory": ...
```

`Trajectory` is an immutable value object that owns a detached canonical OTLP JSON payload. Current payloads require
non-empty `resourceSpans` and `trajectory_id`; `team_id` or `member_id` also requires `session_id`.

Properties:

- `trajectory_id: str`
- `session_id: str | None`
- `team_id: str | None`
- `member_id: str | None`
- `resource_attributes: dict[str, Any]`
- `metadata: dict[str, Any]`, an alias for detached resource attributes

`to_otlp()`, `resource_attributes`, and `metadata` return detached values. `with_resource_attributes()` creates a new
trajectory and does not mutate the original.

Use `from_historical_otlp()` only at a compatibility read boundary where historical data may lack session identity.

## class TrajectorySpanProcessor

`TrajectorySpanProcessor` implements the OpenTelemetry `SpanProcessor` interface and fans out completed spans to
in-process subscriptions. Register one shared instance with the active `TracerProvider`, then inject that same object
into every consuming Rail.

```python
subscription = processor.subscribe(
    include_span_categories={"llm", "tool"},
    trace_id=None,
)

increment, issues = processor.drain(subscription)
processor.unsubscribe(subscription)
```

- `include_span_categories` filters categories such as `llm` and `tool`.
- `trace_id` routes a subscription to one trace; omit it for invoke-local `ContextVar` routing.
- `drain()` atomically returns the pending canonical increment and capture-quality issues.
- `unsubscribe()` releases the handle after the final drain.
- `suppress()` prevents work performed by an optimizer or reviewer from re-entering trajectory subscriptions while
  leaving normal exporters active.

Do not read trajectories back from exporters or create one processor per Rail.

## class TrajectoryStore

```python
class TrajectoryStore(Protocol):
    def save(self, trajectory: Trajectory, version: str | None = None) -> None: ...

    def load(
        self,
        trajectory_id: str,
        version: str | None = None,
    ) -> Trajectory | None: ...

    def query(
        self,
        *,
        version: str | None = None,
        session_id: str | None = None,
        team_id: str | None = None,
        member_id: str | None = None,
        case_id: str | None = None,
        source: str | None = None,
    ) -> list[Trajectory]: ...
```

Stores are synchronous archives. New writes accept only canonical `Trajectory` values.

### InMemoryTrajectoryStore

Process-local implementation keyed by version and trajectory ID. It stores canonical detached snapshots.

### FileTrajectoryStore

JSONL-backed implementation. `load()` and `query()` recognize supported historical step records and OTLP resource
aliases, upgrade them once at the read boundary, and return canonical `Trajectory` values. New records are canonical
OTLP only.

## Stateless span accessors

Use `openjiuwen.agent_evolving.trajectory.spans` to inspect canonical OTLP without building a second long-lived model:

```python
from openjiuwen.agent_evolving.trajectory.spans import (
    iter_spans,
    read_llm_exchange,
    read_llm_messages,
    read_rl_fields,
    read_span_error,
    read_tool_call,
    read_usage,
    span_attributes,
    span_events,
    span_identity,
    span_status,
)
```

The module also provides normalization, merge, crop, and trim helpers. These helpers return detached canonical values
and do not mutate the input trajectory.

## Conversation messages

Use `trajectory_to_messages()` when a consumer needs ordered conversation messages:

```python
from openjiuwen.agent_evolving.trajectory.messages import trajectory_to_messages

messages = trajectory_to_messages(
    trajectory,
    fields={"content", "name", "tool_calls", "tool_call_id"},
)
```

The projection sorts spans, merges overlapping prompt snapshots, normalizes OpenAI-compatible tool calls, and can
select the message fields required by the caller. It does not write messages back into `Trajectory`.

`tool_call_id()`, `tool_call_name()`, and `tool_call_arguments()` accept mapping or object-style tool calls, including
nested `function` fields.

## Team selectors

`openjiuwen.agent_evolving.trajectory.team` provides stateless Team views:

- `span_category()` and `is_team_span()`
- `select_team_spans()` and `iter_team_spans()`
- `build_team_forest()`, `team_forest()`, and `flatten_forest()`
- `select_member_spans()`, `member_spans()`, `member_ids()`, and `group_spans_by_member()`
- `select_task_spans()` and `descendant_spans()`

These functions select or organize existing canonical spans. They do not create a Team trajectory registry or a
second Team data model.

## Offline construction

Legacy-style construction and session extraction remain available only under the explicit offline namespace:

```python
from openjiuwen.agent_evolving.trajectory.offline import (
    TrajectoryBuilder,
    TrajectoryExtractor,
)
```

Use these APIs for offline conversion and dataset tooling. Online evolution and RL capture use
`TrajectorySpanProcessor` and Rails instead.

## Migration notes

The following former root/runtime APIs were removed:

- `LLMCallDetail`, `ToolCallDetail`, and `TrajectoryStep`
- online `TrajectoryBuilder` / `TrajectoryExtractor` exports
- `MemberTrajectorySnapshot`, `TrajectorySource`, `TrajectorySink`, and `InMemoryTrajectoryRegistry`
- `TeamTrajectory`, `TeamTrajectoryAggregator`, and online aggregation helpers

Do not restore compatibility imports for these names. Use canonical spans, stateless accessors, Team root-trace
routing, or the explicit offline namespace as appropriate.
