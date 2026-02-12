# openjiuwen.core.retrieval.indexing.processor.parser.word_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.word_parser.WordParser

本地 DOCX 格式文件解析器。


```python
WordParser(**kwargs: Any)
```

初始化 Word 解析器。

**参数**：

* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### async _parse

```python
_parse(file_path: str) -> Optional[str]
```

解析 DOCX 文件。

**参数**：

* **file_path**(str)：DOCX 文件路径。

**返回**：

**Optional[str]**，返回提取的文本内容，解析失败时返回 None。

**说明**：

* 支持的文件扩展名：`.docx`, `.DOCX`
* 使用 python-docx 库提取 DOCX 文本
* 支持提取段落和表格内容
