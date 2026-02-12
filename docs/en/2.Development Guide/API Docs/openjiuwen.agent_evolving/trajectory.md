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
