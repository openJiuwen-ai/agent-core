# openjiuwen.core.retrieval.indexing.processor.parser.auto_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.auto_parser.AutoParser

Top-level parser that routes input to the appropriate parser: URLs are handled by [AutoLinkParser](auto_link_parser.md) (WeChat article or web page by URL pattern), and local paths by [AutoFileParser](auto_file_parser.md) (by file extension). Using one knowledge base with AutoParser allows both links and local files without separate APIs.

### __init__

```python
__init__(link_parser: Parser | None = None, file_parser: Parser | None = None, **kwargs)
```

**Parameters**:

* **link_parser**(Parser, optional): Link parser; defaults to AutoLinkParser().
* **file_parser**(Parser, optional): File parser; defaults to AutoFileParser().
* **kwargs**: Additional arguments passed to the base class.

### supports

```python
supports(doc: str) -> bool
```

Returns whether the input (URL or file path) is supported.

### async parse

```python
parse(doc: str, doc_id: str = "", **kwargs) -> List[Document]
```

Parses a single input: delegates to link parser for URLs and file parser for local paths; returns a list of Documents.
