# openjiuwen.core.retrieval.indexing.processor.chunker.chunking

## class openjiuwen.core.retrieval.indexing.processor.chunker.chunking.TextChunker

Text chunker that supports character-based or token-based chunking, and can configure text preprocessing options.


```python
TextChunker(chunk_size: int = 512, chunk_overlap: int = 50, chunk_unit: str = "char", embed_model: Optional[Any] = None, preprocess_options: Optional[Dict] = None, **kwargs: Any)
```

Initialize text chunker.

**Parameters**:

* **chunk_size**(int): Chunk size. Default: 512.
* **chunk_overlap**(int): Chunk overlap size. Default: 50.
* **chunk_unit**(str): Chunk unit, "char" means by character, "token" means by token. Default: "char".
* **embed_model**(Any, optional): Embedding model instance, used to get tokenizer when chunk_unit="token". Default: None.
* **preprocess_options**(Dict, optional): Preprocessing options, supports normalize_whitespace (normalize whitespace characters) and remove_url_email (remove URLs and emails). Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### chunk_documents

```python
chunk_documents(documents: List[Document]) -> List[TextChunk]
```

Chunk document list.

**Parameters**:

* **documents**(List[Document]): Document list (e.g., `[Document(...), Document(...)]`).

**Returns**:

**List[TextChunk]**, returns a list of document chunks (e.g., `[TextChunk(...), TextChunk(...)]`).

