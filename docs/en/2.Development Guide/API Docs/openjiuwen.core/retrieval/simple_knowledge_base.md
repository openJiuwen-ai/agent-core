# openjiuwen.core.retrieval.simple_knowledge_base

## class openjiuwen.core.retrieval.simple_knowledge_base.SimpleKnowledgeBase

Knowledge base implementation based on vector retrieval, providing complete knowledge base functionality including document parsing, chunking, index building, and retrieval.

> **Reference Implementation**: An example project based on `SimpleKnowledgeBase`. This project implements a continuously updated PEP (Python Enhancement Proposals) knowledge base for providing professional style and specification recommendations in code review and fixing. See [TomatoReviewer](https://gitcode.com/SushiNinja/TomatoReviewer) for details.


```python
SimpleKnowledgeBase(config: KnowledgeBaseConfig, vector_store: Optional[VectorStore] = None, embed_model: Optional[Embedding] = None, parser: Optional[Parser] = None, chunker: Optional[Chunker] = None, extractor: Optional[Extractor] = None, index_manager: Optional[Indexer] = None, retriever: Optional[Retriever] = None, llm_client: Optional[Any] = None, **kwargs: Any)
```

Initialize simple knowledge base.

**Parameters**:

* **config**(KnowledgeBaseConfig): Knowledge base configuration.
* **vector_store**(VectorStore, optional): Vector store instance. Default: None.
* **embed_model**(Embedding, optional): Embedding model instance. Default: None.
* **parser**(Parser, optional): Document parser instance. Default: None.
* **chunker**(Chunker, optional): Text chunker instance. Default: None.
* **extractor**(Extractor, optional): Extractor instance. Default: None.
* **index_manager**(Indexer, optional): Index manager instance. Default: None.
* **retriever**(Retriever, optional): Retriever instance (optional, will be automatically created based on index_type if not provided). Default: None.
* **llm_client**(Any, optional): LLM client instance. Default: None.
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

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase
>>> from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig
>>> from openjiuwen.core.retrieval.indexing.processor.parser.txt_md_parser import TxtMdParser
>>> 
>>> async def run():
...     config = KnowledgeBaseConfig(kb_id="test_kb", index_type="vector")
...     parser = TxtMdParser()
...     kb = SimpleKnowledgeBase(config=config, parser=parser)
...     documents = await kb.parse_files(["test.txt"])
...     print(f"Parsed {len(documents)} documents")
>>> asyncio.run(run())
Parsed 1 documents
```

### async add_documents

```python
add_documents(documents: List[Document], **kwargs: Any) -> List[str]
```

Add documents to the knowledge base, including document chunking and index building.

**Parameters**:

* **documents**(List[Document]): List of documents.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[str]**, returns a list of successfully added document IDs.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase
>>> from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig
>>> from openjiuwen.core.retrieval.common.document import Document
>>> from openjiuwen.core.retrieval.indexing.processor.chunker.char_chunker import CharChunker
>>> 
>>> async def run():
...     config = KnowledgeBaseConfig(kb_id="test_kb", index_type="vector")
...     chunker = CharChunker(chunk_size=512, chunk_overlap=50)
...     # Need to configure index_manager and embed_model
...     kb = SimpleKnowledgeBase(config=config, chunker=chunker)
...     documents = [Document(text="This is a test document", id_="doc1")]
...     doc_ids = await kb.add_documents(documents)
...     print(f"Added {len(doc_ids)} documents")
>>> asyncio.run(run())
Added 1 documents
```

### async retrieve

```python
retrieve(query: str, config: Optional[RetrievalConfig] = None, **kwargs: Any) -> List[RetrievalResult]
```

Retrieve relevant documents. If retriever is not provided, the corresponding retriever will be automatically created based on index_type.

**Parameters**:

* **query**(str): Query string.
* **config**(RetrievalConfig, optional): Retrieval configuration. Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[RetrievalResult]**, returns a list of retrieval results sorted by relevance score.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase
>>> from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig, RetrievalConfig
>>> 
>>> async def run():
...     config = KnowledgeBaseConfig(kb_id="test_kb", index_type="vector")
...     # Need to configure vector_store, embed_model, and index_manager
...     kb = SimpleKnowledgeBase(config=config)
...     results = await kb.retrieve("test query", config=RetrievalConfig(top_k=5))
...     print(f"Retrieved {len(results)} results")
>>> asyncio.run(run())
Retrieved 5 results
```

### async delete_documents

```python
delete_documents(doc_ids: List[str], **kwargs: Any) -> bool
```

Delete documents.

**Parameters**:

* **doc_ids**(List[str]): List of document IDs.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**bool**, returns True if all documents are deleted successfully, otherwise returns False.

### async update_documents

```python
update_documents(documents: List[Document], **kwargs: Any) -> List[str]
```

Update documents, including re-chunking and updating indexes.

**Parameters**:

* **documents**(List[Document]): List of documents.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[str]**, returns a list of successfully updated document IDs.

### async get_statistics

```python
get_statistics() -> Dict[str, Any]
```

Get knowledge base statistics.

**Returns**:

**Dict[str, Any]**, returns a dictionary containing knowledge base ID, index type, index information, and other statistics.

## func async retrieve_multi_kb

```python
retrieve_multi_kb(kbs: List[KnowledgeBase], query: str, config: Optional[RetrievalConfig] = None, top_k: Optional[int] = None) -> List[str]
```

Perform retrieval on multiple knowledge bases, deduplicate by text and merge results in descending order by score.

**Parameters**:

* **kbs**(List[KnowledgeBase]): List of knowledge bases.
* **query**(str): Query string.
* **config**(RetrievalConfig, optional): Retrieval configuration. Default: None.
* **top_k**(int, optional): Number of results to return. Default: None.

**Returns**:

**List[str]**, returns a deduplicated and sorted list of texts.

## func async retrieve_multi_kb_with_source

```python
retrieve_multi_kb_with_source(kbs: List[KnowledgeBase], query: str, config: Optional[RetrievalConfig] = None, top_k: Optional[int] = None) -> List[Dict[str, Any]]
```

Perform retrieval on multiple knowledge bases, returning results with source information. Result items include: text/score/raw_score/raw_score_scaled/kb_ids.

**Parameters**:

* **kbs**(List[KnowledgeBase]): List of knowledge bases.
* **query**(str): Query string.
* **config**(RetrievalConfig, optional): Retrieval configuration. Default: None.
* **top_k**(int, optional): Number of results to return. Default: None.

**Returns**:

**List[Dict[str, Any]]**, returns a list of results containing text, score, source knowledge base IDs, and other information.

