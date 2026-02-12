# openjiuwen.core.retrieval.indexing.processor.chunker.tokenizer_chunker

## class openjiuwen.core.retrieval.indexing.processor.chunker.tokenizer_chunker.TokenizerChunker

基于tokenizer的固定大小分块器。


```python
TokenizerChunker(chunk_size: int, chunk_overlap: int, tokenizer: Any, language: str = "auto", splitter_config: dict | None = None, **kwargs)
```

初始化基于tokenizer的分块器。

**参数**：

* **chunk_size**(int)：分块大小（token数）。
* **chunk_overlap**(int)：分块重叠大小（token数）。
* **tokenizer**(Any)：分词器，必须具有encode和decode方法。
* **language**(str, 可选)：语言代码，默认为 "auto"（自动检测）。默认值："auto"。
* **splitter_config**(dict, 可选)：传递给 SentenceSplitter 的其他参数。默认值：None。
* **kwargs**：可变参数，用于传递其他额外的配置参数。

### chunk_text

```python
chunk_text(text: str) -> List[str]
```

分块文本。

**参数**：

* **text**(str)：待分块的文本。

**返回**：

**List[str]**，返回分块后的文本列表。

