# openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser.AutoFileParser

Auto file parser that uses plugin architecture to automatically select appropriate parser based on file format. Supports registering new parsers via @register_parser decorator.

**Built-in supported formats**: PDF (.pdf), text/Markdown (.txt / .md / .markdown), Word (.docx), Excel/CSV/TSV (.xlsx / .csv / .tsv), JSON (.json), images (.png / .jpg / .jpeg / .webp / .gif / .jfif). Use `AutoFileParser.get_supported_formats()` for the full list, or extend with `@register_parser`. Model servers such as vLLM and SGLang also support common image formats (e.g. ppm, bmp), but since most model services (e.g. OpenAI) do not, this parser does not auto-detect them; for such files, use [ImageParser](./image_parser.md) explicitly to parse them. Related: [AutoParser](./auto_parser.md) for a single entry (files + URLs), [AutoLinkParser](./auto_link_parser.md) for URL-only parsing.

```python
AutoFileParser(**kwargs: Any)
```

Initialize auto file parser.

**Parameters**:

* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async parse

```python
parse(doc: str, doc_id: str = "", llm_client: Optional[Model] = None, **kwargs: Any) -> List[Document]
```

Automatically select appropriate parser based on file format for parsing.

**Parameters**:

* **doc**(str): File path.
* **doc_id**(str): Document ID. Default: "".
* **llm_client**(Optional[Model], optional): LLM client for captioning or other LLM-based processing. Default: None.
* **kwargs**(Any): Variable arguments passed to the downstream parser.

**Returns**:

**List[Document]**, returns a list of documents (e.g., list).

## function register_parser

```python
register_parser(file_extensions: List[str])
```

Decorator: Register file format parser.

**Parameters**:

* **file_extensions**(List[str]): List of supported file extensions (e.g., list), e.g. [".pdf", ".PDF"].

**Returns**:

Decorator function.

**Example**:

```python
>>> from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser
>>> from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
>>> 
>>> @register_parser([".custom"])
... class CustomParser(Parser):
...     async def _parse(self, file_path: str, llm_client=None):
...         # Implement custom parsing logic; return text content or None
...         return None
```

