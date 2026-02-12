# openjiuwen.core.retrieval.common.triple_memory

## class openjiuwen.core.retrieval.common.triple_memory.TripleMemory

Triple memory data model that retains unique triples during multi-step reasoning in Agentic retriever.

```python
TripleMemory()
```

Initialize triple memory.

**Attributes**:

* **included_triples**(Set[str]): Lowercase string representations of the triples that have already been recorded (e.g., `{"beijing is capital", "paris is capital"}`).
* **memory**(List[tuple[str, ...]]): Original tuple triples stored in insertion order (e.g., `[("Beijing", "is", "capital"), ("Paris", "is", "capital")]`).

### property triples_str

```python
triples_str -> str
```

Get human-readable, newline-separated view of all stored triples.

**Returns**:

**str**, returns the formatted triple string.

### extend_memory

```python
extend_memory(new_triple: tuple[str, ...]) -> None
```

Adds a single triple if its normalized string form has not yet been observed.

**Parameters**:

* **new_triple**(tuple[str, ...]): Triple to add (e.g., `("Beijing", "is", "capital")`).

### batch_extend_memory

```python
batch_extend_memory(new_triples: list[tuple[str, ...]]) -> None
```

Calls `extend_memory` for each triple in `new_triples`.

**Parameters**:

* **new_triples**(list[tuple[str, ...]]): List of triples to add (e.g., `[("Beijing", "is", "capital"), ("Paris", "is", "capital")]`).

**Example**:

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