# openjiuwen.core.retrieval.embedding.base

## class openjiuwen.core.retrieval.embedding.base.Embedding

嵌入模型抽象基类，提供统一的接口用于文本嵌入。

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/retrieval/` 目录下的示例代码，包括：
> - `showcase_text_embedding.py` - 文本嵌入示例
> - `showcase_multimodal_embedding.py` - 多模态嵌入示例

### abstractmethod async embed_query

```python
embed_query(text: str, **kwargs: Any) -> List[float]
```

获取文本的嵌入向量。

**参数**：

* **text**(str)：查询文本。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[float]**，返回查询文本的嵌入向量。

### abstractmethod async embed_documents

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

### property dimension

```python
dimension -> int
```

返回嵌入向量的维度。

**返回**：

**int**，返回嵌入向量的维度。

