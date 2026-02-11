# openjiuwen.core.retrieval.indexing.processor.splitter.splitter

## class openjiuwen.core.retrieval.indexing.processor.splitter.splitter.SentenceSplitter

Sentence-level text splitter, uses pysbd for sentence segmentation, then chunks based on token count.


```python
SentenceSplitter(tokenizer: Callable, chunk_size: int, chunk_overlap: int, lan: str = "auto")
```

Initialize sentence splitter.

**Parameters**:

* **tokenizer**(Callable): Tokenizer (e.g., an object with encode and decode methods), must have encode and decode methods.
* **chunk_size**(int): Chunk size (number of tokens).
* **chunk_overlap**(int): Chunk overlap size (number of tokens).
* **lan**(str, optional): Language code, defaults to "auto" (auto-detect). Default: "auto".

### __call__

```python
__call__(doc: str) -> List[Tuple[str, int, int]]
```

Split document into sentence-level chunks.

**Parameters**:

* **doc**(str): Document text to be split.

**Returns**:

**List[Tuple[str, int, int]]**, returns a list of chunks (e.g., `[("text1", 0, 10), ("text2", 10, 20)]`), each element is (text, start char position, end char position).

**Description**:

* Uses pysbd for sentence segmentation
* Chunks based on token count, ensuring each chunk does not exceed chunk_size
* Supports chunk overlap, overlap does not exceed chunk_overlap
* If a single sentence exceeds chunk_size, it will be treated as an independent chunk
