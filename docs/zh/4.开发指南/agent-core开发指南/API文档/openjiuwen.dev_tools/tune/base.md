# openjiuwen.dev_tools.tune.base

## class openjiuwen.dev_tools.tune.base.Case

```python
openjiuwen.dev_tools.tune.base.Case(inputs: Dict[str, Any], label: Dict[str, Any], tools: Optional[List[ToolInfo]] = None)
```

定义测试用例，包含输入参数、期望输出和可选工具信息。
**参数**：

- **inputs**(Dict[str, Any])：用例的输入参数。
- **label**(Dict[str, Any])：期望的输出或标签信息，用于验证结果正确性。
- **tools**(List[[ToolInfo](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschematool_calltoolcall)]，可选)：表示该用例可调用的工具列表（如API、插件等），用于多工具协作场景, 默认值：`None`。

**样例**：

```python
>>> from openjiuwen.dev_tools.tune.base import Case
>>>
>>> # 构造测试用例
>>> case1 = Case(
...     inputs = {"query": "沈自邠，字茂仁，号几轩，又号茂秀，浙江秀水长溪（今嘉兴南汇）人"},
...     label = {"output": "[沈自邠]"},
... )
>>> case2 = Case(
...     inputs={"query": "潘之恒（约1536—1621）字景升，号鸾啸生，冰华生，安徽歙县、岩寺人，侨寓金陵（今江苏南京）"},
...     label={"output": "[潘之恒]"}
... )
```

## class openjiuwen.dev_tools.tune.base.EvaluatedCase

```python
openjiuwen.dev_tools.tune.base.EvaluatedCase(case: Case, answer: Dict[str, Any], score: float = 0.0, reason: str = "")
```

`EvaluatedCase`为经过评估器评估后的用例，包含原始用例、模型输出、评估得分与原因说明，适用于自动化评测流程中的结果记录与分析。

**参数**：

- **case**(Case)：原始测试用例，包含inputs、label、tools等字段。
- **answer**(Dict[str, Any])：模型输出结果，用于与label对比。
- **score**(float)：评估得分。范围为[0, 1]，分值越大表示评估结果越好。
- **reason**(str)：评估失败或通过的原因说明，用于调试和分析。

**样例**：

```python
>>> from openjiuwen.dev_tools.tune.base import EvaluatedCase
>>> from openjiuwen.dev_tools.tune.base import Case
>>> # 构造测试用例
>>> case1 = Case(
...     inputs = {"query": "沈自邠，字茂仁，号几轩，又号茂秀，浙江秀水长溪（今嘉兴南汇）人"},
...     label = {"output": "[沈自邠]"},
... )
>>>
>>> evaluated_case = EvaluatedCase(
...     case=case1,
...     answer={"output": "[沈自邠, 茂仁]"},
...     score=0.0,
...     reason="答案不一致，模型回答比标准答案多了'茂仁'"
... )
```