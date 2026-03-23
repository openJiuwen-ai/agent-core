# openjiuwen.dev_tools.agent_builder.resource

`openjiuwen.dev_tools.agent_builder.resource` 是 **构建期资源子模块**，负责：

- `ResourceRetriever`：基于对话与提示词从本地插件清单中检索相关插件；
- `PluginProcessor`：将原始插件 JSON 预处理为 `plugin_dict` 与 `tool_id` 映射。

**包导出**：`ResourceRetriever`、`PluginProcessor`。

---

## class openjiuwen.dev_tools.agent_builder.resource.retriever.ResourceRetriever

```python
class openjiuwen.dev_tools.agent_builder.resource.retriever.ResourceRetriever(
    llm: Model,
)
```

加载默认 `plugins.json`（或通过 `load_resources`），经 `PluginProcessor` 预处理后，在 `retrieve` 中调用大模型筛选相关插件。

**参数**：

* **llm**([Model](../../../openjiuwen.core/foundation/llm/llm.md))：用于检索推理的大模型。

### staticmethod load_resources(source_path: Optional[str] = None) -> List[Dict[str, Any]]

读取 JSON 文件中的 `plugins` 列表；文件不存在时返回空列表。

**异常**：

* 文件不存在时记录警告并返回 `[]`（不抛异常，行为以源码为准）。

### retrieve(dialog_history: List[Dict[str, str]], for_workflow: bool = True) -> Dict[str, Any]

根据对话历史返回资源字典（通常含 `plugins`、`plugin_dict`、`tool_id_map` 等；`for_workflow` 影响返回结构细节）。

---

## func openjiuwen.dev_tools.agent_builder.resource.processor.convert_type

将参数类型编号或字符串规范为类型名字符串（见 `TYPE_MAP`）。

**参数**：

* **param_type**(Any)：类型编号或字符串。

**返回**：

**str**，如 `string`、`integer`。

---

## class openjiuwen.dev_tools.agent_builder.resource.processor.PluginProcessor

插件资源预处理（静态方法为主）。

### staticmethod preprocess(raw_plugins: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]

返回 `(plugin_dict, tool_plugin_id_map)`，供检索与 DSL 依赖拼装使用。
