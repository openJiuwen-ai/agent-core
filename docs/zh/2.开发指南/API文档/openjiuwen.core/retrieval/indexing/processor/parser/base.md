# openjiuwen.core.retrieval.indexing.processor.parser.base

## class openjiuwen.core.retrieval.indexing.processor.parser.base.Parser

文档解析器抽象基类，继承自Processor，提供文档解析接口。

### async parse

```python
parse(doc: str, doc_id: str = "", llm_client: Optional[Model] = None, **kwargs: Any) -> List[Document]
```

解析文档。

**参数**：

* **doc**(str)：文档源（文件路径、URL等）。
* **doc_id**(str)：文档ID。默认值：""。
* **llm_client**(Optional[Model], 可选)：用于 caption 等 LLM 相关处理的客户端。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[Document]**，返回文档列表（比如 list）。

### async lazy_parse

```python
lazy_parse(doc: str, doc_id: str = "", **kwargs: Any) -> AsyncIterator[Document]
```

懒加载解析文档（默认实现基于parse方法）。

**参数**：

* **doc**(str)：文档源（文件路径、URL等）。
* **doc_id**(str)：文档ID。默认值：""。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**AsyncIterator[Document]**，返回文档的异步迭代器（比如 async generator）。

### supports

```python
supports(doc: str) -> bool
```

检查是否支持该文档源。

**参数**：

* **doc**(str)：文档源。

**返回**：

**bool**，返回是否支持。

