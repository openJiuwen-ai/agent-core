# openjiuwen.dev_tools.agent_builder.builders

`openjiuwen.dev_tools.agent_builder.builders` is the **Agent builder** subpackage. It:

- Defines abstract `BaseAgentBuilder` with shared resource retrieval, state machine, and `execute` template flow;
- Uses `AgentBuilderFactory` with [AgentType](utils.md) to create [LlmAgentBuilder](builders/llm_agent.md) or [WorkflowBuilder](builders/workflow.md);
- See [builders/llm_agent.md](builders/llm_agent.md) and [builders/workflow.md](builders/workflow.md) for concrete logic.

**Exports**: `BaseAgentBuilder`, `LlmAgentBuilder`, `WorkflowBuilder`, `AgentBuilderFactory`.

---

## class openjiuwen.dev_tools.agent_builder.builders.base.BaseAgentBuilder

```python
class openjiuwen.dev_tools.agent_builder.builders.base.BaseAgentBuilder(
    llm: Model,
    history_manager: HistoryManager,
    progress_reporter: Optional[ProgressReporter] = None,
)
```

Abstract base class for builders (template method). `execute` updates resources then dispatches by [BuildState](utils.md) to `_handle_initial`, `_handle_processing`, `_handle_completed`.

Subclasses must implement `_handle_*`, `_reset_internal_state`, `_is_workflow_builder`.

**Parameters**:

* **llm**([Model](../../../openjiuwen.core/foundation/llm/llm.md)): Model used for retrieval and generation.
* **history_manager**([HistoryManager](executor.md)): Session history manager.
* **progress_reporter**([ProgressReporter](utils.md), optional): Progress reporter. Default: `None`.

### property state -> BuildState

Current build state.

### property resource -> Dict[str, Any]

Accumulated resource dict (plugins, workflows, etc.).

### execute(query: str) -> Union[str, Dict[str, Any]]

Runs one build round. May return intermediate strings (clarification, Mermaid) or final DSL dict.

**Raises**:

* **ApplicationError**: Invalid or unknown [BuildState](utils.md).

### reset() -> None

Resets to initial state, clears resources, calls `_reset_internal_state()`.

### get_build_status() -> Dict[str, Any]

Returns a dict including `state`, `resource_count`, etc.

### abstractmethod _handle_initial(query: str, dialog_history: List[Dict[str, str]]) -> Union[str, Dict[str, Any]]

Initial state handler (subclass).

### abstractmethod _handle_processing(query: str, dialog_history: List[Dict[str, str]]) -> Union[str, Dict[str, Any]]

Processing state handler (subclass).

### abstractmethod _handle_completed(query: str, dialog_history: List[Dict[str, str]]) -> Union[str, Dict[str, Any]]

Completed state handler (subclass).

### abstractmethod _reset_internal_state() -> None

Subclass-specific reset.

### abstractmethod _is_workflow_builder() -> bool

Whether this builder targets workflow agents.

### is_workflow_builder() -> bool

Returns `_is_workflow_builder()`.

---

## class openjiuwen.dev_tools.agent_builder.builders.factory.AgentBuilderFactory

Factory for builder instances. First `create` lazily registers `LlmAgentBuilder` and `WorkflowBuilder`.

### classmethod create(agent_type: AgentType, llm: Model, history_manager: HistoryManager) -> BaseAgentBuilder

Creates a builder for the given type.

**Parameters**:

* **agent_type**([AgentType](utils.md)): e.g. `AgentType.LLM_AGENT`, `AgentType.WORKFLOW`.
* **llm**([Model](../../../openjiuwen.core/foundation/llm/llm.md)): Model instance.
* **history_manager**([HistoryManager](executor.md)): History manager.

**Raises**:

* **ValueError**: Unsupported `agent_type`.

### classmethod register(agent_type: AgentType, builder_class: Type[BaseAgentBuilder]) -> None

Registers a custom builder class.

**Raises**:

* **TypeError**: `builder_class` does not inherit `BaseAgentBuilder`.

### classmethod get_supported_types() -> list[AgentType]

Returns registered [AgentType](utils.md) values.

### classmethod clear_registry() -> None

Clears the registry (often for tests).

### classmethod get_registered_builders() -> Dict[AgentType, Type[BaseAgentBuilder]]

Shallow copy of the registry.

---

## class openjiuwen.dev_tools.agent_builder.builders.llm_agent.builder.LlmAgentBuilder

See [builders/llm_agent.md](builders/llm_agent.md).

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.builder.WorkflowBuilder

See [builders/workflow.md](builders/workflow.md).
