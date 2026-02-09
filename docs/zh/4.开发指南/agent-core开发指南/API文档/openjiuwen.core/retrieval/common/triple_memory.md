# openjiuwen.core.retrieval.common.triple_memory

## class openjiuwen.core.retrieval.common.triple_memory.TripleMemory

三元组记忆数据模型，用于在智能检索的多步推理中保留唯一三元组。

**属性**：

* **included_triples**(Set[str])：已记录三元组的小写字符串表示。
* **memory**(List[tuple[str, ...]])：按插入顺序保存的原始三元组元组。

**属性方法**：

* **triples_str**(str)：所有已保存三元组的可读字符串（按行分隔）。

**方法**：

* **extend_memory**(new_triple: tuple[str, ...]) -> None：如果规范化后的字符串尚未出现，则添加单个三元组。
* **batch_extend_memory**(new_triples: list[tuple[str, ...]]) -> None：对 `new_triples` 中的每个三元组调用 `extend_memory`。

**样例**：

```python
>>> from openjiuwen.core.retrieval.common.triple_memory import TripleMemory
>>>
>>> memory = TripleMemory()
>>> memory.extend_memory(("Beijing", "is", "capital"))
>>> memory.batch_extend_memory([
...     ("Paris", "is", "capital"),
...     ("Beijing", "is", "capital")
... ])
>>> print(memory.triples_str)
(Beijing is capital)
(Paris is capital)
```
