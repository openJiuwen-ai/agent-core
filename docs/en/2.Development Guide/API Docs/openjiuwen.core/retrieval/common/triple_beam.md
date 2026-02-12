# openjiuwen.core.retrieval.common.triple_beam

## class openjiuwen.core.retrieval.common.triple_beam.TripleBeam

Triple beam data model that holds a sequence of triples that participate in the beam.

```python
TripleBeam(nodes: List[RetrievalResult], score: float)
```

Initialize triple beam.

**Parameters**:

* **nodes**(List[RetrievalResult]): The triples stored in this beam (e.g., `[RetrievalResult(text="...", score=0.9)]`).
* **score**(float): Aggregated score associated with the beam.

### property triples

```python
triples -> List[RetrievalResult]
```

Get the triples currently in the beam.

**Returns**:

**List[RetrievalResult]**, returns the list of triples currently in the beam.

### property score

```python
score -> float
```

Get beam score.

**Returns**:

**float**, returns the beam score.

**Example**:

```python
>>> from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult
>>> from openjiuwen.core.retrieval.common.triple_beam import TripleBeam
>>>
>>> nodes = [RetrievalResult(text="This is a test triple", score=0.9)]
>>> beam = TripleBeam(nodes=nodes, score=0.9)
>>> print(len(beam))
1
>>> print(beam.score)
0.9
```
