# openjiuwen.core.retrieval.indexing.processor.extractor.triple_extractor

## class openjiuwen.core.retrieval.indexing.processor.extractor.triple_extractor.TripleExtractor

三元组提取器实现，使用LLM进行OpenIE三元组提取。


```python
TripleExtractor(llm_client: Any, model_name: str, temperature: float = 0.0, max_concurrent: int = 50, **kwargs: Any)
```

初始化三元组提取器。

**参数**：

* **llm_client**(Any)：LLM客户端实例。
* **model_name**(str)：模型名称。
* **temperature**(float)：温度参数。默认值：0.0。
* **max_concurrent**(int)：最大并发数，默认值：50。默认值：50。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### async extract

```python
extract(chunks: List[TextChunk], **kwargs: Any) -> List[Triple]
```

提取三元组。

**参数**：

* **chunks**(List[TextChunk])：文本分块列表。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[Triple]**，返回三元组列表。

