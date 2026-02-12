# openjiuwen.core.retrieval.indexing.processor.parser.json_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.json_parser.JSONParser

Local file parser for JSON format.


```python
JSONParser(**kwargs: Any)
```

Initialize JSON parser.

**Parameters**:

* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async _parse

```python
_parse(file_path: str) -> Optional[str]
```

Parse JSON file.

**Parameters**:

* **file_path**(str): JSON file path.

**Returns**:

**Optional[str]**, returns formatted JSON text, or None if parsing fails.

**Description**:

* Supported file extensions: `.json`, `.JSON`
* Automatically formats JSON content with indentation and UTF-8 encoding
* Returns original content if JSON format is invalid
