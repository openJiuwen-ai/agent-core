# openjiuwen.core.retrieval.indexing.processor.parser.auto_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.auto_parser.AutoParser

统一解析器（顶层路由）：输入为 URL 时委托给 [AutoLinkParser](./auto_link_parser.md)（按 URL 模式选择微信公众号或网页解析器），输入为本地路径时委托给 [AutoFileParser](./auto_file_parser.md)（按扩展名选择文件解析器）。使用一个知识库 + AutoParser 即可同时接受链接与本地文件，无需区分两套 API。

**说明**：图片格式方面，vLLM、SGLang 等模型服务还支持 ppm、bmp 等常用格式，但考虑到大部分模型服务（如 OpenAI）并不支持，本解析器不会自动识别这些类型；遇到此类文件时，需用户显式调用 [ImageParser](./image_parser.md) 解析。

```python
AutoParser(link_parser: Parser | None = None, file_parser: Parser | None = None, **kwargs)
```

**参数**：

* **link_parser**(Parser, 可选)：链接解析器，默认使用 AutoLinkParser()。默认值：None。
* **file_parser**(Parser, 可选)：文件解析器，默认使用 AutoFileParser()。默认值：None。
* **kwargs**：其他参数传递给基类。

### supports

```python
supports(doc: str) -> bool
```

判断输入（URL 或文件路径）是否被当前解析器支持。

### async parse

```python
parse(doc: str, doc_id: str = "", llm_client: Optional[Model] = None, **kwargs) -> List[Document]
```

解析单个输入：若为 URL 则走链接解析，若为本地路径则走文件解析，返回 Document 列表。

**参数**：

* **doc**(str)：文档源（URL 或文件路径）。
* **doc_id**(str)：文档 ID。默认值：""。
* **llm_client**(Optional[Model], 可选)：用于 caption 等 LLM 相关处理的客户端。默认值：None。
* **kwargs**：可变参数，传递给下游解析器。
