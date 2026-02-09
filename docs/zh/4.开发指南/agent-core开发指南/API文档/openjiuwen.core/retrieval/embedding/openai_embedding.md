# openjiuwen.core.retrieval.embedding.openai_embedding

## class openjiuwen.core.retrieval.embedding.openai_embedding.OpenAIEmbedding

OpenAI 嵌入模型实现，支持符合 OpenAI 标准的服务，支持 encoding_format="base64" 格式。


```python
OpenAIEmbedding(config: EmbeddingConfig, timeout: int = 60, max_retries: int = 3, extra_headers: Optional[dict] = None, max_batch_size: int = 8, dimension: Optional[int] = None, verify: bool | str | ssl.SSLContext = True, **kwargs)
```

初始化 OpenAI 嵌入模型。

**参数**：

* **config**(EmbeddingConfig)：嵌入模型配置。
* **timeout**(int, 可选)：请求超时时间（秒）。默认值：60。
* **max_retries**(int, 可选)：最大重试次数。默认值：3。
* **extra_headers**(dict, 可选)：额外的请求头。默认值：None。
* **max_batch_size**(int, 可选)：最大批处理大小。默认值：8。
* **dimension**(int, 可选)：Matryoshka 模型的嵌入维度。默认值：None。
* **verify**(bool | str | ssl.SSLContext, 可选)：SSL 验证设置。bool：是否使用默认 CA 证书；str：自定义 CA 证书路径；ssl.SSLContext：自定义 SSL 上下文。默认值：True。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### staticmethod parse_openai_response

```python
parse_openai_response(resp: CreateEmbeddingResponse) -> List[List[float]]
```

解析 OpenAI 嵌入响应对象并返回嵌入结果。

**参数**：

* **resp**(CreateEmbeddingResponse)：OpenAI 嵌入响应对象。

**返回**：

**List[List[float]]**，返回嵌入向量列表。

**异常**：

* **JiuWenBaseException**：响应无效或无法解析时抛出。

### async _get_embeddings

```python
_get_embeddings(text: str | List[str], **kwargs) -> List[List[float]]
```

获取嵌入向量（异步）。

**参数**：

* **text**(str | List[str])：文本或文本列表。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[List[float]]**，返回嵌入向量列表。

**异常**：

* **JiuWenBaseException**：请求失败或响应无效时抛出。

### _get_embeddings_sync

```python
_get_embeddings_sync(text: str | List[str], **kwargs) -> List[List[float]]
```

获取嵌入向量（同步）。

**参数**：

* **text**(str | List[str])：文本或文本列表。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[List[float]]**，返回嵌入向量列表。

**异常**：

* **JiuWenBaseException**：请求失败或响应无效时抛出。
