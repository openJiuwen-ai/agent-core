# openjiuwen.core.retrieval.common.triple_beam

## class openjiuwen.core.retrieval.common.triple_beam.TripleBeam

三元组beam数据模型，用于保存参与beam的三元组序列。

**参数**：

* **nodes**(List[RetrievalResult])：beam中保存的三元组结果。
* **score**(float)：与beam关联的聚合得分。

**属性**：

* **triples**(List[RetrievalResult])：当前beam中的三元组结果。
* **score**(float)：beam得分。

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
