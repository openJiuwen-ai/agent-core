# openjiuwen.core.retrieval.indexing.processor.extractor.triple_extractor

## class openjiuwen.core.retrieval.indexing.processor.extractor.triple_extractor.TripleExtractor

Triple extractor implementation using LLM for OpenIE triple extraction.


```python
TripleExtractor(llm_client: Any, model_name: str, temperature: float = 0.0, max_concurrent: int = 50, **kwargs: Any)
```

Initialize triple extractor.

**Parameters**:

* **llm_client**(Any): LLM client instance.
* **model_name**(str): Model name.
* **temperature**(float): Temperature parameter. Default: 0.0.
* **max_concurrent**(int): Maximum concurrency. Default: 50.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async extract

```python
extract(chunks: List[TextChunk], **kwargs: Any) -> List[Triple]
```

Extract triples.

**Parameters**:

* **chunks**(List[TextChunk]): List of text chunks.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[Triple]**, returns a list of triples.

