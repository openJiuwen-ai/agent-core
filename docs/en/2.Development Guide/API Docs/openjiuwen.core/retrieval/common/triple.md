# openjiuwen.core.retrieval.common.triple

## class openjiuwen.core.retrieval.common.triple.Triple

Triple data model, representing a triple (subject-predicate-object) in a knowledge graph.

**Parameters**:

* **subject**(str): Subject.
* **predicate**(str): Predicate.
* **object**(str): Object.
* **metadata**(Dict[str, Any]): Metadata (e.g., `{"doc_id": "doc1", "source": "kb1"}`). Default: {}.

**Example**:

```python
>>> from openjiuwen.core.retrieval.common.triple import Triple
>>> 
>>> # Create triple
>>> triple = Triple(
...     subject="Beijing",
...     predicate="is",
...     object="the capital of China",
...     metadata={"doc_id": "doc1"}
... )
>>> print(f"Triple: {triple.subject} {triple.predicate} {triple.object}")
Triple: Beijing is the capital of China
```

