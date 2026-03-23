# openjiuwen.dev_tools.agent_builder

`openjiuwen.dev_tools.agent_builder` is the **Agent Builder** development toolkit in openJiuwen. It:

- Exposes a unified entry point `AgentBuilder` (see below) to orchestrate LLM Agent and workflow Agent builds per session;
- Uses [AgentBuilderExecutor](executor.md) and [BaseAgentBuilder](builders.md) for reusable execution and state management;
- Works with progress reporting, dialog history, and resource retrieval submodules.

**Submodule documentation**:

- [builders](builders.md): `BaseAgentBuilder`, `AgentBuilderFactory`, and builder factories.
- [builders/llm_agent](builders/llm_agent.md): LLM Agent clarification, generation, and DSL conversion.
- [builders/workflow](builders/workflow.md): Workflow intent, design, DL generation, and validation.
- [builders/workflow/dl_transformer](builders/workflow/dl_transformer.md): DL, Mermaid, and workflow DSL conversion.
- [builders/workflow/dl_transformer/converters](builders/workflow/dl_transformer/converters.md): DL node to platform DSL converters.
- [builders/workflow/workflow_designer](builders/workflow/workflow_designer.md): SE workflow design (basic, branch, reflection).
- [executor](executor.md): Executor and `create_core_model`.
- [resource](resource.md): Plugin resource retrieval and preprocessing.
- [utils](utils.md): Enums, progress types, and utility helpers.

**Package exports** (`from openjiuwen.dev_tools.agent_builder import ...`):

| Symbol | Notes |
|--------|--------|
| `AgentBuilder` | This document (`AgentBuilder` section below) |
| `AgentBuilderExecutor` | See [executor.md](executor.md) |
| `HistoryManager`, `HistoryCache` | See [executor.md](executor.md) |
| `BaseAgentBuilder`, `LlmAgentBuilder`, `WorkflowBuilder`, `AgentBuilderFactory` | See [builders.md](builders.md) and subpages |

---

## class openjiuwen.dev_tools.agent_builder.main.AgentBuilder

```python
class openjiuwen.dev_tools.agent_builder.main.AgentBuilder(
    model_info: Optional[Dict[str, Any]] = None,
    history_manager_map: Optional[Dict[str, HistoryManager]] = None,
    agent_builder_map: Optional[Dict[str, BaseAgentBuilder]] = None,
)
```

Unified Agent build entry point. Keeps per-session [HistoryManager](executor.md) and [BaseAgentBuilder](builders.md) maps and delegates to [AgentBuilderExecutor](executor.md) to assemble responses.

**Parameters**:

* **model_info**(Dict[str, Any], optional): LLM configuration passed to the executor to construct [Model](../../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmmodelmodel). Should include `model_provider` (or `client_provider`), `model_name` (or `model`), `api_key`, etc. Default: `{}`.
* **history_manager_map**(Dict[str, HistoryManager], optional): Map of `session_id` to reusable history managers. Default: `None` (empty dict internally).
* **agent_builder_map**(Dict[str, BaseAgentBuilder], optional): Map of `session_id` to reusable builder instances. Default: `None` (empty dict internally).

### build_agent(query: str, session_id: str, agent_type: str = "llm_agent") -> Dict[str, Any]

Unified build API. Creates an [AgentBuilderExecutor](executor.md), runs `execute()`, then builds a response dict (may include `dsl`, `response`, `mermaid_code`, `status`, etc.).

**Parameters**:

* **query**(str): User input or follow-up.
* **session_id**(str): Session id for history and builder lookup.
* **agent_type**(str, optional): `'llm_agent'` or `'workflow'`. Default: `'llm_agent'`.

**Returns**:

**Dict[str, Any]**, at least `status`, `session_id`, `agent_type`; other keys depend on the build phase.

### build_llm_agent(query: str, session_id: str) -> Dict[str, Any]

Same as `build_agent(query, session_id, "llm_agent")`.

### build_workflow(query: str, session_id: str) -> Dict[str, Any]

Same as `build_agent(query, session_id, "workflow")`.

### get_session_history(session_id: str, k: Optional[int] = None) -> List[Dict[str, str]]

Returns dialog history for the session.

**Parameters**:

* **session_id**(str): Session id.
* **k**(int, optional): Return only the last `k` messages; `None` means all. Default: `None`.

**Returns**:

**List[Dict[str, str]]** with `role` and `content`.

### clear_session(session_id: str) -> None

Clears session history and calls `reset()` on the session builder if present.

### get_build_status(session_id: str) -> Dict[str, Any]

Returns the builder `get_build_status()` or `{"state": "not_found"}` if the session has no builder.

### staticmethod get_progress(session_id: str) -> Optional[Dict[str, Any]]

Reads build progress from the global [progress_manager](utils.md); returns `BuildProgress.to_dict()` when present.

### staticmethod map_state_to_status(state: str, agent_type: str) -> str

Maps internal states `initial` / `processing` / `completed` to external `status` strings (e.g. LLM Agent starts as `clarifying`, workflow as `requesting`).

**Example**:

```python
>>> from openjiuwen.dev_tools.agent_builder import AgentBuilder
>>>
>>> builder = AgentBuilder(
...     model_info={
...         "model_provider": "OpenAI",
...         "model_name": "your_model",
...         "api_key": "your_api_key",
...         "api_base": "https://api.example.com/v1",
...     }
... )
>>> result = builder.build_llm_agent(
...     query="Create a customer service assistant",
...     session_id="session_demo_001",
... )
>>> isinstance(result.get("session_id"), str)
True
```
