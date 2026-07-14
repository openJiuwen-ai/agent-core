# openjiuwen.core.retrieval.indexing.processor.extractor.ontology_triple_extractor

## class openjiuwen.core.retrieval.indexing.processor.extractor.ontology_triple_extractor.OntologyTripleExtractor

Ontology based triple extractor implementation using LLM.


```python
OntologyTripleExtractor(llm_client: Any, model_name: str, ontology_name: str, ontology_path: str | None = None, constrain_ontology: bool = False, temperature: float = 0.0, max_concurrent: int = 50, **kwargs: Any)
```

Initialize triple extractor.

**Parameters**:

* **llm_client**(Any): LLM client instance.
* **model_name**(str): Model name.

* **ontology_name**(str): Name of the ontology.
* **ontology_path**(str): Path to the file where the ontology is stored. It can be either a [.nt file](https://www.w3.org/TR/rdf12-n-triples/) or a [.ttl file](https://www.w3.org/TR/turtle/)
* **constrain_ontology**(bool): Whether to enforce ontology rules for extracted triples.

* **temperature**(float): Temperature parameter. Default: 0.0.
* **max_concurrent**(int): Maximum concurrency. Default: 50.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async extract

```python
extract(chunks: List[TextChunk], **kwargs: Any) -> List[Triple]
```

Extract triples.

**Parameters**:

* **chunks**(List[TextChunk]): List of text chunks (e.g., `[TextChunk(...), TextChunk(...)]`).
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[Triple]**, returns a list of triples (e.g., `[Triple(...), Triple(...)]`).

