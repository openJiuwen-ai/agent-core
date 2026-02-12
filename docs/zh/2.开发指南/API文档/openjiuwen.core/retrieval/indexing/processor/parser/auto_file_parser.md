# openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser.AutoFileParser

自动文件解析器，使用插件架构，根据文件格式自动选择合适的解析器。支持通过@register_parser装饰器注册新的解析器。


```python
AutoFileParser(**kwargs: Any)
```

初始化自动文件解析器。

**参数**：

* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### async parse

```python
parse(doc: str, doc_id: str = "", **kwargs: Any) -> List[Document]
```

根据文件格式自动选择合适的解析器进行解析。

**参数**：

* **doc**(str)：文件路径。
* **doc_id**(str)：文档ID。默认值：""。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[Document]**，返回文档列表（比如 list）。

## function register_parser

```python
register_parser(file_extensions: List[str])
```

装饰器：注册文件格式解析器。

**参数**：

* **file_extensions**(List[str])：支持的文件扩展名列表（比如 list），例如[".pdf", ".PDF"]。

**返回**：

装饰器函数。

**样例**：

```python
>>> from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser
>>> from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
>>> 
>>> @register_parser([".custom"])
... class CustomParser(Parser):
...     async def _parse(self, file_path: str):
...         # 实现自定义解析逻辑
...         pass
```

