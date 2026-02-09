# openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser.AutoFileParser

Auto file parser that uses plugin architecture to automatically select appropriate parser based on file format. Supports registering new parsers via @register_parser decorator.


```python
AutoFileParser(**kwargs: Any)
```

Initialize auto file parser.

**Parameters**:

* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async parse

```python
parse(doc: str, doc_id: str = "", **kwargs: Any) -> List[Document]
```

Automatically select appropriate parser based on file format for parsing.

**Parameters**:

* **doc**(str): File path.
* **doc_id**(str): Document ID. Default: "".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[Document]**, returns a list of documents.

**Exceptions**:

* **FileNotFoundError**: File does not exist.
* **ValueError**: Unsupported file format.

## function register_parser

```python
register_parser(file_extensions: List[str])
```

Decorator: Register file format parser.

**Parameters**:

* **file_extensions**(List[str]): List of supported file extensions, e.g. [".pdf", ".PDF"].

**Returns**:

Decorator function.

**Example**:

```python
>>> from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser
>>> from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
>>> 
>>> @register_parser([".custom"])
... class CustomParser(Parser):
...     async def _parse(self, file_path: str):
...         # Implement custom parsing logic
...         pass
```

