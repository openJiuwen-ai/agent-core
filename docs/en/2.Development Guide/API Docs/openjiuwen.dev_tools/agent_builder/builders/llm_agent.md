# openjiuwen.dev_tools.agent_builder.builders.llm_agent

`openjiuwen.dev_tools.agent_builder.builders.llm_agent` implements **LLM Agent building**:

- `LlmAgentBuilder`: full pipeline for clarification, config generation, and DSL conversion;
- `Clarifier`, `Generator`, `Transformer`: reusable components;
- `IntentionDetector`: detects whether the user wants to refine clarified requirements.

**Exports**: `LlmAgentBuilder`, `Clarifier`, `Generator`, `Transformer`.

---

## class openjiuwen.dev_tools.agent_builder.builders.llm_agent.builder.LlmAgentBuilder

```python
class openjiuwen.dev_tools.agent_builder.builders.llm_agent.builder.LlmAgentBuilder(
    llm: Model,
    history_manager: HistoryManager,
)
```

Extends [BaseAgentBuilder](../builders.md). In `INITIAL`, uses `Clarifier` then moves to `PROCESSING`. In `PROCESSING`, uses `IntentionDetector` to choose re-clarification or `Generator` + `Transformer` to produce a DSL JSON string, then `reset()`.

**Parameters**:

* **llm**([Model](../../../../openjiuwen.core/foundation/llm/llm.md)): Model instance.
* **history_manager**([HistoryManager](../executor.md)): Session history.

### property agent_config_info -> Optional[str]

Latest clarified factor output (internal).

### property factor_output_info -> Optional[str]

Clarification main output (internal).

### property display_resource_info -> Optional[str]

Resource display text (internal).

---

## class openjiuwen.dev_tools.agent_builder.builders.llm_agent.clarifier.Clarifier

Requirement clarifier: extracts Agent factors, then plans resources.

**Parameters**:

* **llm**([Model](../../../../openjiuwen.core/foundation/llm/llm.md)): Model instance.

### staticmethod parse_resource_output(resource_output: str, available_resources: Dict[Any, Any]) -> Tuple[str, Dict[str, List[str]]]

Parses resource planning sections; returns display text and resource id dict.

**Raises**:

* **ApplicationError**: Parse failure (`AGENT_BUILDER_RESOURCE_PARSE_ERROR`).

### clarify(messages: str, resource: Dict[Any, Any]) -> Tuple[str, str, Dict[str, List[str]]]

Runs two LLM calls; returns `(factor output, resource display, resource id dict)`.

---

## class openjiuwen.dev_tools.agent_builder.builders.llm_agent.generator.Generator

Generates structured Agent fields from clarification output, then merges resource ids.

**Parameters**:

* **llm**([Model](../../../../openjiuwen.core/foundation/llm/llm.md)): Model instance.

### staticmethod parse_info(content: str) -> Dict[str, Any]

Parses name, description, prompts, plugin/knowledge/workflow lists from model output.

### generate(message: str, agent_config_info: str, agent_resource_info: str, resource_id_dict: Dict[str, Any] = None) -> Dict[str, Any]

Returns a full config dict; `resource_id_dict` overrides parsed plugin/knowledge/workflow lists when provided.

---

## class openjiuwen.dev_tools.agent_builder.builders.llm_agent.transformer.Transformer

Converts a config dict to platform LLM Agent DSL JSON string (stateless).

### transform_to_dsl(agent_info: dict, resource: Dict[str, Any]) -> str

Builds DSL from `agent_info` and `resource` (`plugin_dict`, `tool_id_map`, `workflow_dict`, etc.).

### staticmethod collect_plugin / collect_workflow / convert_input_parameters / convert_output_parameters / build_plugin_dependencies / build_workflow_dependencies

Helpers for plugin/workflow dependency assembly in DSL.

---

## class openjiuwen.dev_tools.agent_builder.builders.llm_agent.intention_detector.IntentionDetector

Detects whether the user wants to refine clarified requirements (`need_refined`).

**Parameters**:

* **llm**: Same as [Model](../../../../openjiuwen.core/foundation/llm/llm.md).

### staticmethod extract_intent(inputs: str) -> Dict[str, Any]

Extracts JSON intent payload from model output.

### detect_refine_intent(query, agent_config_info) -> bool

Whether to re-clarify (calls LLM internally).

**Raises**:

* **ApplicationError**: Intent detection failure.
