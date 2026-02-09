# openjiuwen.core.retrieval.indexing.processor.chunker.base

## class openjiuwen.core.retrieval.indexing.processor.chunker.base.Chunker

文本分块器抽象基类，继承自Processor，提供文本分块接口。


```python
Chunker(chunk_size: int = 512, chunk_overlap: int = 50, length_function: Optional[Callable[[str], int]] = None, **kwargs: Any)
```

初始化文本分块器。

**参数**：

* **chunk_size**(int)：分块大小。默认值：512。
* **chunk_overlap**(int)：分块重叠大小。默认值：50。
* **length_function**(Callable[[str], int], 可选)：长度计算函数（默认使用字符计数）。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**异常**：

* **ValueError**：如果chunk_overlap大于等于chunk_size。

### chunk_text

```python
chunk_text(text: str) -> List[str]
```

分块文本。

**参数**：

* **text**(str)：待分块的文本。

**返回**：

**List[str]**，返回分块后的文本列表。

### chunk_documents

```python
chunk_documents(documents: List[Document]) -> List[TextChunk]
```

分块文档列表。

**参数**：

* **documents**(List[Document])：文档列表。

**返回**：

**List[TextChunk]**，返回文档分块列表。

