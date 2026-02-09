# openjiuwen.core.retrieval.embedding.openai_embedding

## class openjiuwen.core.retrieval.embedding.openai_embedding.OpenAIEmbedding

OpenAI embedding model implementation, supports services following OpenAI standard, supports encoding_format="base64".


```python
OpenAIEmbedding(config: EmbeddingConfig, timeout: int = 60, max_retries: int = 3, extra_headers: Optional[dict] = None, max_batch_size: int = 8, dimension: Optional[int] = None, verify: bool | str | ssl.SSLContext = True, **kwargs)
```

Initialize OpenAI embedding model.

**Parameters**:

* **config**(EmbeddingConfig): Embedding model configuration.
* **timeout**(int, optional): Request timeout (seconds). Default: 60.
* **max_retries**(int, optional): Maximum number of retries. Default: 3.
* **extra_headers**(dict, optional): Additional request headers. Default: None.
* **max_batch_size**(int, optional): Maximum batch size. Default: 8.
* **dimension**(int, optional): Embedding dimension for Matryoshka models. Default: None.
* **verify**(bool | str | ssl.SSLContext, optional): SSL verification settings. bool: whether to use default CA certificate; str: path to custom CA certificate; ssl.SSLContext: custom SSL context. Default: True.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### staticmethod parse_openai_response

```python
parse_openai_response(resp: CreateEmbeddingResponse) -> List[List[float]]
```

Parse OpenAI embedding response object and return embedding result.

**Parameters**:

* **resp**(CreateEmbeddingResponse): OpenAI embedding response object.

**Returns**:

**List[List[float]]**, returns a list of embedding vectors.

**Exceptions**:

* **JiuWenBaseException**: Raised when response is invalid or cannot be parsed.

### async _get_embeddings

```python
_get_embeddings(text: str | List[str], **kwargs) -> List[List[float]]
```

Get embedding vectors (async).

**Parameters**:

* **text**(str | List[str]): Text or list of texts.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[List[float]]**, returns a list of embedding vectors.

**Exceptions**:

* **JiuWenBaseException**: Raised when request fails or response is invalid.

### _get_embeddings_sync

```python
_get_embeddings_sync(text: str | List[str], **kwargs) -> List[List[float]]
```

Get embedding vectors (sync).

**Parameters**:

* **text**(str | List[str]): Text or list of texts.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[List[float]]**, returns a list of embedding vectors.

**Exceptions**:

* **JiuWenBaseException**: Raised when request fails or response is invalid.
