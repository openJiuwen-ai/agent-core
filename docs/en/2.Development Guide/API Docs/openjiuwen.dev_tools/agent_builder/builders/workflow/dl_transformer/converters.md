# openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters

`openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters` provides **DL node to platform DSL converters**:

- Abstract `BaseConverter`: shared `convert()` flow (common config → subclass-specific config → edges);
- Concrete converters: Start, End, LLM, IntentDetection, Questioner, Code, Plugin, Output, Branch.

**Exports**: `BaseConverter`, `StartConverter`, `EndConverter`, `LLMConverter`, `IntentDetectionConverter`, `QuestionerConverter`, `CodeConverter`, `PluginConverter`, `OutputConverter`, `BranchConverter`.

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base.BaseConverter

```python
class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters.base.BaseConverter(
    node_data: Dict[str, Any],
    nodes_dict: Dict[str, Any],
    resource: Optional[Dict[str, Any]] = None,
    position: Position = Position(0, 0),
)
```

Base class for single-node DL conversion. Subclasses implement `_convert_specific_config()`.

**Parameters**:

* **node_data**(Dict[str, Any]): Current node DL dict.
* **nodes_dict**(Dict[str, Any]): All nodes by id for reference resolution.
* **resource**(Dict[str, Any], optional): Plugins and related resources. Default: `None`.
* **position**([Position](../dl_transformer.md)): Canvas position. Default: `Position(0, 0)`.

### abstractmethod _convert_specific_config() -> None

Subclass fills node `data`, branches, etc.

### convert() -> None

Runs `convert_common_config`, `_convert_specific_config`, `convert_edges`.

---

## Concrete converter classes

All inherit `BaseConverter` and are selected by [DLTransformer](../dl_transformer.md) from node `type`. Constructor matches `BaseConverter` (`PluginConverter` usually needs `resource`).

| Class | Module | Role |
|--------|--------|------|
| `StartConverter` | `...converters.start_converter` | Start node |
| `EndConverter` | `...converters.end_converter` | End node |
| `LLMConverter` | `...converters.llm_converter` | LLM node |
| `IntentDetectionConverter` | `...converters.intent_detection_converter` | Intent detection node |
| `QuestionerConverter` | `...converters.questioner_converter` | Questioner node |
| `CodeConverter` | `...converters.code_converter` | Code node |
| `PluginConverter` | `...converters.plugin_converter` | Plugin/tool node |
| `OutputConverter` | `...converters.output_converter` | Output node |
| `BranchConverter` | `...converters.branch_converter` | Branch node |

After `convert()`, nodes and edges are appended to the `Workflow` graph (see [dl_transformer.md](../dl_transformer.md)).

**Example** (via `DLTransformer`):

```python
>>> from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer import DLTransformer
>>> t = DLTransformer()
>>> callable(t.transform_to_dsl)
True
```
