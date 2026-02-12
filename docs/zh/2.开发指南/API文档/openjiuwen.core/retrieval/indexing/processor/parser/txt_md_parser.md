# openjiuwen.core.retrieval.indexing.processor.parser.txt_md_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.txt_md_parser.TxtMdParser

本地 TXT/MD 格式文件解析器。


```python
TxtMdParser(**kwargs: Any)
```

初始化 TXT/MD 解析器。

**参数**：

* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### async _parse

```python
_parse(file_path: str) -> Optional[str]
```

解析 TXT/MD 文件。

**参数**：

* **file_path**(str)：TXT/MD 文件路径。

**返回**：

**Optional[str]**，返回文件文本内容，解析失败时返回 None。

**说明**：

* 支持的文件扩展名：`.txt`, `.TXT`, `.md`, `.MD`, `.markdown`, `.MARKDOWN`
* 使用 charset-normalizer 自动检测文件编码
* 如果编码检测失败，默认使用 UTF-8 编码
