# openjiuwen.core.retrieval.common.retrieval_result

## class openjiuwen.core.retrieval.common.retrieval_result.SearchResult

搜索结果数据模型，表示向量存储返回的搜索结果。

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/retrieval/` 目录下的示例代码，包括：
> - `chroma_query_expr.py` - ChromaDB 查询表达式示例
> - `milvus_query_expr.py` - Milvus 查询表达式示例

**参数**：

* **id**(str)：结果ID。
* **text**(str)：文本内容。
* **score**(float)：相关性得分。
* **metadata**(Dict[str, Any])：元数据（比如 `{"source": "doc1", "author": "Alice"}`）。默认值：{}。

## class openjiuwen.core.retrieval.common.retrieval_result.RetrievalResult

检索结果数据模型，表示检索器返回的检索结果。

**参数**：

* **text**(str)：文本内容。
* **score**(float)：相关性得分。
* **metadata**(Dict[str, Any])：元数据（比如 `{"source": "doc1", "author": "Alice"}`）。默认值：{}。
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

## class openjiuwen.core.retrieval.common.retrieval_result.MultiKBRetrievalResult

多知识库检索结果数据模型，用于跨多个知识库检索的场景。由 `retrieve_multi_kb_with_source` 返回。

**参数**：

* **text**(str)：文本内容。
* **score**(float)：相关性得分（跨知识库合并后取最高分）。
* **raw_score**(float)：合并前的原始相关性得分。
* **raw_score_scaled**(float)：缩放后的原始相关性得分，范围在 0 到 1 之间。
* **kb_ids**(list)：包含该结果的知识库ID列表。
* **metadata**(Dict[str, Any])：元数据。默认值：{}。

**样例**：

```python
>>> from openjiuwen.core.retrieval.common.retrieval_result import MultiKBRetrievalResult
>>> 
>>> result = MultiKBRetrievalResult(
...     text="相关文档文本",
...     score=0.92,
...     raw_score=0.88,
...     raw_score_scaled=0.90,
...     kb_ids=["kb_1", "kb_2"],
...     metadata={"source": "doc1"},
... )
>>> print(f"Text: {result.text}, Score: {result.score}, KBs: {result.kb_ids}")
Text: 相关文档文本, Score: 0.92, KBs: ['kb_1', 'kb_2']
```

