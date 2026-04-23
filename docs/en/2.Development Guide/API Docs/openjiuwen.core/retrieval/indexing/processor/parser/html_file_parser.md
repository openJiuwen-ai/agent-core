# openjiuwen.core.retrieval.indexing.processor.parser.html_file_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.html_file_parser.HTMLFileParser

Local file parser for HTML (`.html`, `.htm`). Reads the file as text, parses markup with BeautifulSoup, and extracts a single main-content `Document` with title metadata. Content heuristics are similar in spirit to [WebPageParser](./web_page_parser.md) (article/main/common content selectors), but this class operates on **local paths**, not HTTP URLs.

Subclasses [TxtMdParser](./txt_md_parser.md) so HTML files benefit from the same charset detection and async file reading pattern as plain text.

```python
HTMLFileParser(**kwargs: Any)
```

Initialize the HTML file parser.

**Parameters**:

* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async parse

```python
parse(doc: str, doc_id: str = "", **kwargs: Any) -> List[Document]
```

Parse a local HTML file into a list of `Document` instances (typically one).

**Parameters**:

* **doc**(str): Path to an `.html` or `.htm` file.
* **doc_id**(str, optional): Optional document ID for the produced `Document`. If empty, the implementation may fall back to `doc` or another stable id. Default: "".
* **kwargs**(Any): Optional arguments (e.g. `timeout`, `user_agent`) reserved for alignment with other parsers; local file parsing does not perform HTTP fetches.

**Returns**:

**List[Document]**, usually one item whose `text` is normalized plain text from the detected main content region.

**Raises**:

* Errors built with `StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR` when no suitable main-content node is found or when extracted text is empty or shorter than the minimum length threshold after parsing.

**Description**:

* Registered extensions: `.htm`, `.HTM`, `.html`, `.HTML` (see `@register_parser` in the module).
* Parsing backend: prefers `lxml` if available, otherwise Python’s built-in `html.parser`.
* **Title** metadata: `og:title` (`<meta property="og:title" content="...">`) if present, otherwise the `<title>` element; if still missing, a placeholder title may be used.
* **Main content**: tries, in order, selectors such as `article`, `main`, `[role="main"]`, and several common article/content class names; if none yield enough text, falls back to larger `div`/`section` regions or `body`.
* **Text extraction**: removes `script` and `style`, collapses horizontal whitespace, and normalizes blank lines before emitting `Document` text.
* **Metadata** on the document includes at least `title` and `source_type` (`"web_page"`).

### async _parse_html (classmethod)

```python
_parse_html(
    html: str,
    doc_id: str = "",
    source: Optional[str] = None,
) -> List[Document]
```

Parse an HTML **string** (already read from disk or elsewhere) into `Document` objects using the same title and main-content logic as `parse`. Intended for internal or advanced use when the raw HTML is already in memory.

**Parameters**:

* **html**(str): Full HTML document as a string.
* **doc_id**(str, optional): Document ID for the result. Default: "".
* **source**(str, optional): Descriptive source string (e.g. file path) for error messages. Default: None.

**Returns**:

**List[Document]**, typically one document.

**Raises**:

* Same as `parse` when main content cannot be determined or text is too short.
