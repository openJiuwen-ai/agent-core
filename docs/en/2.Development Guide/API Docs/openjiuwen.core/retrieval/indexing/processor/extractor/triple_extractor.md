# openjiuwen.core.retrieval.indexing.processor.extractor.triple_extractor

## class openjiuwen.core.retrieval.indexing.processor.extractor.triple_extractor.TripleExtractor

Triple extractor implementation using an LLM for OpenIE triple extraction, with optional post-extraction validation via another LLM pass when validate is true.


```python
TripleExtractor(llm_client: Any, model_name: str, temperature: float = 0.0, max_concurrent: int = 50, validate: bool = False, **kwargs: Any)
```

Initialize triple extractor.

**Parameters**:

* **llm_client**(Any): LLM client instance.
* **model_name**(str): Model name.
* **temperature**(float): Temperature parameter. Default: 0.0.
* **max_concurrent**(int): Maximum concurrency. Default: 50.
* **validate**(bool): If true, after extraction the extractor runs an additional LLM step to validate triples against their source chunks and returns only triples that pass validation. Default: false (no validation pass).
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async extract

```python
extract(chunks: List[TextChunk], **kwargs: Any) -> List[Triple]
```

Extract triples from chunks (parallel LLM calls). When validate was set to true on the extractor, results are filtered by a follow-up validation step per chunk.

**Parameters**:

* **chunks**(List[TextChunk]): List of text chunks (e.g., `[TextChunk(...), TextChunk(...)]`).
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[Triple]**, returns a list of triples (e.g., `[Triple(...), Triple(...)]`).

