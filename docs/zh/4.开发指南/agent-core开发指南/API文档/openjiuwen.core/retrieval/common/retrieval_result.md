# openjiuwen.core.retrieval.common.retrieval_result

## class openjiuwen.core.retrieval.common.retrieval_result.SearchResult

搜索结果数据模型，表示向量存储返回的搜索结果。

**参数**：

* **id**(str)：结果ID。
* **text**(str)：文本内容。
* **score**(float)：相关性得分。
* **metadata**(Dict[str, Any])：元数据。默认值：{}。

## class openjiuwen.core.retrieval.common.retrieval_result.RetrievalResult

检索结果数据模型，表示检索器返回的检索结果。

**参数**：

* **text**(str)：文本内容。
* **score**(float)：相关性得分。
* **metadata**(Dict[str, Any])：元数据。默认值：{}。
* **doc_id**(str, 可选)：文档ID。默认值：None。
* **chunk_id**(str, 可选)：文本块ID。默认值：None。

**样例**：

```python
>>> from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult
>>> 
>>> # 创建检索结果
>>> result = RetrievalResult(
...     text="这是检索到的文本",
...     score=0.95,
...     metadata={"source": "doc1"},
...     doc_id="doc1",
...     chunk_id="chunk1"
... )
>>> print(f"Text: {result.text}, Score: {result.score}, Doc ID: {result.doc_id}")
Text: 这是检索到的文本, Score: 0.95, Doc ID: doc1
```

