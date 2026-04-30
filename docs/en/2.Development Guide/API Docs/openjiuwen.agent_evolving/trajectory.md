# openjiuwen.agent_evolving.trajectory

`openjiuwen.agent_evolving.trajectory` defines execution trajectory types (Trajectory, TrajectoryStep, ExecutionSpec, UpdateKey, Updates), and interfaces for extracting trajectories from Session and filtering steps by conditions.

---

## Type Aliases

* **UpdateKey**: `Tuple[str, str]`, represents (operator_id, target).
* **Updates**: `Dict[UpdateKey, Any]`, operator parameter update collection.
* **StepKind**: `Literal["llm", "tool", "memory", "workflow", "agent"]`, single step type.

---

## class openjiuwen.agent_evolving.trajectory.types.ExecutionSpec

Metadata for a single execution (read-only dataclass).

* **case_id**(str): Sample ID.
* **execution_id**(str): Execution ID.
* **seed**(int, optional): Random seed. Default: `None`.
* **tags**(Dict[str, Any], optional): Tags. Default: `None`.

---

## class openjiuwen.agent_evolving.trajectory.types.TrajectoryStep

Single step in a trajectory.

* **kind**(StepKind): Step type (llm/tool/memory/workflow/agent).
* **operator_id**(str, optional): Operator ID.
* **agent_id**(str, optional): Agent ID.
* **role**(str, optional): Role.
* **node_id**(str, optional): Node ID.
* **inputs**(Any): Inputs.
* **outputs**(Any): Outputs.
* **error**(Dict[str, Any], optional): Error information.
* **start_time_ms**(int, optional): Start time (milliseconds).
* **end_time_ms**(int, optional): End time (milliseconds).
* **meta**(Dict[str, Any]): Metadata (e.g., invoke_id, parent_invoke_id, child_invokes, etc.).

---

## class openjiuwen.agent_evolving.trajectory.types.Trajectory

Complete execution trajectory.

* **case_id**(str): Sample ID.
* **execution_id**(str): Execution ID.
* **trace_id**(str, optional): Trace ID.
* **steps**(List[TrajectoryStep]): Step list.
* **edges**(List[Tuple[int, int]], optional): Dependency edges between steps (index pairs). Default: `None`.

---

## class openjiuwen.agent_evolving.trajectory.operation.TracerTrajectoryExtractor

Extracts Trajectory from Session's tracer. Parses agent and workflow spans, builds steps and edges, doesn't depend on core internal implementation details (only depends on invoke_type, name, inputs, outputs, error, meta_data, llm_call_id, etc. fields).

### extract(session, execution: ExecutionSpec) -> Trajectory

Extracts one trajectory from session's tracer.

**Parameters**:

* **session**: Session object with tracer attribute.
* **execution**(ExecutionSpec): Metadata for this execution.

**Returns**:

**Trajectory**, containing all steps and dependencies.

---

## func openjiuwen.agent_evolving.trajectory.operation.iter_steps(trajectories, *, case_id=None, operator_id=None, kind=None)

Iterates TrajectoryStep by optional conditions.

**Parameters**:

* **trajectories**(List[Trajectory]): Trajectory list.
* **case_id**(str, optional): Filter by case_id.
* **operator_id**(str, optional): Filter by operator_id.
* **kind**(StepKind, optional): Filter by step type (e.g., `"llm"`, `"tool"`).

**Returns**:

**Iterator[TrajectoryStep]**, steps satisfying all given conditions.

---

## func openjiuwen.agent_evolving.trajectory.operation.get_steps_for_case_operator(trajectories, case_id, operator_id, kind='llm')

Gets all matching steps for specified case and operator.

**Parameters**:

* **trajectories**(List[Trajectory]): Trajectory list.
* **case_id**(str): Sample ID.
* **operator_id**(str): Operator ID.
* **kind**(StepKind, optional): Step type, default `"llm"`.

**Returns**:

**List[TrajectoryStep]**, matching step list.

---

## class TeamTrajectory

Aggregated team trajectory dataclass for team-level view of a single session.

* **team_id** (str): Team ID.
* **session_id** (str): Session ID.
* **combined** (Trajectory): Merged view of all member trajectories, sorted by `start_time_ms`.
* **members** (Dict[str, Trajectory]): Mapping from member ID to its individual trajectory.

---

## class TeamTrajectoryAggregator

Aggregates team member trajectories into a team-level view.

```text
class TeamTrajectoryAggregator(
    *,
    store: Optional[TrajectoryStore] = None,
    trajectories_dir: Optional[Path] = None,
    team_id: str,
)
```

**Parameters**:

* **store** (TrajectoryStore, optional): Trajectory store instance.
* **trajectories_dir** (Path, optional): Trajectory directory path (backward-compatible).
* **team_id** (str): Team ID.

**Either `store` or `trajectories_dir` must be provided**.

### aggregate(session_id, filter_collaborative) -> TeamTrajectory

Aggregate all member trajectories for the given session.

**Parameters**:

* **session_id** (str): Session ID.
* **filter_collaborative** (bool): Whether to filter collaboration-relevant steps, defaults to `True`.

**Returns**:

* **TeamTrajectory**: Contains `members` dict and `combined` merged view.

---

## func filter_member_trajectory(trajectory: Trajectory) -> Trajectory

Filter member trajectory to keep only collaboration-relevant steps.

Retains step types:

* Steps with cross-member meta markers (`invoke_id`, `parent_invoke_id`, `child_invokes`)
* Collaborative tool calls (`view_task`, `claim_task`, `send_message`, etc.)
* Skill file reads (`read_file` calls containing "skill")

**Parameters**:

* **trajectory** (Trajectory): Member trajectory.

**Returns**:

* **Trajectory**: Filtered trajectory, preserving other fields.

---

## Constants

### COLLABORATIVE_TOOLS

Collaborative tool name set:

```python
COLLABORATIVE_TOOLS = frozenset({
    "view_task",
    "claim_task",
    "send_message",
    "workspace_meta",
    "read_file",
    "write_file",
})
```

### CROSS_MEMBER_META_KEYS

Cross-member interaction meta key set:

```python
CROSS_MEMBER_META_KEYS = frozenset({
    "invoke_id",
    "parent_invoke_id",
    "child_invokes",
})
```
