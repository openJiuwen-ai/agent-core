# openjiuwen.dev_tools.agent_builder.resource

`openjiuwen.dev_tools.agent_builder.resource` provides **build-time resources**:

- `ResourceRetriever`: Retrieves relevant plugins from local manifest via LLM-guided selection from dialog context.
- `PluginProcessor`: Preprocesses raw plugin JSON into `plugin_dict` and `tool_id` mappings.

**Exports**: `ResourceRetriever`, `PluginProcessor`.

---

## class openjiuwen.dev_tools.agent_builder.resource.retriever.ResourceRetriever

```python
class openjiuwen.dev_tools.agent_builder.resource.retriever.ResourceRetriever(
    llm: Model,
)
```

Loads default `plugins.json` (or `load_resources`), preprocesses with `PluginProcessor`, then calls the model in `retrieve` to filter relevant plugins.

**Parameters**:

* **llm**([Model](../../../openjiuwen.core/foundation/llm/llm.md)): Model used for retrieval.

### staticmethod load_resources(source_path: Optional[str] = None) -> List[Dict[str, Any]]

Reads the `plugins` list from a JSON file; returns an empty list if the file is missing.

**Note**:

* Missing file may log a warning and return `[]` (see source).

### retrieve(dialog_history: List[Dict[str, str]], for_workflow: bool = True) -> Dict[str, Any]

Returns a resource dict (typically `plugins`, `plugin_dict`, `tool_id_map`; `for_workflow` may change structure).

---

## func openjiuwen.dev_tools.agent_builder.resource.processor.convert_type

Normalizes parameter type codes or strings to type name strings (see `TYPE_MAP`).

**Parameters**:

* **param_type**(Any): Numeric code or string.

**Returns**:

**str**, e.g. `string`, `integer`.

---

## class openjiuwen.dev_tools.agent_builder.resource.processor.PluginProcessor

Plugin preprocessing (mostly static helpers).

### staticmethod preprocess(raw_plugins: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]

Returns `(plugin_dict, tool_plugin_id_map)` for retrieval and DSL dependencies.
