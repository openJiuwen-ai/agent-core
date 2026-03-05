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
_parse(file_path: str, llm_client: Optional[Model] = None) -> Optional[str]
```

解析 DOCX 文件。会提取段落、表格与内嵌图片；图片会通过 [ImageCaptioner](./captioner.md) 生成 caption，需传入 `llm_client` 以启用图片描述。

**参数**：

* **file_path**(str)：DOCX 文件路径。
* **llm_client**(Optional[Model], 可选)：用于图片 caption 的 LLM 客户端（VLM）。默认值：None。

**返回**：

**Optional[str]**，返回提取的文本内容，解析失败时返回 None。

**说明**：

* 支持的文件扩展名：`.docx`, `.DOCX`
* 使用 python-docx 库提取 DOCX 文本
* 支持提取段落和表格内容；内嵌图片可经 `llm_client` 调用 [ImageCaptioner](./captioner.md) 生成描述
