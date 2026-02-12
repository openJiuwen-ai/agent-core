# openjiuwen.dev_tools.tune.dataset.case_loader

## class openjiuwen.dev_tools.tune.dataset.case_loader.CaseLoader

```python
openjiuwen.dev_tools.tune.dataset.case_loader.CaseLoader(cases: List[Case])
```

CaseLoader class is used to manage cases, provides operations like case retrieval and shuffling, used in scenarios like Agent training and optimization.

**Parameters**:

- **cases** (List[[Case](base.md#class-openjiuwendev_toolstunebasecase)]): Test case list to be loaded, each `Case` should contain input parameters (inputs), expected output (label) and tools (tools, optional).

**Example**:

```python
>>> from openjiuwen.dev_tools.tune.base import Case
>>> from openjiuwen.dev_tools.tune.dataset.case_loader import CaseLoader
>>>
>>> # Construct test cases
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

Randomly shuffle case list.


**Parameters**:

- **random_seed** (int, optional): Random seed, used to control random shuffle result, default value: `0`.

**Example**:

```python
>>> from openjiuwen.dev_tools.tune.base import Case
>>> from openjiuwen.dev_tools.tune.dataset.case_loader import CaseLoader
>>>
>>> # Construct test cases
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

Returns total number of currently loaded cases.

**Returns**:

**int**, case count.

**Example**:

```python
>>> from openjiuwen.dev_tools.tune.base import Case
>>> from openjiuwen.dev_tools.tune.dataset.case_loader import CaseLoader
>>>
>>> # Construct test cases
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

Returns case list.


**Returns**:

**List[[Case](base.md#class-openjiuwendev_toolstunebasecase)]**, all currently loaded cases.

**Example**:

```python
>>> from openjiuwen.dev_tools.tune.base import Case
>>> from openjiuwen.dev_tools.tune.dataset.case_loader import CaseLoader
>>>
>>> # Construct test cases
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
