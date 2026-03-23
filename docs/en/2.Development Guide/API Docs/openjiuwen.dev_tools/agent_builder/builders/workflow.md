# openjiuwen.dev_tools.agent_builder.builders.workflow

`openjiuwen.dev_tools.agent_builder.builders.workflow` implements **workflow Agent building**:

- `WorkflowBuilder`: intent → design → DL generation/reflection → Mermaid and cycle checks → workflow DSL;
- `IntentionDetector` (workflow package): initial process description and refine intent;
- `DLGenerator`, `Reflector`, `CycleChecker`: DL generation, validation, and Mermaid cycle checks.

**See also**:

- [WorkflowDesigner](workflow_designer.md): SE workflow design (basic / branch / reflection).
- [DLTransformer](workflow/dl_transformer.md): DL, Mermaid, and platform DSL.

**Exports**: `WorkflowBuilder`, `IntentionDetector`, `WorkflowDesigner`, `DLGenerator`, `Reflector`, `DLTransformer`, `CycleChecker`.

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.builder.WorkflowBuilder

```python
class openjiuwen.dev_tools.agent_builder.builders.workflow.builder.WorkflowBuilder(
    llm: Model,
    history_manager: HistoryManager,
)
```

Extends [BaseAgentBuilder](../builders.md). In `INITIAL`, if the user has not provided enough process detail, returns a prompt and enters `PROCESSING`; otherwise runs design, DL, Mermaid, etc. `PROCESSING` branches on intent and whether `_dl` is empty—continue design, refine DL, or emit final DSL.

**Parameters**:

* **llm**([Model](../../../../openjiuwen.core/foundation/llm/llm.md)): Model instance.
* **history_manager**([HistoryManager](../executor.md)): Session history.

### property workflow_name / workflow_name_en / workflow_desc / dl / mermaid_code

Current workflow metadata and intermediate DL/Mermaid strings (may be `None`).

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.intention_detector.IntentionDetector

Workflow-specific intent detection (distinct from `builders.llm_agent.IntentionDetector`): whether a process description exists and whether Mermaid should be refined.

**Parameters**:

* **llm**([Model](../../../../openjiuwen.core/foundation/llm/llm.md)): Model instance.

### classmethod format_dialog_history(dialog_history: List[Dict[str, Any]]) -> str

Formats history as `User:` / `Assistant:` text.

### staticmethod extract_intent(inputs: str) -> Dict[str, Any]

Parses JSON from model output (via [extract_json_from_text](../utils.md)).

### detect_initial_instruction(messages: List[Dict[str, Any]]) -> bool

Whether a runnable process description is present (`provide_process`).

**Raises**:

* **ApplicationError**: `WORKFLOW_INTENTION_DETECT_ERROR`.

### detect_refine_intent(messages: List[Dict[str, Any]], flowchart_code: str) -> bool

Whether the current Mermaid should be refined (`need_refined`).

**Raises**:

* **ApplicationError**: `WORKFLOW_INTENTION_DETECT_ERROR`.

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_generator.DLGenerator

Generates JSON-array DL strings from workflow design and node schemas, or refines existing DL/Mermaid.

**Parameters**:

* **llm**([Model](../../../../openjiuwen.core/foundation/llm/llm.md)): Model instance.

### generate(query: str, resource: Dict[str, List[Dict[str, Any]]]) -> str

Generates DL.

### refine(query: str, resource: Dict[str, List[Dict[str, Any]]], exist_dl: str, exist_mermaid: str) -> str

Refines DL given user instructions.

### staticmethod load_schema_and_examples() -> tuple[str, str, str]

Loads component text, schema, and examples from `dl_assets`.

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_reflector.Reflector

Validates DL JSON arrays: node types, references, branches, placeholders; errors accumulate in `errors`.

### check_format(generated_dl: str) -> None

Parses and validates; returns early on invalid JSON and records in `errors`.

### reset() -> None

Clears validation state.

### staticmethod extract_placeholder_content(input_str: str) -> Tuple[bool, List[str]]

Detects `${...}` placeholders.

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.cycle_checker.CycleChecker

**Parameters**:

* **llm**([Model](../../../../openjiuwen.core/foundation/llm/llm.md)): Model used for cycle-check prompts.

### check_mermaid_cycle(mermaid_code: str) -> str

Calls the LLM to assess problematic cycles in Mermaid.

### staticmethod parse_cycle_result_json(inputs: str) -> Tuple[bool, str]

Parses JSON to `(need_refined, loop_desc)`.

### check_and_parse(mermaid_code: str) -> Tuple[bool, str]

Combines `check_mermaid_cycle` and `parse_cycle_result_json`.

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.WorkflowDesigner

See [workflow_designer.md](workflow_designer.md).

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.dl_transformer.DLTransformer

See [workflow/dl_transformer.md](workflow/dl_transformer.md).
