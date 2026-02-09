# openjiuwen.core.retrieval.indexing.processor.parser.base

## class openjiuwen.core.retrieval.indexing.processor.parser.base.Parser

Document parser abstract base class, inherits from Processor, provides document parsing interface.

### async parse

```python
parse(doc: str, doc_id: str = "", **kwargs: Any) -> List[Document]
```

Parse document.

**Parameters**:

* **doc**(str): Document source (file path, URL, etc.).
* **doc_id**(str): Document ID. Default: "".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[Document]**, returns a list of documents.

### async lazy_parse

```python
lazy_parse(doc: str, doc_id: str = "", **kwargs: Any) -> AsyncIterator[Document]
```

Lazy loading parse document (default implementation based on parse method).

**Parameters**:

* **doc**(str): Document source (file path, URL, etc.).
* **doc_id**(str): Document ID. Default: "".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**AsyncIterator[Document]**, returns an async iterator of documents.

### supports

```python
supports(doc: str) -> bool
```

Check if the document source is supported.

**Parameters**:

* **doc**(str): Document source.

**Returns**:

**bool**, returns whether it is supported.

