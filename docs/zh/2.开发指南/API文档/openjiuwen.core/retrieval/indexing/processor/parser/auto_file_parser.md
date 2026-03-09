# openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser.AutoFileParser

自动文件解析器，使用插件架构，根据文件格式自动选择合适的解析器。支持通过@register_parser装饰器注册新的解析器。

**当前内置支持格式**：PDF（.pdf）、文本/Markdown（.txt / .md / .markdown）、Word（.docx）、Excel/CSV/TSV（.xlsx / .csv / .tsv）、JSON（.json）、图片（.png / .jpg / .jpeg / .webp / .gif / .jfif）。可通过 `AutoFileParser.get_supported_formats()` 获取完整列表，或使用 `@register_parser` 扩展新格式。vLLM、SGLang 等模型服务还支持 ppm、bmp 等常用图片格式，但考虑到大部分模型服务（如 OpenAI）并不支持，本解析器不会自动识别这些类型；遇到此类文件时，需用户显式使用 [ImageParser](./image_parser.md) 解析。相关：统一入口（文件+链接）见 [AutoParser](./auto_parser.md)，仅链接解析见 [AutoLinkParser](./auto_link_parser.md)。

```python
AutoFileParser(**kwargs: Any)
```

初始化自动文件解析器。

**参数**：

* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### async parse

```python
parse(doc: str, doc_id: str = "", llm_client: Optional[Model] = None, **kwargs: Any) -> List[Document]
```

根据文件格式自动选择合适的解析器进行解析。

**参数**：

* **doc**(str)：文件路径。
* **doc_id**(str)：文档 ID。默认值：""。
* **llm_client**(Optional[Model], 可选)：用于 caption 等 LLM 相关处理的客户端。默认值：None。
* **kwargs**(Any)：可变参数，传递给下游解析器。

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

**示例**：

```python
>>> from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser
>>> from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
>>> 
>>> @register_parser([".custom"])
... class CustomParser(Parser):
...     async def _parse(self, file_path: str, llm_client=None):
...         # 实现自定义解析逻辑，返回文本内容或 None
...         return None
```

