# openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters

`openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converters` 是 **DL 节点到平台 DSL 的转换器包**，负责：

- 抽象基类 `BaseConverter`：统一 `convert()` 流程（公共配置 → 子类特定配置 → 边）；
- 各具体节点转换器：Start、End、LLM、IntentDetection、Questioner、Code、Plugin、Output、Branch。

**包导出**：`BaseConverter`、`StartConverter`、`EndConverter`、`LLMConverter`、`IntentDetectionConverter`、`QuestionerConverter`、`CodeConverter`、`PluginConverter`、`OutputConverter`、`BranchConverter`。

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

DL 单节点转换基类。子类实现 `_convert_specific_config()`。

**参数**：

* **node_data**(Dict[str, Any])：当前节点 DL 字典。
* **nodes_dict**(Dict[str, Any])：全部节点 id → 数据，用于引用解析。
* **resource**(Dict[str, Any]，可选)：插件等资源。默认值：`None`。
* **position**([Position](../dl_transformer.md))：画布位置。默认值：`Position(0, 0)`。

### abstractmethod _convert_specific_config() -> None

子类写入节点 `data`、分支等。

### convert() -> None

执行 `convert_common_config`、`_convert_specific_config`、`convert_edges`。

---

## 具体转换器类

以下类均继承 `BaseConverter`，由 [DLTransformer](../dl_transformer.md) 按节点 `type` 选择。构造参数与 `BaseConverter` 相同（`PluginConverter` 通常需要传入 `resource`）。

| 类 | 模块路径 | 说明 |
|----|----------|------|
| `StartConverter` | `...converters.start_converter` | 起始节点 |
| `EndConverter` | `...converters.end_converter` | 结束节点 |
| `LLMConverter` | `...converters.llm_converter` | 大模型节点 |
| `IntentDetectionConverter` | `...converters.intent_detection_converter` | 意图识别节点 |
| `QuestionerConverter` | `...converters.questioner_converter` | 提问器节点 |
| `CodeConverter` | `...converters.code_converter` | 代码节点 |
| `PluginConverter` | `...converters.plugin_converter` | 插件/工具节点 |
| `OutputConverter` | `...converters.output_converter` | 输出节点 |
| `BranchConverter` | `...converters.branch_converter` | 分支节点 |

各类在 `convert()` 后向 `Workflow` 图结构追加 `node` 与 `edges`（见 [dl_transformer.md](../dl_transformer.md)）。

**样例**（通过 DLTransformer 间接使用）：

```python
>>> from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer import DLTransformer
>>> t = DLTransformer()
>>> callable(t.transform_to_dsl)
True
```
