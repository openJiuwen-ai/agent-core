# openjiuwen.agent_evolving.trajectory

`openjiuwen.agent_evolving.trajectory` defines the public trajectory data model, extraction interface, persistence interface, and team aggregation utilities used by the agent-evolving pipeline.

---

## Type Aliases

* **StepKind**: `Literal["llm", "tool"]`, step kind in the extracted trajectory.
* **CostInfo**: `Dict[str, int]`, aggregated token cost such as `{"input_tokens": N, "output_tokens": M}`.

---

## class openjiuwen.agent_evolving.trajectory.types.LLMCallDetail

Structured detail for one LLM step.

* **model**(str): Model name.
* **messages**(List[Dict[str, Any]]): Input messages.
* **response**(Dict[str, Any], optional): Parsed response payload.
* **tools**(List[Dict[str, Any]], optional): Tool schema passed to the model.
* **usage**(Dict[str, Any], optional): Usage metadata.
* **meta**(Dict[str, Any]): Extension metadata.

---

## class openjiuwen.agent_evolving.trajectory.types.ToolCallDetail

Structured detail for one tool step.

* **tool_name**(str): Tool name.
* **call_args**(Any): Tool input arguments.
* **call_result**(Any): Tool execution result.
* **tool_description**(str, optional): Tool description from resource metadata.
* **tool_schema**(Dict[str, Any], optional): Tool parameter schema.
* **tool_call_id**(str, optional): Tool call ID for artifact tracking.

---

## class openjiuwen.agent_evolving.trajectory.types.TrajectoryStep

Single extracted trajectory step.

* **kind**(StepKind): Step kind.
* **error**(Dict[str, Any], optional): Error payload.
* **start_time_ms**(int, optional): Step start time in milliseconds.
* **end_time_ms**(int, optional): Step end time in milliseconds.
* **detail**(LLMCallDetail | ToolCallDetail, optional): Structured step detail.
* **reward**(float, optional): Post-injected scalar reward.
* **prompt_token_ids**(List[int], optional): Prompt token IDs lifted from LLM response.
* **completion_token_ids**(List[int], optional): Completion token IDs lifted from LLM response.
* **logprobs**(Any, optional): Token log probabilities.
* **meta**(Dict[str, Any]): Extension metadata such as `operator_id`, `agent_id`, and invoke relationships.

---

## class openjiuwen.agent_evolving.trajectory.types.Trajectory

Complete extracted trajectory.

* **execution_id**(str): Unique execution ID.
* **steps**(List[TrajectoryStep]): Ordered steps.
* **source**(str): Execution source, default `"offline"`.
* **case_id**(str, optional): Offline dataset case identifier.
* **session_id**(str, optional): Session ID.
* **cost**(CostInfo, optional): Aggregated token cost.
* **meta**(Dict[str, Any]): Trajectory-level metadata.

---

## class openjiuwen.agent_evolving.trajectory.extractor.TrajectoryExtractor

Extracts `Trajectory` from `Session.tracer()` spans and normalizes span data into trajectory steps.

```text
class TrajectoryExtractor(resource_manager: Any = None)
```

### extract(session, case_id=None) -> Trajectory

Extract one trajectory from a session tracer.

**Parameters**:

* **session**: Session object exposing `tracer`.
* **case_id**(str, optional): Case identifier used for offline training.

**Returns**:

* **Trajectory**: Assembled trajectory with normalized steps.

---

## class openjiuwen.agent_evolving.trajectory.builder.TrajectoryBuilder

Incrementally records `TrajectoryStep` values and builds a final `Trajectory`.

```text
class TrajectoryBuilder(
    execution_id: str | None = None,
    *,
    session_id: str | None = None,
    source: str = "offline",
    case_id: str | None = None,
)
```

### record_step(step) -> None

Append one step to the builder.

### build() -> Trajectory

Build the final trajectory object.

---

## class openjiuwen.agent_evolving.trajectory.store.TrajectoryStore

Persistence protocol for saving, loading, and querying trajectories.

### save(trajectory, version=None) -> None

Save one trajectory.

### load(execution_id, version=None) -> Optional[Trajectory]

Load one trajectory by execution ID.

### query(version=None, **filters) -> List[Trajectory]

Query trajectories by metadata filters such as `session_id`, `case_id`, or `source`.

---

## class openjiuwen.agent_evolving.trajectory.store.InMemoryTrajectoryStore

In-memory trajectory store for tests and development.

---

## class openjiuwen.agent_evolving.trajectory.store.FileTrajectoryStore

JSONL-backed trajectory store.

```text
class FileTrajectoryStore(base_dir: Path)
```

---

## class openjiuwen.agent_evolving.trajectory.aggregator.TeamTrajectory

Aggregated team trajectory for a single session.

* **team_id**(str): Team ID.
* **session_id**(str): Session ID.
* **combined**(Trajectory): Merged team trajectory.
* **members**(Dict[str, Trajectory]): Member ID to individual trajectory mapping.

---

## class openjiuwen.agent_evolving.trajectory.aggregator.TeamTrajectoryAggregator

Aggregates member trajectories into one team-level view.

```text
class TeamTrajectoryAggregator(
    *,
    store: Optional[TrajectoryStore] = None,
    trajectories_dir: Optional[Path] = None,
    team_id: str,
)
```

### aggregate(session_id, filter_collaborative=True) -> TeamTrajectory

Aggregate all member trajectories for one session.

**Parameters**:

* **session_id**(str): Session to aggregate.
* **filter_collaborative**(bool): Whether to keep only collaboration-relevant member steps.

**Returns**:

* **TeamTrajectory**: Aggregated team view.

---

## func openjiuwen.agent_evolving.trajectory.aggregator.filter_member_trajectory(trajectory: Trajectory) -> Trajectory

Filter one member trajectory to collaboration-relevant steps only.
