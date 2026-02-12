# openjiuwen.core.retrieval.common.triple_beam

## class openjiuwen.core.retrieval.common.triple_beam.TripleBeam

三元组beam数据模型，用于保存参与beam的三元组序列。

```python
TripleBeam(nodes: List[RetrievalResult], score: float)
```

初始化三元组beam。

**参数**：

* **nodes**(List[RetrievalResult])：beam中保存的三元组结果（比如 `[RetrievalResult(text="...", score=0.9)]`）。
* **score**(float)：与beam关联的聚合得分。

### property triples

```python
triples -> List[RetrievalResult]
```

获取当前beam中的三元组结果。

**返回**：

**List[RetrievalResult]**，返回当前beam中的三元组结果列表。

### property score

```python
score -> float
```

获取beam得分。

**返回**：

**float**，返回beam得分。

**样例**：

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
