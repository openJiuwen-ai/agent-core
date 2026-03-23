# openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer

`openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer` handles **workflow DL conversion**:

- `DLTransformer`: DL (JSON array) → Mermaid; DL + resources → platform workflow DSL JSON;
- `models` defines `NodeType`, `Workflow`, `Node`, `Edge`, `Position`, etc.;
- Per-node conversion: [converters.md](dl_transformer/converters.md).

**Exports**: `DLTransformer`, `Workflow`, `Node`, `Edge`, `NodeType`, `Position`.

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.dl_transformer.DLTransformer

```python
class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.dl_transformer.DLTransformer()
```

Coordinates DL ↔ Mermaid ↔ platform DSL. Registers converter classes by node `type` from [converters](dl_transformer/converters.md).

### classmethod get_dsl_converter_registry() -> Dict[str, type]

Copy of the DL node type string to converter class map.

### staticmethod collect_plugin(tool_id_list, plugin_dict, tool_id_map) -> List[Dict[str, Any]]

Collects plugin tool metadata for DSL dependency sections.

### staticmethod transform_to_mermaid(dl_content: str) -> str

Converts DL JSON array string to Mermaid (via `SimpleIrToMermaid`).

**Raises**:

* **ValueError**: DL is not a JSON array.

### transform_to_dsl(dl_content: str, resource: Optional[Dict[str, Any]] = None) -> str

Converts DL to platform workflow DSL JSON. With `resource`, normalizes `plugins` using `plugin_dict` / `tool_id_map`, instantiates converters per node, and builds a `Workflow` object.

**Raises**:

* **ValueError**: DL is not a JSON array.

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models.NodeType

Node type enum. Each member has:

* **dl_type**(str): `type` field in DL (e.g. `Start`, `LLM`).
* **dsl_type**(str): platform DSL type code string.

Includes: `Start`, `End`, `LLM`, `IntentDetection`, `Questioner`, `Code`, `Plugin`, `Output`, `Branch`, etc.

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models.SourceType

Source kind enum: `ref`, `constant`.

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models.Position

* **x**(float), **y**(float): Canvas coordinates.

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models.Workflow

Workflow graph container.

* **nodes**(List[Node]): Node list.
* **edges**(List[Edge]): Edge list.

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models.Node

Intermediate workflow node filled by converters. Includes `id`, `type`, `meta`, `data` (`DataConfig`), etc.

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models.Edge

Workflow edge.

* **source_node_id**(str), **target_node_id**(str): Endpoint node ids.
* **source_port_id**(str, optional): Port id.
