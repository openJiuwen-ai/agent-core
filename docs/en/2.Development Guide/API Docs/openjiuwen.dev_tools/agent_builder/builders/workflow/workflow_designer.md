# openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer

`openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer` is the **SE (Self-Evaluate) workflow design** submodule:

- `WorkflowDesigner` chains basic design → branch design → reflection evaluation to produce final design text;
- Prompt modules in the same folder (`basic_design_prompt`, `branch_design_prompt`, `reflection_evaluate_prompt`, etc.) are not exported separately.

**Exports**: `WorkflowDesigner`.

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.workflow_designer.WorkflowDesigner

```python
class openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.workflow_designer.WorkflowDesigner(
    llm: Model,
)
```

Three-stage workflow designer backed by an LLM.

**Parameters**:

* **llm**([Model](../../../../../openjiuwen.core/foundation/llm/llm.md)): Model instance.

### basic_design(user_input: str, tool_list: str) -> str

Basic design from requirements and available tools.

### branch_design(user_input: str, basic_result: str) -> str

Branch design: extends the basic plan with branches.

### reflection_evaluation(user_input: str, basic_result: str, branch_result: str) -> str

Reflection step; uses `parse_reflection_result` to extract the "New Workflow Design" section.

### staticmethod parse_reflection_result(reflection_result: str) -> str

Extracts optimized design body; returns full text if no marker is found.

### design(user_input: str, tool_list: str) -> str

Runs `basic_design` → `branch_design` → `reflection_evaluation` and returns the final string.

**Example**:

```python
>>> from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
>>> from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer import WorkflowDesigner
>>> import os
>>>
>>> # Placeholder config; replace with real environment values
>>> model = Model(
...     model_client_config=ModelClientConfig(
...         client_provider="OpenAI",
...         client_id="demo",
...         api_key=os.getenv("API_KEY", "your_api_key"),
...         api_base=os.getenv("API_BASE", ""),
...     ),
...     model_config=ModelRequestConfig(model="your_model"),
... )
>>> designer = WorkflowDesigner(model)
>>> text = designer.design("Create a simple approval workflow", tool_list="")
>>> isinstance(text, str)
True
```
