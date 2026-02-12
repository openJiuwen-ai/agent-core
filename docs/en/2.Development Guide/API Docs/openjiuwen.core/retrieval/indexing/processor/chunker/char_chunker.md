# openjiuwen.core.retrieval.indexing.processor.chunker.char_chunker

## class openjiuwen.core.retrieval.indexing.processor.chunker.char_chunker.CharChunker

Fixed size chunker based on character length.


```python
CharChunker(chunk_size: int = 512, chunk_overlap: int = 50, **kwargs: Any)
```

Initialize fixed size chunker.

**Parameters**:

* **chunk_size**(int): Chunk size (number of characters). Default: 512.
* **chunk_overlap**(int): Chunk overlap size (number of characters). Default: 50.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### chunk_text

```python
chunk_text(text: str) -> List[str]
```

Chunk text.

**Parameters**:

* **text**(str): Text to be chunked.

**Returns**:

**List[str]**, returns a list of chunked texts.

