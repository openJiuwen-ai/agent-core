# openjiuwen.core.retrieval.utils.fusion

## function rrf_fusion

```python
rrf_fusion(results_list: List[List[RetrievalResult]], k: int = 60) -> List[RetrievalResult | SearchResult]
```

RRF (Reciprocal Rank Fusion) fusion algorithm for fusing multiple retrieval result lists.

**Parameters**:

* **results_list**(List[List[RetrievalResult]]): List of multiple retrieval result lists (e.g., `[[result1, result2], [result3, result4]]`).
* **k**(int): RRF parameter. Default: 60.

**Returns**:

**List[RetrievalResult | SearchResult]**, returns a fused list of retrieval results sorted in descending order by RRF score.

**Description**:

The RRF algorithm calculates fusion score using the following formula:
```
score = sum(1.0 / (k + rank)) for each result list
```

where rank is the position of the result in each list (starting from 1), and k is the RRF parameter.

**Example**:

```python
>>> from openjiuwen.core.retrieval.utils.fusion import rrf_fusion
>>> from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult
>>> 
>>> # Assume there are two retrieval result lists
>>> results1 = [
...     RetrievalResult(text="Document 1", score=0.9),
...     RetrievalResult(text="Document 2", score=0.8),
... ]
>>> results2 = [
...     RetrievalResult(text="Document 2", score=0.85),
...     RetrievalResult(text="Document 3", score=0.7),
... ]
>>> 
>>> # Fuse results
>>> fused = rrf_fusion([results1, results2], k=60)
>>> print(f"Number of fused results: {len(fused)}")
Number of fused results: 3
```

