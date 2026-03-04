# openjiuwen.core.retrieval.indexing.processor.parser.auto_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.auto_parser.AutoParser

统一解析器（顶层路由）：输入为 URL 时委托给 [AutoLinkParser](auto_link_parser.md)（按 URL 模式选择微信公众号或网页解析器），输入为本地路径时委托给 [AutoFileParser](auto_file_parser.md)（按扩展名选择文件解析器）。使用一个知识库 + AutoParser 即可同时接受链接与本地文件，无需区分两套 API。

### __init__

```python
__init__(link_parser: Parser | None = None, file_parser: Parser | None = None, **kwargs)
```

**参数**：

* **link_parser**(Parser, 可选)：链接解析器，默认使用 AutoLinkParser()。
* **file_parser**(Parser, 可选)：文件解析器，默认使用 AutoFileParser()。
* **kwargs**：其他参数传递给基类。

### supports

```python
supports(doc: str) -> bool
```

判断输入（URL 或文件路径）是否被当前解析器支持。

### async parse

```python
parse(doc: str, doc_id: str = "", **kwargs) -> List[Document]
```

解析单个输入：若为 URL 则走链接解析，若为本地路径则走文件解析，返回 Document 列表。
