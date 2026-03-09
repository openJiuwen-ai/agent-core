# openjiuwen.core.retrieval.indexing.processor.parser.captioner

## class openjiuwen.core.retrieval.indexing.processor.parser.captioner.ImageCaptioner

图片描述（caption）辅助类，使用 VLM 对本地图片生成文字描述。被 [ImageParser](./image_parser.md)、[PDFParser](./pdf_parser.md)、[WordParser](./word_parser.md) 等解析器内部调用。

```python
ImageCaptioner(llm_client: Optional[Model] = None)
```

**参数**：

* **llm_client**(Optional[Model], 可选)：用于调用 VLM 生成图片描述的客户端。默认值：None。

**说明**：当前支持的模型名称前缀包括 `gpt-4o`、`gpt-5`、`qwen3-vl` 等；若为 None 或不受支持，会记录告警且 caption 可能为空。

### staticmethod cp_image

```python
cp_image(image_loc: str, target_dir: str = SAVED_IMAGE_DIR) -> str
```

将图片复制到目标目录并返回目标路径（用于后续读取）。若复制失败则回退为原始路径。

**参数**：

* **image_loc**(str)：图片本地路径。
* **target_dir**(str, 可选)：目标目录。默认值：`"images"`。

**返回**：

**str**，目标图片路径。

### async caption_images

```python
caption_images(image_locs: List[str]) -> List[str]
```

对多张图片依次调用 VLM 生成描述。

**参数**：

* **image_locs**(List[str])：图片路径列表（比如 list）。

**返回**：

**List[str]**，与输入顺序对应的描述列表；某张图片失败或未传 `llm_client` 时对应位置可能为空字符串。
