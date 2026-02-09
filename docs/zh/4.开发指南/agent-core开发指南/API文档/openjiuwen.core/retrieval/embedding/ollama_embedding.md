# openjiuwen.core.retrieval.embedding.ollama_embedding

## class openjiuwen.core.retrieval.embedding.ollama_embedding.OllamaEmbedding

Ollama嵌入模型实现。


```python
__init__(config: EmbeddingConfig, hf_tokenizer_name: Optional[str] = None, timeout: int = 60, max_retries: int = 3, extra_headers: Optional[dict] = None, max_batch_size: int = 8, dimension: Optional[int] = None)
```

初始化Ollama嵌入模型。

**参数**：

* **config**(EmbeddingConfig)：嵌入模型配置。
* **hf_tokenizer_name**(str, 可选)：HuggingFace tokenizer名称（可选）。默认值：None。
* **timeout**(int)：请求超时时间（秒）。默认值：60。
* **max_retries**(int)：最大重试次数。默认值：3。
* **extra_headers**(dict, 可选)：额外的请求头。默认值：None。
* **max_batch_size**(int)：最大批处理大小。默认值：8。
* **dimension**(int, 可选)：嵌入输出维度。默认值：None。

**异常**：

* **ValueError**：如果Ollama未运行或模型不可用。

### property dimension

```python
dimension() -> int
```

返回嵌入向量的维度。

**返回**：

**int**，返回嵌入向量的维度。

### property tokenizer

```python
tokenizer() -> Optional[AutoTokenizer]
```

获取tokenizer实例（如果已配置）。

**返回**：

**Optional[AutoTokenizer]**，返回tokenizer实例，如果未配置则返回None。

### async embed_query

```python
embed_query(text: str, **kwargs: Any) -> List[float]
```

获取文本的嵌入向量。

**参数**：

* **text**(str)：查询文本。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[float]**，返回查询文本的嵌入向量。

### async embed_documents

```python
embed_documents(texts: List[str], batch_size: Optional[int] = None, **kwargs: Any) -> List[List[float]]
```

获取文档列表的嵌入向量。

**参数**：

* **texts**(List[str])：文档文本列表。
* **batch_size**(int, 可选)：批处理大小。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[List[float]]**，返回每个文档文本的嵌入向量列表。

