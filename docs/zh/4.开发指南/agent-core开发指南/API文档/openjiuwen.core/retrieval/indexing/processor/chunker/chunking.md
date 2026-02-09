# openjiuwen.core.retrieval.indexing.processor.chunker.chunking

## class openjiuwen.core.retrieval.indexing.processor.chunker.chunking.TextChunker

文本分块器，支持基于字符或token的分块，并可配置文本预处理选项。


```python
TextChunker(chunk_size: int = 512, chunk_overlap: int = 50, chunk_unit: str = "char", embed_model: Optional[Any] = None, preprocess_options: Optional[Dict] = None, **kwargs: Any)
```

初始化文本分块器。

**参数**：

* **chunk_size**(int)：分块大小。默认值：512。
* **chunk_overlap**(int)：分块重叠大小。默认值：50。
* **chunk_unit**(str)：分块单位，"char"表示按字符，"token"表示按token。默认值："char"。
* **embed_model**(Any, 可选)：嵌入模型实例，当chunk_unit="token"时用于获取tokenizer。默认值：None。
* **preprocess_options**(Dict, 可选)：预处理选项，支持normalize_whitespace（规范化空白字符）和remove_url_email（移除URL和邮箱）。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### chunk_documents

```python
chunk_documents(documents: List[Document]) -> List[TextChunk]
```

分块文档列表。

**参数**：

* **documents**(List[Document])：文档列表。

**返回**：

**List[TextChunk]**，返回文档分块列表。

