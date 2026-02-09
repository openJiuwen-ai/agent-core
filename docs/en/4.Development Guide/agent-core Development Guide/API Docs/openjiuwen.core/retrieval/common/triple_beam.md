# openjiuwen.core.retrieval.common.triple_beam

## class openjiuwen.core.retrieval.common.triple_beam.TripleBeam

Triple beam data model that holds a sequence of triples that participate in the beam.

**Parameters**:

* **nodes**(List[RetrievalResult]): The triples stored in this beam.
* **score**(float): Aggregated score associated with the beam.

**Properties**:

* **triples**(List[RetrievalResult]): The triples currently in the beam.
* **score**(float): Beam score.

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
