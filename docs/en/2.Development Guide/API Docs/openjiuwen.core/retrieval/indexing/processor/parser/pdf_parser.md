# openjiuwen.core.retrieval.indexing.processor.parser.pdf_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.pdf_parser.PDFParser

Local file parser for PDF format.


```python
PDFParser(**kwargs: Any)
```

Initialize PDF parser.

**Parameters**:

* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async _parse

```python
_parse(file_path: str, llm_client: Optional[Model] = None) -> Optional[str]
```

Parse PDF file. Extracts text and embedded images; images are captioned via [ImageCaptioner](./captioner.md). Pass `llm_client` to enable image captioning.

**Parameters**:

* **file_path**(str): PDF file path.
* **llm_client**(Optional[Model], optional): LLM client (VLM) for image captioning. Default: None.

**Returns**:

**Optional[str]**, returns extracted text content, or None if parsing fails.

**Description**:

* Supported file extensions: `.pdf`, `.PDF`
* Uses pdfplumber library to extract PDF text
* Extracts text page by page and merges the content; embedded images can be captioned via [ImageCaptioner](./captioner.md) when `llm_client` is provided
