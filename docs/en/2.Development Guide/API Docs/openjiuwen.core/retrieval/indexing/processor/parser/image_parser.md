# openjiuwen.core.retrieval.indexing.processor.parser.image_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.image_parser.ImageParser

Local image file parser; uses [ImageCaptioner](./captioner.md) with a VLM to generate image captions.

```python
ImageParser(**kwargs: Any)
```

Initialize image parser.

**Parameters**:

* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async _parse

```python
_parse(image_path: str, llm_client: Optional[Model] = None) -> Optional[str]
```

Parse image file and generate caption.

**Parameters**:

* **image_path**(str): Image file path.
* **llm_client**(Optional[Model], optional): LLM client (VLM) for generating image captions. Default: None.

**Returns**:

**Optional[str]**, returns caption text, or None if parsing fails.

**Description**:

* Supported file extensions: `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.jfif`
* Depends on [ImageCaptioner](./captioner.md); pass `llm_client` to generate captions; may return None or empty when not provided
* vLLM and SGLang and similar model servers also support common image formats such as ppm and bmp, but since most model services (e.g. OpenAI) do not, [AutoParser](./auto_parser.md) / [AutoFileParser](./auto_file_parser.md) do not auto-detect these extensions; for such files, use ImageParser explicitly to parse them.
