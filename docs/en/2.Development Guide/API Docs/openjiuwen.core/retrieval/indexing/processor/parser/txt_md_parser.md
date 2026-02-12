# openjiuwen.core.retrieval.indexing.processor.parser.txt_md_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.txt_md_parser.TxtMdParser

Local file parser for TXT/MD format.


```python
TxtMdParser(**kwargs: Any)
```

Initialize TXT/MD parser.

**Parameters**:

* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async _parse

```python
_parse(file_path: str) -> Optional[str]
```

Parse TXT/MD file.

**Parameters**:

* **file_path**(str): TXT/MD file path.

**Returns**:

**Optional[str]**, returns file text content, or None if parsing fails.

**Description**:

* Supported file extensions: `.txt`, `.TXT`, `.md`, `.MD`, `.markdown`, `.MARKDOWN`
* Uses charset-normalizer to automatically detect file encoding
* Defaults to UTF-8 encoding if encoding detection fails
