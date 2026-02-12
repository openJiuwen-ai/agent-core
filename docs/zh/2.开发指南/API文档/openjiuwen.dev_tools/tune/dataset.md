# openjiuwen.dev_tools.tune.dataset.case_loader

## class openjiuwen.dev_tools.tune.dataset.case_loader.CaseLoader

```python
openjiuwen.dev_tools.tune.dataset.case_loader.CaseLoader(cases: List[Case])
```

CaseLoader类用于管理用例，提供case的获取、乱序等操作，用于Agent训练、优化等场景。

**参数**：

- **cases**(List[[Case](base.md#class-openjiuwendev_toolstunebasecase)])：待加载的测试用例列表，每个`Case`应包含输入参数（inputs）、期望输出（label）以及工具（tools, 可选）。

**样例**：

```python
>>> from openjiuwen.dev_tools.tune.base import Case
>>> from openjiuwen.dev_tools.tune.dataset.case_loader import CaseLoader
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
>>> case_loader = CaseLoader([case1, case2])
```

### shuffle

```python
shuffle(random_seed: int = 0)
```

随机打乱用例列表。


**参数**：

- **random_seed**(int, 可选)：随机种子，用于控制随机打乱的结果，默认值：`0`。

**样例**：

```python
>>> from openjiuwen.dev_tools.tune.base import Case
>>> from openjiuwen.dev_tools.tune.dataset.case_loader import CaseLoader
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
>>> case_loader = CaseLoader([case1, case2])
>>> case_loader.shuffle()
>>> print(case_loader.get_cases())
[Case(inputs={'query': '沈自邠，字茂仁，号几轩，又号茂秀，浙江秀水长溪（今嘉兴南汇）人'}, label={'output': '[沈自邠]'}, tools=None, case_id='case_0'), Case(inputs={'query': '潘之恒（约1536—1621） 字景升，号鸾啸生，冰华生，安徽歙县、岩寺人，侨寓金陵（今江苏南京）'}, label={'output': '[潘之恒]'}, tools=None, case_id='case_1')]
```

---

### size

```python
size() -> int
```

返回当前加载的用例总数。

**返回**：

**int**，用例数量。

**样例**：

```python
>>> from openjiuwen.dev_tools.tune.base import Case
>>> from openjiuwen.dev_tools.tune.dataset.case_loader import CaseLoader
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
>>> case_loader = CaseLoader([case1, case2])
>>> print(case_loader .size())
2
```

---

### get_cases

```python
get_cases() -> List[Case]
```

返回用例列表。


**返回**：

**List[[Case](base.md#class-openjiuwendev_toolstunebasecase)]**，当前加载的所有用例。

**样例**：

```python
>>> from openjiuwen.dev_tools.tune.base import Case
>>> from openjiuwen.dev_tools.tune.dataset.case_loader import CaseLoader
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
>>> case_loader = CaseLoader([case1, case2])
>>> all_cases = case_loader.get_cases()
>>> for case in all_cases:
...     print(case.inputs)
{'query': '沈自邠，字茂仁，号几轩，又号茂秀，浙江秀水长溪（今嘉兴南汇）人'}
{'query': '潘之恒（约1536—1621）字景升，号鸾啸生，冰华生，安徽歙县、岩寺人，侨寓金陵（今江苏南京）'}
```
