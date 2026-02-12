# openjiuwen.core.retrieval.utils.common

## function openjiuwen.core.retrieval.utils.common.deduplicate

Remove duplicates from an iterable while preserving the original order, optionally using a custom key function for deduplication.

**Parameters**:

* **data**(Iterable[T]): Source iterable from which duplicates should be removed (e.g., list or tuple).
* **key**(Callable[[T], Hashable], optional): Function that maps each item to a hashable key used for deduplication (e.g., `lambda x: x.id`). Defaults to the identity function.

**Returns**:

**List[T]**, a list containing the first occurrence of each deduplicated item in the original order.

**Example**:

```python
>>> from openjiuwen.core.retrieval.utils.common import deduplicate
>>>
>>> items = ["a", "b", "a", "c"]
>>> deduplicated = deduplicate(items)
>>> print(deduplicated)
["a", "b", "c"]
```
