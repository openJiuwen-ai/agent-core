# openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer

`openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer` 是 **SE（Self-Evaluate）工作流设计子模块**，负责：

- 通过 `WorkflowDesigner` 串联「基础设计 → 分支设计 → 反思评估」，输出最终工作流设计文本；
- 提示词模板位于同目录 `basic_design_prompt`、`branch_design_prompt`、`reflection_evaluate_prompt` 等模块（不单独导出）。

**包导出**：`WorkflowDesigner`。

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.workflow_designer.WorkflowDesigner

```python
class openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.workflow_designer.WorkflowDesigner(
    llm: Model,
)
```

基于大模型的三阶段工作流设计器。

**参数**：

* **llm**([Model](../../../../../openjiuwen.core/foundation/llm/llm.md))：大模型实例。

### basic_design(user_input: str, tool_list: str) -> str

基础设计：结合用户需求与可用工具列表，输出初步方案。

### branch_design(user_input: str, basic_result: str) -> str

分支设计：在基础方案上识别分支并扩展结构。

### reflection_evaluation(user_input: str, basic_result: str, branch_result: str) -> str

反思评估：综合前三步输出，经 `parse_reflection_result` 提取「New Workflow Design」段落。

### staticmethod parse_reflection_result(reflection_result: str) -> str

从反思结果中截取优化后的设计正文；若无标记则返回全文。

### design(user_input: str, tool_list: str) -> str

依次调用 `basic_design` → `branch_design` → `reflection_evaluation`，返回最终设计字符串。

**样例**：

```python
>>> from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
>>> from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer import WorkflowDesigner
>>> import os
>>>
>>> # 以下为占位配置，请替换为真实环境变量或配置
>>> model = Model(
...     model_client_config=ModelClientConfig(
...         client_provider="OpenAI",
...         client_id="demo",
...         api_key=os.getenv("API_KEY", "your_api_key"),
...         api_base=os.getenv("API_BASE", ""),
...     ),
...     model_config=ModelRequestConfig(model="your_model"),
... )
>>> designer = WorkflowDesigner(model)
>>> text = designer.design("创建一个简单的审批工作流", tool_list="")
>>> isinstance(text, str)
True
```
