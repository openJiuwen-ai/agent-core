# openjiuwen.core.retrieval.indexing.processor.parser.captioner

## class openjiuwen.core.retrieval.indexing.processor.parser.captioner.ImageCaptioner

Helper class for image captioning; uses a VLM to generate text descriptions for local images. Used internally by [ImageParser](./image_parser.md), [PDFParser](./pdf_parser.md), [WordParser](./word_parser.md), and similar parsers.

```python
ImageCaptioner(llm_client: Optional[Model] = None)
```

**Parameters**:

* **llm_client**(Optional[Model], optional): Client used to invoke the VLM for image captions. Default: None.

**Description**: Supported model name prefixes include `gpt-4o`, `gpt-5`, `qwen3-vl`; if None or unsupported, a warning is logged and captions may be empty.

### staticmethod cp_image

```python
cp_image(image_loc: str, target_dir: str = SAVED_IMAGE_DIR) -> str
```

Copy image to target directory and return the destination path (for later reading). Falls back to the original path if copy fails.

**Parameters**:

* **image_loc**(str): Local image path.
* **target_dir**(str, optional): Target directory. Default: `"images"`.

**Returns**:

**str**, destination image path.

### async caption_images

```python
caption_images(image_locs: List[str]) -> List[str]
```

Generate captions for multiple images via the VLM in order.

**Parameters**:

* **image_locs**(List[str]): List of image paths (e.g., list).

**Returns**:

**List[str]**, list of captions in the same order as input; may contain empty strings when a caption fails or `llm_client` was not provided.
