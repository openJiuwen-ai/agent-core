# openjiuwen.core.retrieval.common.triple_memory

## class openjiuwen.core.retrieval.common.triple_memory.TripleMemory

三元组记忆数据模型，用于在智能检索的多步推理中保留唯一三元组。

```python
TripleMemory()
```

初始化三元组记忆。

**属性**：

* **included_triples**(Set[str])：已记录三元组的小写字符串表示（比如 `{"beijing is capital", "paris is capital"}`）。
* **memory**(List[tuple[str, ...]])：按插入顺序保存的原始三元组元组（比如 `[("Beijing", "is", "capital"), ("Paris", "is", "capital")]`）。

### property triples_str

```python
triples_str -> str
```

获取所有已保存三元组的可读字符串（按行分隔）。

**返回**：

**str**，返回格式化的三元组字符串。

### extend_memory

```python
extend_memory(new_triple: tuple[str, ...]) -> None
```

如果规范化后的字符串尚未出现，则添加单个三元组。

**参数**：

* **new_triple**(tuple[str, ...])：要添加的三元组（比如 `("Beijing", "is", "capital")`）。

### batch_extend_memory

```python
batch_extend_memory(new_triples: list[tuple[str, ...]]) -> None
```

对 `new_triples` 中的每个三元组调用 `extend_memory`。

**参数**：

* **new_triples**(list[tuple[str, ...]])：要添加的三元组列表（比如 `[("Beijing", "is", "capital"), ("Paris", "is", "capital")]`）。

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
