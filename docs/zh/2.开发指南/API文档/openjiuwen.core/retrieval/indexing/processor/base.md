# openjiuwen.core.retrieval.indexing.processor.base

## class openjiuwen.core.retrieval.indexing.processor.base.Processor

处理器抽象基类，所有处理器（Parser、Chunker、Extractor）的基类。

### abstractmethod async process

```python
process(*args: Any, **kwargs: Any) -> Any
```

处理数据（抽象方法，必须由子类实现）。

**参数**：

* **args**(Any)：位置参数。
* **kwargs**(Any)：关键字参数。

**返回**：

**Any**，返回处理结果。

