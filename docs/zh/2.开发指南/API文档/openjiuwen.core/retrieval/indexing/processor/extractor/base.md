# openjiuwen.core.retrieval.indexing.processor.extractor.base

## class openjiuwen.core.retrieval.indexing.processor.extractor.base.Extractor

提取器抽象基类，继承自Processor，用于提取三元组等信息。

### abstractmethod async extract

```python
extract(chunks: List[TextChunk], **kwargs: Any) -> List[Triple]
```

提取信息（例如三元组）。

**参数**：

* **chunks**(List[TextChunk])：文本分块列表（比如 `[TextChunk(...), TextChunk(...)]`）。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[Triple]**，返回提取结果列表（比如三元组列表 `[Triple(...), Triple(...)]`）。

