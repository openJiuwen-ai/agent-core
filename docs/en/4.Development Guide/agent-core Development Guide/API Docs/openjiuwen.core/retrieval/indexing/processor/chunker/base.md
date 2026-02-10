# openjiuwen.core.retrieval.indexing.processor.chunker.base

## class openjiuwen.core.retrieval.indexing.processor.chunker.base.Chunker

Text chunker abstract base class, inherits from Processor, provides text chunking interface.


```python
Chunker(chunk_size: int = 512, chunk_overlap: int = 50, length_function: Optional[Callable[[str], int]] = None, **kwargs: Any)
```

Initialize text chunker.

**Parameters**:

* **chunk_size**(int): Chunk size. Default: 512.
* **chunk_overlap**(int): Chunk overlap size. Default: 50.
* **length_function**(Callable[[str], int], optional): Length calculation function (e.g., `len` function, default uses character count). Default: None.
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

### chunk_documents

```python
chunk_documents(documents: List[Document]) -> List[TextChunk]
```

Chunk document list.

**Parameters**:

* **documents**(List[Document]): Document list.

**Returns**:

**List[TextChunk]**, returns a list of document chunks.

