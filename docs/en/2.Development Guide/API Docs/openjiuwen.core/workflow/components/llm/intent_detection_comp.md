# openjiuwen.core.workflow

The `openjiuwen.core.workflow.components.llm.intent_detection_comp` module provides intent detection components in workflows, used to call large language models for intent classification based on user input and optional conversation history, and route execution to different downstream nodes in conjunction with [BranchRouter]. Classification results are output as classification id, classification name and reason through configuration like `category_name_list` in [IntentDetectionCompConfig](./intent_detection_comp.md#class-openjiuwencoreworkflowcomponentsllmintent_detection_compintentdetectioncompconfig). Components are exported through `openjiuwen.core.workflow`, it is recommended to use `from openjiuwen.core.workflow import IntentDetectionComponent, IntentDetectionCompConfig` for import. For more component information, see [components](../../components.README.md).

## class openjiuwen.core.workflow.components.llm.intent_detection_comp.IntentDetectionCompConfig

Intent detection component configuration data class.

* **model_id** (str | None): Registered model id, mutually exclusive with `model_client_config` + `model_config`.
* **model_client_config** (ModelClientConfig | None): Model client configuration.
* **model_config** (ModelRequestConfig | None): Model request configuration.
* **category_name_list** (list[str]): Category name list, one-to-one correspondence with internally generated `分类1`, `分类2`, etc., used for parsing LLM output and display.
* **user_prompt** (str): User-side prompt content, will be filled into `user_prompt` placeholder in default template.
* **example_content** (list[str]): Example content list, used for few-shot prompting.
* **enable_history** (bool): Whether to include conversation history from context into intent detection input, default `False`.
* **chat_history_max_turn** (int): Maximum number of conversation history turns to use, default 3.

---

## class openjiuwen.core.workflow.components.llm.intent_detection_comp.IntentDetectionComponent

```python
class openjiuwen.core.workflow.components.llm.intent_detection_comp.IntentDetectionComponent(component_config: Optional[IntentDetectionCompConfig] = None)
```

Intent detection component, implements [ComponentComposable](../components.md). Internally holds a [BranchRouter], adds this node to graph and adds conditional edges through `add_component`, configures "category → downstream node" mapping through `add_branch`; `to_executable` returns IntentDetectionExecutable, which calls large language model to get classification result and writes to session during execution, for router evaluation.

**Parameters**:

- **component_config** (IntentDetectionCompConfig | None): Intent detection configuration; when `None`, handled by executable object side as needed.

### add_component(graph: Graph, node_id: str, wait_for_all: bool = False) -> None

Add this component as a node to [Graph](../../../graph/graph.md#class-graph), and add conditional edges for this node, route to downstream based on intent detection result by internal [BranchRouter].

**Parameters**:

- **graph** (Graph): Workflow graph instance.
- **node_id** (str): Unique id of this node in graph.
- **wait_for_all** (bool): Whether to wait for all predecessors to complete, default `False`.

### add_branch(condition: Union[str, Callable[[], bool], Condition], target: Union[str, list[str]], branch_id: str = None)

Add a branch to internal [BranchRouter]: route to `target` when `condition` is true. `condition` is usually an expression based on intent detection output (e.g., `"${intent.result} == '出行'"`), see [ExpressionCondition](../condition/expression.md).

**Parameters**:

- **condition**: Condition, supports str, parameterless Callable or [Condition](../condition/condition.md) subclass.
- **target**: Downstream node id or id list.
- **branch_id**: Optional branch identifier.

### router() -> BranchRouter

Returns internally used [BranchRouter] instance.

**Returns**:

- **BranchRouter**: Branch router of this component.

### property to_executable() -> IntentDetectionExecutable

Returns IntentDetectionExecutable instance, and sets internal [BranchRouter] for it (used to set_session during execution).

**Returns**:

- **IntentDetectionExecutable**: Executable implementation of this component.

---
