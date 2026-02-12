# openjiuwen.core.retrieval.utils.common

## function openjiuwen.core.retrieval.utils.common.deduplicate

从可迭代对象中去重并保持原始顺序，可通过自定义key函数实现去重规则。

**参数**：

* **data**(Iterable[T])：需要去重的可迭代对象（比如 list 或 tuple）。
* **key**(Callable[[T], Hashable], 可选)：用于生成去重键的函数（比如 `lambda x: x.id`）。默认使用恒等函数。

**返回**：

**List[T]**，按原始顺序保留首次出现项的列表。

**样例**：

```python
>>> from openjiuwen.core.retrieval.utils.common import deduplicate
>>>
>>> items = ["a", "b", "a", "c"]
>>> deduplicated = deduplicate(items)
>>> print(deduplicated)
["a", "b", "c"]
```
