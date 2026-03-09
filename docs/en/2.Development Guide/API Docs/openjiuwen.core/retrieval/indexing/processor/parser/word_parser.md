# openjiuwen.core.retrieval.indexing.processor.parser.word_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.word_parser.WordParser

Local file parser for DOCX format.


```python
WordParser(**kwargs: Any)
```

Initialize Word parser.

**Parameters**:

* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async _parse

```python
_parse(file_path: str, llm_client: Optional[Model] = None) -> Optional[str]
```

Parse DOCX file. Extracts paragraphs, tables, and embedded images; images are captioned via [ImageCaptioner](./captioner.md). Pass `llm_client` to enable image captioning.

**Parameters**:

* **file_path**(str): DOCX file path.
* **llm_client**(Optional[Model], optional): LLM client (VLM) for image captioning. Default: None.

**Returns**:

**Optional[str]**, returns extracted text content, or None if parsing fails.

**Description**:

* Supported file extensions: `.docx`, `.DOCX`
* Uses python-docx library to extract DOCX text
* Output is markdown-like: plain paragraphs are emitted as-is; paragraphs with style "Title" or "Heading 1" through "Heading 9" are converted to markdown headings (`#` / `##` / …); tables are emitted as markdown tables (with header separator row)
* Embedded images can be captioned via [ImageCaptioner](./captioner.md) when `llm_client` is provided
