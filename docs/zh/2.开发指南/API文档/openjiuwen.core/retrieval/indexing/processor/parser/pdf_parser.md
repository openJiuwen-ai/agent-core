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
_parse(file_path: str, llm_client: Optional[Model] = None) -> Optional[str]
```

解析 PDF 文件。会提取文本与内嵌图片；图片会通过 [ImageCaptioner](./captioner.md) 生成 caption，需传入 `llm_client` 以启用图片描述。

**参数**：

* **file_path**(str)：PDF 文件路径。
* **llm_client**(Optional[Model], 可选)：用于图片 caption 的 LLM 客户端（VLM）。默认值：None。

**返回**：

**Optional[str]**，返回提取的文本内容，解析失败时返回 None。

**说明**：

* 支持的文件扩展名：`.pdf`, `.PDF`
* 使用 pdfplumber 库提取 PDF 文本
* 逐页提取文本内容并合并；页内图片可经 `llm_client` 调用 [ImageCaptioner](./captioner.md) 生成描述
