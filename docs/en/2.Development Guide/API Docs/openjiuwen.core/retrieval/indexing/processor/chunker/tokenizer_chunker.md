# openjiuwen.core.retrieval.indexing.processor.chunker.tokenizer_chunker

## class openjiuwen.core.retrieval.indexing.processor.chunker.tokenizer_chunker.TokenizerChunker

Fixed size chunker based on tokenizer.


```python
TokenizerChunker(chunk_size: int, chunk_overlap: int, tokenizer: Any, language: str = "auto", splitter_config: dict | None = None, **kwargs)
```

Initialize tokenizer-based chunker.

**Parameters**:

* **chunk_size**(int): Chunk size (number of tokens).
* **chunk_overlap**(int): Chunk overlap size (number of tokens).
* **tokenizer**(Any): Tokenizer, must have encode and decode methods.
* **language**(str, optional): Language code, defaults to "auto" (auto-detect). Default: "auto".
* **splitter_config**(dict, optional): Other arguments to SentenceSplitter. Default: None.
* **kwargs**: Variable arguments for passing additional configuration parameters.

### chunk_text

```python
chunk_text(text: str) -> List[str]
```

Chunk text.

**Parameters**:

* **text**(str): Text to be chunked.

**Returns**:

**List[str]**, returns a list of chunked texts.

