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
_parse(file_path: str) -> Optional[str]
```

Parse DOCX file.

**Parameters**:

* **file_path**(str): DOCX file path.

**Returns**:

**Optional[str]**, returns extracted text content, or None if parsing fails.

**Description**:

* Supported file extensions: `.docx`, `.DOCX`
* Uses python-docx library to extract DOCX text
* Supports extracting paragraph and table content
