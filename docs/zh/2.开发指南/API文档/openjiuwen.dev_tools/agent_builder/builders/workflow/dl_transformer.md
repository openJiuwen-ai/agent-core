# openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer

`openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer` 是 **工作流 DL 转换子模块**，负责：

- `DLTransformer`：DL（JSON 数组）→ Mermaid；DL + 资源 → 平台工作流 DSL JSON；
- `models` 中定义 `NodeType`、`Workflow`、`Node`、`Edge`、`Position` 等数据结构；
- 节点级转换实现见 [converters.md](dl_transformer/converters.md)。

**包导出**：`DLTransformer`、`Workflow`、`Node`、`Edge`、`NodeType`、`Position`。

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.dl_transformer.DLTransformer

```python
class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.dl_transformer.DLTransformer()
```

DL 与 Mermaid、平台 DSL 之间的转换协调类。内部按节点 `type` 注册 [converters](dl_transformer/converters.md) 中的转换器类。

### classmethod get_dsl_converter_registry() -> Dict[str, type]

返回 DL 节点类型字符串到转换器类的拷贝。

### staticmethod collect_plugin(tool_id_list, plugin_dict, tool_id_map) -> List[Dict[str, Any]]

从资源中收集插件工具信息，供 DSL 依赖段使用。

### staticmethod transform_to_mermaid(dl_content: str) -> str

将 DL JSON 数组字符串转为 Mermaid 流程图文本（经 `SimpleIrToMermaid`）。

**异常**：

* **ValueError**：DL 不是 JSON 数组。

### transform_to_dsl(dl_content: str, resource: Optional[Dict[str, Any]] = None) -> str

将 DL 转为平台工作流 DSL JSON 字符串。若传入 `resource`，会先用 `plugins` 与 `plugin_dict` / `tool_id_map` 规范化插件列表，再逐节点实例化对应 Converter 并组装 `Workflow` 对象。

**异常**：

* **ValueError**：DL 不是 JSON 数组。

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models.NodeType

节点类型枚举。每项包含：

* **dl_type**(str)：DL 中的 `type` 字段取值（如 `Start`、`LLM`）。
* **dsl_type**(str)：平台 DSL 中的类型编码字符串。

取值包括：`Start`、`End`、`LLM`、`IntentDetection`、`Questioner`、`Code`、`Plugin`、`Output`、`Branch` 等。

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models.SourceType

来源类型枚举：`ref`、`constant`。

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models.Position

* **x**(float)、**y**(float)：画布坐标。

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models.Workflow

工作流图容器。

* **nodes**(List[Node])：节点列表。
* **edges**(List[Edge])：边列表。

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models.Node

工作流节点（中间表示），由各类 Converter 填充。含 `id`、`type`、`meta`、`data`（`DataConfig`）等字段。

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models.Edge

工作流边。

* **source_node_id**(str)、**target_node_id**(str)：端点节点 ID。
* **source_port_id**(str，可选)：端口 ID。
