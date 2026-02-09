# openjiuwen.core.retrieval.knowledge_base

## class openjiuwen.core.retrieval.knowledge_base.KnowledgeBase

Knowledge base abstract base class, providing a unified interface for document parsing, index building, retrieval, and other operations.


```python
KnowledgeBase(config: KnowledgeBaseConfig, vector_store: Optional[VectorStore] = None, embed_model: Optional[Embedding] = None, parser: Optional[Parser] = None, chunker: Optional[Chunker] = None, extractor: Optional[Extractor] = None, index_manager: Optional[Indexer] = None, llm_client: Optional[Any] = None, strict_validation: bool = True, **kwargs: Any)
```

Initialize knowledge base.

**Parameters**:

* **config**(KnowledgeBaseConfig): Knowledge base configuration.
* **vector_store**(VectorStore, optional): Vector store instance. Default: None.
* **embed_model**(Embedding, optional): Embedding model instance. Default: None.
* **parser**(Parser, optional): Document parser instance. Default: None.
* **chunker**(Chunker, optional): Text chunker instance. Default: None.
* **extractor**(Extractor, optional): Extractor instance (for extracting triples, etc.). Default: None.
* **index_manager**(Indexer, optional): Index manager instance. Default: None.
* **llm_client**(Any, optional): LLM client instance (for graph retrieval, etc.). Default: None.
* **strict_validation**(bool, optional): Whether to enable strict validation. When enabled, validates configuration consistency between vector store and index manager. Default: True.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### abstractmethod async parse_files

```python
parse_files(file_paths: List[str], **kwargs: Any) -> List[Document]
```

Parse files from file paths and return a list of Document objects.

**Parameters**:

* **file_paths**(List[str]): List of file paths.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[Document]**, returns a list of parsed Document objects.

### abstractmethod async add_documents

```python
add_documents(documents: List[Document], **kwargs: Any) -> List[str]
```

Add documents to the knowledge base.

**Parameters**:

* **documents**(List[Document]): List of documents.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[str]**, returns a list of successfully added document IDs.

### abstractmethod async retrieve

```python
retrieve(query: str, config: Optional[RetrievalConfig] = None, **kwargs: Any) -> List[RetrievalResult]
```

Retrieve relevant documents.

**Parameters**:

* **query**(str): Query string.
* **config**(RetrievalConfig, optional): Retrieval configuration. Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[RetrievalResult]**, returns a list of retrieval results.

### abstractmethod async delete_documents

```python
delete_documents(doc_ids: List[str], **kwargs: Any) -> bool
```

Delete documents.

**Parameters**:

* **doc_ids**(List[str]): List of document IDs.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**bool**, returns True if deletion is successful, otherwise returns False.

### abstractmethod async update_documents

```python
update_documents(documents: List[Document], **kwargs: Any) -> List[str]
```

Update documents.

**Parameters**:

* **documents**(List[Document]): List of documents.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[str]**, returns a list of successfully updated document IDs.

### abstractmethod async get_statistics

```python
get_statistics() -> Dict[str, Any]
```

Get knowledge base statistics.

**Returns**:

**Dict[str, Any]**, returns a dictionary of knowledge base statistics.

### async delete_collection

```python
delete_collection(collection: str) -> None
```

Delete a collection from current database.

**Parameters**:

* **collection**(str): Name of the collection to delete.

**Exceptions**:

* **JiuWenBaseException**: Raised when vector_store is not set.

### async close

```python
close() -> None
```

Close the knowledge base and release resources.

