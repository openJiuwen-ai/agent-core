# openjiuwen.dev_tools.tune.base

## class openjiuwen.dev_tools.tune.base.Case

```python
openjiuwen.dev_tools.tune.base.Case(inputs: Dict[str, Any], label: Dict[str, Any], tools: Optional[List[ToolInfo]] = None)
```

Defines test case, containing input parameters, expected output and optional tool information.
**Parameters**:

- **inputs** (Dict[str, Any]): Input parameters of the case.
- **label** (Dict[str, Any]): Expected output or label information, used to verify result correctness.
- **tools** (List[[ToolInfo](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschematool_calltoolcall)], optional): Represents list of tools (e.g., APIs, plugins, etc.) that can be called by this case, used for multi-tool collaboration scenarios, default value: `None`.

**Example**:

```python
>>> from openjiuwen.dev_tools.tune.base import Case
>>>
>>> # Construct test case
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

`EvaluatedCase` is a case that has been evaluated by an evaluator, containing original case, model output, evaluation score and reason description, suitable for result recording and analysis in automated evaluation processes.

**Parameters**:

- **case** (Case): Original test case, containing fields like inputs, label, tools.
- **answer** (Dict[str, Any]): Model output result, used to compare with label.
- **score** (float): Evaluation score. Range is [0, 1], higher score indicates better evaluation result.
- **reason** (str): Reason description for evaluation failure or pass, used for debugging and analysis.

**Example**:

```python
>>> from openjiuwen.dev_tools.tune.base import EvaluatedCase
>>> from openjiuwen.dev_tools.tune.base import Case
>>> # Construct test case
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
