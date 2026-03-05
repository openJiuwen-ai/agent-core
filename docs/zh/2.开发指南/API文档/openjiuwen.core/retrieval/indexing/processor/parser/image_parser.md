# openjiuwen.core.retrieval.indexing.processor.parser.image_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.image_parser.ImageParser

本地图片文件解析器，通过 [ImageCaptioner](./captioner.md) 使用 VLM 生成图片描述（caption）。

```python
ImageParser(**kwargs: Any)
```

初始化图片解析器。

**参数**：

* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### async _parse

```python
_parse(image_path: str, llm_client: Optional[Model] = None) -> Optional[str]
```

解析图片文件并生成 caption。

**参数**：

* **image_path**(str)：图片文件路径。
* **llm_client**(Optional[Model], 可选)：用于生成图片描述的 LLM 客户端（VLM）。默认值：None。

**返回**：

**Optional[str]**，返回图片描述文本，解析失败时返回 None。

**说明**：

* 支持的文件扩展名：`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.jfif`
* 依赖 [ImageCaptioner](./captioner.md)，需传入 `llm_client` 才能生成 caption；未传入时可能返回 None 或空
* vLLM、SGLang 等模型服务还支持 ppm、bmp 等常用图片格式，但考虑到大部分模型服务（如 OpenAI）并不支持，[AutoParser](./auto_parser.md) / [AutoFileParser](./auto_file_parser.md) 不会自动识别这些类型；遇到此类文件时，需用户显式调用 ImageParser 解析。
