# openjiuwen.core.retrieval.common.triple

## class openjiuwen.core.retrieval.common.triple.Triple

三元组数据模型，表示知识图谱中的三元组（主体-谓词-客体）。

**参数**：

* **subject**(str)：主体。
* **predicate**(str)：谓词。
* **object**(str)：客体。
* **metadata**(Dict[str, Any])：元数据（比如 `{"doc_id": "doc1", "source": "kb1"}`）。默认值：{}。

**样例**：

```python
>>> from openjiuwen.core.retrieval.common.triple import Triple
>>> 
>>> # 创建三元组
>>> triple = Triple(
...     subject="北京",
...     predicate="是",
...     object="中国的首都",
...     metadata={"doc_id": "doc1"}
... )
>>> print(f"Triple: {triple.subject} {triple.predicate} {triple.object}")
Triple: 北京 是 中国的首都
```

