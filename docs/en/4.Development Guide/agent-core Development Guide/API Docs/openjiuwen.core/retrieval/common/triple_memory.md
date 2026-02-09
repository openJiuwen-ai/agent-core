# openjiuwen.core.retrieval.common.triple_memory

## class openjiuwen.core.retrieval.common.triple_memory.TripleMemory

Triple memory data model that retains unique triples during multi-step reasoning in Agentic retriever.

**Attributes**:

* **included_triples**(Set[str]): Lowercase string representations of the triples that have already been recorded.
* **memory**(List[tuple[str, ...]]): Original tuple triples stored in insertion order.

**Properties**:

* **triples_str**(str): Human-readable, newline-separated view of all stored triples.

**Methods**:

* **extend_memory**(new_triple: tuple[str, ...]) -> None: Adds a single triple if its normalized string form has not yet been observed.
* **batch_extend_memory**(new_triples: list[tuple[str, ...]]) -> None: Calls `extend_memory` for each triple in `new_triples`.

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