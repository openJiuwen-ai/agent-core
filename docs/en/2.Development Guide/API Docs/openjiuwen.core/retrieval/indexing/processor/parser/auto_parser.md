# openjiuwen.core.retrieval.indexing.processor.parser.auto_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.auto_parser.AutoParser

Top-level parser that routes input to the appropriate parser: URLs are handled by [AutoLinkParser](./auto_link_parser.md) (WeChat article or web page by URL pattern), and local paths by [AutoFileParser](./auto_file_parser.md) (by file extension). Using one knowledge base with AutoParser allows both links and local files without separate APIs.

**Note**: Model servers such as vLLM and SGLang also support common image formats (e.g. ppm, bmp), but since most model services (e.g. OpenAI) do not, this parser does not auto-detect them; for such files, call [ImageParser](./image_parser.md) explicitly to parse them.

```python
AutoParser(link_parser: Parser | None = None, file_parser: Parser | None = None, **kwargs)
```

**Parameters**:

* **link_parser**(Parser, optional): Link parser; defaults to AutoLinkParser(). Default: None.
* **file_parser**(Parser, optional): File parser; defaults to AutoFileParser(). Default: None.
* **kwargs**: Additional arguments passed to the base class.

### supports

```python
supports(doc: str) -> bool
```

Returns whether the input (URL or file path) is supported.

### async parse

```python
parse(doc: str, doc_id: str = "", llm_client: Optional[Model] = None, **kwargs) -> List[Document]
```

Parses a single input: delegates to link parser for URLs and file parser for local paths; returns a list of Documents.

**Parameters**:

* **doc**(str): Document source (URL or file path).
* **doc_id**(str): Document ID. Default: "".
* **llm_client**(Optional[Model], optional): LLM client for captioning or other LLM-based processing. Default: None.
* **kwargs**: Variable arguments passed to the downstream parser.
