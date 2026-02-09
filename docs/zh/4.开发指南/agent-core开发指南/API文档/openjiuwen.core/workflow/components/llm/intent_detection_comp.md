# openjiuwen.core.workflow

`openjiuwen.core.workflow.components.llm.intent_detection_comp` 模块提供工作流中的意图识别组件，用于根据用户输入与可选对话历史调用大模型进行意图分类，并配合 [BranchRouter] 将执行路由到不同下游节点。分类结果通过 [IntentDetectionCompConfig](intent_detection_comp.md#class-openjiuwencoreworkflowcomponentsllmintent_detection_compintentdetectioncompconfig) 中的 `category_name_list` 等配置，输出为分类 id、分类名称及原因。组件通过 `openjiuwen.core.workflow` 导出，建议使用 `from openjiuwen.core.workflow import IntentDetectionComponent, IntentDetectionCompConfig` 导入。更多组件说明见 [components](../../components.README.md)。

## class openjiuwen.core.workflow.components.llm.intent_detection_comp.IntentDetectionCompConfig

意图识别组件的配置数据类。

* **model_id**（str | None）：已注册模型 id，与 `model_client_config` + `model_config` 二选一。
* **model_client_config**（ModelClientConfig | None）：模型客户端配置。
* **model_config**（ModelRequestConfig | None）：模型请求配置。
* **category_name_list**（list[str]）：分类名称列表，与内部生成的 `分类1`、`分类2` 等一一对应，用于解析 LLM 输出与展示。
* **user_prompt**（str）：用户侧提示词内容，会填入默认模板中的 `user_prompt` 占位符。
* **example_content**（list[str]）：示例内容列表，用于 few-shot 提示。
* **enable_history**（bool）：是否将上下文中的对话历史加入意图识别输入，默认 `False`。
* **chat_history_max_turn**（int）：使用的对话历史最大轮数，默认 3。

---

## class openjiuwen.core.workflow.components.llm.intent_detection_comp.IntentDetectionComponent

```python
class openjiuwen.core.workflow.components.llm.intent_detection_comp.IntentDetectionComponent(component_config: Optional[IntentDetectionCompConfig] = None)
```

意图识别组件，实现 [ComponentComposable](../components.md)。内部持有一个 [BranchRouter]，通过 `add_component` 将本节点加入图并添加条件边，通过 `add_branch` 配置“分类 → 下游节点”的映射；`to_executable` 返回 IntentDetectionExecutable，执行时调用大模型得到分类结果并写入会话，供路由器求值。

**参数**：

- **component_config**（IntentDetectionCompConfig | None）：意图识别配置；为 `None` 时由可执行对象侧按需处理。

### add_component(graph: Graph, node_id: str, wait_for_all: bool = False) -> None

将本组件作为节点加入 [Graph](../../../graph/graph.md#class-graph)，并为该节点添加条件边，由内部 [BranchRouter] 根据意图识别结果路由到下游。

**参数**：

- **graph**（Graph）：工作流图实例。
- **node_id**（str）：本节点在图中的唯一 id。
- **wait_for_all**（bool）：是否等待所有前驱完成，默认 `False`。

### add_branch(condition: Union[str, Callable[[], bool], Condition], target: Union[str, list[str]], branch_id: str = None)

为内部 [BranchRouter] 添加一条分支：当 `condition` 成立时路由到 `target`。`condition` 通常为基于意图识别输出的表达式（如 `"${intent.result} == '出行'"`），参见 [ExpressionCondition](../condition/expression.md)。

**参数**：

- **condition**：条件，支持 str、无参 Callable 或 [Condition](../condition/condition.md) 子类。
- **target**：下游节点 id 或 id 列表。
- **branch_id**：可选分支标识。

### router() -> BranchRouter

返回内部使用的 [BranchRouter] 实例。

**返回**：

- **BranchRouter**：本组件的分支路由器。

### property to_executable() -> IntentDetectionExecutable

返回 IntentDetectionExecutable 实例，并为其设置内部 [BranchRouter]（用于在执行时 set_session）。

**返回**：

- **IntentDetectionExecutable**：本组件的可执行实现。

---