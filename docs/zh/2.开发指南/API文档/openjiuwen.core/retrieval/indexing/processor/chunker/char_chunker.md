# openjiuwen.core.retrieval.indexing.processor.chunker.char_chunker

## class openjiuwen.core.retrieval.indexing.processor.chunker.char_chunker.CharChunker

基于字符长度的固定大小分块器。


```python
CharChunker(chunk_size: int = 512, chunk_overlap: int = 50, **kwargs: Any)
```

初始化固定大小分块器。

**参数**：

* **chunk_size**(int)：分块大小（字符数）。默认值：512。
* **chunk_overlap**(int)：分块重叠大小（字符数）。默认值：50。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### chunk_text

```python
chunk_text(text: str) -> List[str]
```

分块文本。

**参数**：

* **text**(str)：待分块的文本。

**返回**：

**List[str]**，返回分块后的文本列表。

