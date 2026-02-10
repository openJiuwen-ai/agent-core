# openjiuwen.core.retrieval.indexing.processor.extractor.base

## class openjiuwen.core.retrieval.indexing.processor.extractor.base.Extractor

Extractor abstract base class, inherits from Processor, used for extracting triples and other information.

### abstractmethod async extract

```python
extract(chunks: List[TextChunk], **kwargs: Any) -> List[Triple]
```

Extract information (e.g., triples).

**Parameters**:

* **chunks**(List[TextChunk]): List of text chunks (e.g., `[TextChunk(...), TextChunk(...)]`).
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[Triple]**, returns a list of extraction results (e.g., triple list `[Triple(...), Triple(...)]`).

