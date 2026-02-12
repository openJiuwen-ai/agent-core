# openjiuwen.core.retrieval.indexing.processor.base

## class openjiuwen.core.retrieval.indexing.processor.base.Processor

Processor abstract base class, base class for all processors (Parser, Chunker, Extractor).

### abstractmethod async process

```python
process(*args: Any, **kwargs: Any) -> Any
```

Process data (abstract method, must be implemented by subclasses).

**Parameters**:

* **args**(Any): Positional arguments.
* **kwargs**(Any): Keyword arguments.

**Returns**:

**Any**, returns processing result.

