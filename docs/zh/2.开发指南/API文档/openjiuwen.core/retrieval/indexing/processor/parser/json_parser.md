# openjiuwen.core.retrieval.indexing.processor.parser.json_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.json_parser.JSONParser

本地 JSON 格式文件解析器。


```python
JSONParser(**kwargs: Any)
```

初始化 JSON 解析器。

**参数**：

* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### async _parse

```python
_parse(file_path: str) -> Optional[str]
```

解析 JSON 文件。

**参数**：

* **file_path**(str)：JSON 文件路径。

**返回**：

**Optional[str]**，返回格式化后的 JSON 文本，解析失败时返回 None。

**说明**：

* 支持的文件扩展名：`.json`, `.JSON`
* 自动格式化 JSON 内容，使用缩进和 UTF-8 编码
* 如果 JSON 格式错误，返回原始内容
