# openjiuwen.core.retrieval.indexing.processor.parser.pdf_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.pdf_parser.PDFParser

本地 PDF 格式文件解析器。


```python
PDFParser(**kwargs: Any)
```

初始化 PDF 解析器。

**参数**：

* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### async _parse

```python
_parse(file_path: str) -> Optional[str]
```

解析 PDF 文件。

**参数**：

* **file_path**(str)：PDF 文件路径。

**返回**：

**Optional[str]**，返回提取的文本内容，解析失败时返回 None。

**说明**：

* 支持的文件扩展名：`.pdf`, `.PDF`
* 使用 pdfplumber 库提取 PDF 文本
* 逐页提取文本内容并合并
