# openjiuwen.core.retrieval.graph_knowledge_base

## class openjiuwen.core.retrieval.graph_knowledge_base.GraphKnowledgeBase

Graph-enhanced knowledge base implementation that supports graph indexing and retrieval. Can extract triples from documents and build graph indexes, supporting graph-based retrieval expansion.


```python
GraphKnowledgeBase(config: KnowledgeBaseConfig, vector_store: Optional[VectorStore] = None, embed_model: Optional[Embedding] = None, parser: Optional[Parser] = None, chunker: Optional[Chunker] = None, extractor: Optional[Extractor] = None, index_manager: Optional[Indexer] = None, chunk_retriever: Optional[Retriever] = None, triple_retriever: Optional[Retriever] = None, llm_client: Optional[BaseModelClient] = None, **kwargs: Any)
```

Initialize graph knowledge base.

**Parameters**:

* **config**(KnowledgeBaseConfig): Knowledge base configuration, use_graph=True enables graph indexing.
* **vector_store**(VectorStore, optional): Vector store instance. Default: None.
* **embed_model**(Embedding, optional): Embedding model instance. Default: None.
* **parser**(Parser, optional): Document parser instance. Default: None.
* **chunker**(Chunker, optional): Text chunker instance. Default: None.
* **extractor**(Extractor, optional): Triple extractor instance (required when graph indexing is enabled). Default: None.
* **index_manager**(Indexer, optional): Index manager instance. Default: None.
* **chunk_retriever**(Retriever, optional): Document chunk retriever instance. Default: None.
* **triple_retriever**(Retriever, optional): Triple retriever instance. Default: None.
* **llm_client**(BaseModelClient, optional): LLM client instance (for triple extraction and agentic retrieval). Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async parse_files

```python
parse_files(file_paths: List[str], **kwargs: Any) -> List[Document]
```

Parse files from file paths and return a list of Document objects.

**Parameters**:

* **file_paths**(List[str]): List of file paths.
* **kwargs**(Any): Variable arguments that may include file_name and file_id parameters.

**Returns**:

**List[Document]**, returns a list of parsed Document objects.

### async add_documents

```python
add_documents(documents: List[Document], **kwargs: Any) -> List[str]
```

Add documents to the knowledge base, including document chunk indexing and triple indexing (if graph indexing is enabled).

**Parameters**:

* **documents**(List[Document]): List of documents.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[str]**, returns a list of successfully added document IDs.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.graph_knowledge_base import GraphKnowledgeBase
>>> from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig
>>> from openjiuwen.core.retrieval.common.document import Document
>>> 
>>> async def run():
...     config = KnowledgeBaseConfig(kb_id="graph_kb", index_type="hybrid", use_graph=True)
...     # Need to configure chunker, extractor, index_manager, and embed_model
...     kb = GraphKnowledgeBase(config=config)
...     documents = [Document(text="Beijing is the capital of China", id_="doc1")]
...     doc_ids = await kb.add_documents(documents)
...     print(f"Added {len(doc_ids)} documents with graph index")
>>> asyncio.run(run())
Added 1 documents with graph index
```

### async retrieve

```python
retrieve(query: str, config: Optional[RetrievalConfig] = None, **kwargs: Any) -> List[RetrievalResult]
```

Retrieve relevant documents, supporting graph retrieval and agentic retrieval. If config.use_graph is True, graph retriever will be used for retrieval, supporting graph expansion and multi-hop retrieval. If config.agentic is True, an `AgenticRetriever` wraps the underlying retriever to perform iterative query rewriting and result fusion.

**Parameters**:

* **query**(str): Query string.
* **config**(RetrievalConfig, optional): Retrieval configuration, use_graph=True enables graph retrieval, graph_expansion=True enables graph expansion, agentic=True enables agentic retrieval. Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[RetrievalResult]**, returns a list of retrieval results. Graph retrieval will expand through triple relationships.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.graph_knowledge_base import GraphKnowledgeBase
>>> from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig, RetrievalConfig
>>> 
>>> async def run():
...     config = KnowledgeBaseConfig(kb_id="graph_kb", index_type="hybrid", use_graph=True)
...     # Need to configure vector_store, embed_model, index_manager, etc.
...     kb = GraphKnowledgeBase(config=config)
...     retrieval_config = RetrievalConfig(top_k=5, use_graph=True, graph_expansion=True)
...     results = await kb.retrieve("capital of China", config=retrieval_config)
...     print(f"Retrieved {len(results)} results with graph expansion")
>>> asyncio.run(run())
Retrieved 5 results with graph expansion
```

### async delete_documents

```python
delete_documents(doc_ids: List[str], **kwargs: Any) -> bool
```

Delete documents, including document chunk indexes and triple indexes (if they exist).

**Parameters**:

* **doc_ids**(List[str]): List of document IDs.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**bool**, returns True if all documents are deleted successfully, otherwise returns False.

### async update_documents

```python
update_documents(documents: List[Document], **kwargs: Any) -> List[str]
```

Update documents, including re-chunking, re-extracting triples, and updating indexes.

**Parameters**:

* **documents**(List[Document]): List of documents.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[str]**, returns a list of successfully updated document IDs.

### async get_statistics

```python
get_statistics() -> Dict[str, Any]
```

Get knowledge base statistics, including document chunk index and triple index information.

**Returns**:

**Dict[str, Any]**, returns a dictionary containing knowledge base ID, index type, whether graph indexing is enabled, document chunk index information, triple index information, and other statistics.

### async close

```python
close() -> None
```

Close the knowledge base and release resources, including graph retriever, document chunk retriever, and triple retriever.

## func async retrieve_multi_graph_kb

```python
retrieve_multi_graph_kb(kbs: List[KnowledgeBase], query: str, config: Optional[RetrievalConfig] = None, top_k: Optional[int] = None) -> List[str]
```

Perform retrieval on multiple knowledge bases (returns text list).

**Parameters**:

* **kbs**(List[KnowledgeBase]): List of knowledge bases.
* **query**(str): Query string.
* **config**(RetrievalConfig, optional): Retrieval configuration. Default: None.
* **top_k**(int, optional): Number of results to return. Default: None.

**Returns**:

**List[str]**, returns a deduplicated and sorted list of texts.

## func async retrieve_multi_graph_kb_with_source

```python
retrieve_multi_graph_kb_with_source(kbs: List[KnowledgeBase], query: str, config: Optional[RetrievalConfig] = None, top_k: Optional[int] = None) -> List[Dict[str, Any]]
```

Perform retrieval on multiple knowledge bases (with source information).

**Parameters**:

* **kbs**(List[KnowledgeBase]): List of knowledge bases.
* **query**(str): Query string.
* **config**(RetrievalConfig, optional): Retrieval configuration. Default: None.
* **top_k**(int, optional): Number of results to return. Default: None.

**Returns**:

**List[Dict[str, Any]]**, returns a list of results containing text, score, source knowledge base IDs, and other information.

