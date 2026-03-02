# Knowledge Retrieval

Knowledge Retrieval is the module in openJiuwen that manages knowledge bases and handles document retrieval. It covers document parsing, chunking, indexing, and vector-based retrieval, so developers do not need to implement these steps themselves when building Agents.

In dialogue scenarios, users need to quickly and accurately retrieve relevant information from a large number of documents. Knowledge Retrieval organizes documents through vectorization and indexing, allowing Agents to retrieve relevant context for downstream generation.

Using Knowledge Retrieval typically involves the following two steps:

1. Create a knowledge base instance: Use ```SimpleKnowledgeBase``` as an example to create a knowledge base instance, where you configure the vector store, embedding model, document parser, and chunker used by the knowledge base.
2. Use the knowledge base instance: Supports main operations such as adding documents, building indexes, and retrieving documents, helping agents better utilize knowledge content.

> **Reference Implementation**: An example project based on `SimpleKnowledgeBase`. This project implements a continuously updated PEP (Python Enhancement Proposals) knowledge base for providing professional style and specification recommendations in code review and fixing. See [TomatoReviewer](https://gitcode.com/SushiNinja/TomatoReviewer) for details.

# Create Knowledge Base Instance

To create a knowledge base instance through ```SimpleKnowledgeBase```, you need to pass in the configuration parameters. The configuration includes knowledge base ID, index type (vector/bm25/hybrid), etc. In addition, data storage in the knowledge base involves vector storage, embedding models, index managers, etc., which also need to be configured during initialization. Default implementations are available under `openjiuwen.core.retrieval`, and each component can be replaced with a custom implementation.

The following example uses the default ```MilvusVectorStore```, ```OpenAIEmbedding```, and ```MilvusIndexer``` to illustrate how to create a knowledge base instance.

```python
import asyncio

from openjiuwen.core.retrieval.common.config import (
    EmbeddingConfig,
    KnowledgeBaseConfig,
    RetrievalConfig,
    VectorStoreConfig,
)
from openjiuwen.core.retrieval.embedding.openai_embedding import OpenAIEmbedding
from openjiuwen.core.retrieval.indexing.indexer.milvus_indexer import MilvusIndexer
from openjiuwen.core.retrieval.indexing.processor.chunker.chunking import TextChunker
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import AutoFileParser
from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase
from openjiuwen.core.retrieval.vector_store.milvus_store import MilvusVectorStore


async def create_kb() -> None:
    """Create knowledge base"""
    # Define knowledge base configuration
    kb_config = KnowledgeBaseConfig(
        kb_id="test_kb_001",
        index_type="hybrid",  # Supports vector, bm25, hybrid
        use_graph=False,
        chunk_size=512,
        chunk_overlap=50,
    )

    # Create vector store
    # milvus_uri represents the host address of the Milvus service (default is http://localhost:19530), milvus_token is the key for authentication (default is empty)
    vector_store_config = VectorStoreConfig(
        collection_name=f"kb_{kb_config.kb_id}_chunks",
        distance_metric="cosine",
    )
    milvus_uri = "http://localhost:19530"
    milvus_token = ""
    vector_store = MilvusVectorStore(
        config=vector_store_config,
        milvus_uri=milvus_uri,
        milvus_token=milvus_token,
    )

    # Create embedding model
    # model_name represents the embedding model name used, base_url represents the address for requesting the embedding API, api_key represents the secret key, max_retries represents the maximum number of retries, timeout represents the request timeout
    embedding_config = EmbeddingConfig(
        model_name="xxxxx",
        api_key="sk-xxxxx",
        base_url="http://xxxxx",
    )

    # You can specify embedding dimension or leave it at default
    embed_model = OpenAIEmbedding(config=embedding_config, max_retries=3, timeout=60, dimension=128)

    # Create index manager (shares VectorStoreConfig with vector store)
    indexer = MilvusIndexer(
        config=vector_store_config,
        milvus_uri=milvus_uri,
        milvus_token=milvus_token,
    )

    # Create document parser (automatically recognizes file formats)
    parser = AutoFileParser()

    # Create text chunker
    chunker = TextChunker(
        chunk_size=kb_config.chunk_size,
        chunk_overlap=kb_config.chunk_overlap,
        chunk_unit="token",  # Supports char or token
    )

    # Create knowledge base instance
    knowledge_base = SimpleKnowledgeBase(
        config=kb_config,
        vector_store=vector_store,
        embed_model=embed_model,
        parser=parser,
        chunker=chunker,
        index_manager=indexer,
    )

    return knowledge_base
```

# Use Knowledge Base Instance

The knowledge base supports main operations such as adding documents, retrieving documents, deleting documents, and updating documents.

## Add Documents

Documents can be added to the knowledge base through the ```parse_files``` and ```add_documents``` methods. The knowledge base automatically parses files, splits them into chunks, and builds the corresponding indexes.

```python
async def add_documents(knowledge_base: SimpleKnowledgeBase) -> None:
    """Add documents"""
    # Prepare document file paths
    file_paths = [
        "./documents/doc1.pdf",
        "./documents/doc2.txt",
        "./documents/doc3.md",
    ]

    # Parse files into a list of Document objects
    documents = await knowledge_base.parse_files(file_paths)

    # Add doc id
    for doc_idx, doc in enumerate(documents, 1):
        doc.id_ = f"doc_id_{doc_idx}"

    # Add documents to knowledge base (automatically chunk and build index)
    doc_ids = await knowledge_base.add_documents(documents)
    print(f"Successfully added {len(doc_ids)} documents, document IDs: {doc_ids}")
```

## Retrieve Documents

Documents are retrieved through the ```retrieve``` method, which supports vector, sparse, and hybrid retrieval modes.

```python
async def retrieval(knowledge_base: SimpleKnowledgeBase) -> None:
    # Configure retrieval parameters
    retrieval_config = RetrievalConfig(
        top_k=3,  # Return top 3 results
        filters=None,  # Metadata filter conditions (optional)
    )

    # Execute retrieval
    query = "What is artificial intelligence?"
    results = await knowledge_base.retrieve(query, config=retrieval_config)

    # Print retrieval results
    for i, result in enumerate(results, 1):
        print(f"\nResult {i}:")
        print(f"  Text: {result.text[:100]}...")
        print(f"  Similarity: {result.score:.4f}")
        print(f"  Document ID: {result.doc_id}")
        print(f"  Chunk ID: {result.chunk_id}")
```

## Delete Documents

Documents in the knowledge base can be deleted through the ```delete_documents``` method.

```python
async def delete_document_id(knowledge_base: SimpleKnowledgeBase) -> None:
    doc_ids_to_delete = ["doc_id_1", "doc_id_2"]
    success = await knowledge_base.delete_documents(doc_ids_to_delete)
    print(f"Delete result: {success}")
```

## Multi-Knowledge Base Retrieval

`openjiuwen.core.retrieval.simple_knowledge_base` also provides helper functions for multi-knowledge base retrieval, suitable for querying multiple `KnowledgeBase` instances in a single request:

- `retrieve_multi_kb(kbs: list[KnowledgeBase], query: str, config: RetrievalConfig | None = None, top_k: int | None = None) -> list[str]`
- `retrieve_multi_kb_with_source(kbs: list[KnowledgeBase], query: str, config: RetrievalConfig | None = None, top_k: int | None = None) -> list[dict]`

Example:

```python
from openjiuwen.core.retrieval.simple_knowledge_base import (
    retrieve_multi_kb,
    retrieve_multi_kb_with_source,
)


async def search_across_kbs(kbs: list[SimpleKnowledgeBase]) -> None:
    query = "What is vector retrieval?"

    # Return only deduplicated text results
    texts = await retrieve_multi_kb(kbs, query, config=RetrievalConfig(top_k=5))
    for i, t in enumerate(texts, 1):
        print(f"{i}. {t}")

    # Return results with source information
    results = await retrieve_multi_kb_with_source(kbs, query, config=RetrievalConfig(top_k=5))
    for item in results:
        print(f"text={item['text']}, score={item['score']}, kb_ids={list(item['kb_ids'])}")
```

## Get Statistics

The ```get_statistics``` method returns basic metadata about the knowledge base and its index state.

```python
# Get knowledge base statistics
stats = await knowledge_base.get_statistics()
print(f"Knowledge Base ID: {stats['kb_id']}")
print(f"Index Type: {stats['index_type']}")
print(f"Index Info: {stats['index_info']}")
print(f"Component Status: {stats}")
```

## Agentic Retrieval

Both `SimpleKnowledgeBase` and `GraphKnowledgeBase` support **agentic retrieval** — an LLM-powered iterative retrieval strategy that automatically rewrites queries and fuses results across multiple rounds for higher recall and precision.

To enable agentic retrieval, set `agentic=True` in `RetrievalConfig` and provide an `llm_client` when creating the knowledge base:

### Agentic retrieval with SimpleKnowledgeBase
```python
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient

# Create an LLM client for agentic retrieval
llm_client = OpenAIModelClient(
    api_key="sk-xxxxx",
    base_url="http://xxxxx",
    model="<model_name>",
)

# Pass llm_client when creating the knowledge base
knowledge_base = SimpleKnowledgeBase(
    config=kb_config,
    vector_store=vector_store,
    embed_model=embed_model,
    llm_client=llm_client,  # Required for agentic retrieval
)

# Enable agentic retrieval via RetrievalConfig
retrieval_config = RetrievalConfig(
    top_k=5,
    agentic=True,  # Enable agentic retrieval
)

results = await knowledge_base.retrieve("complex multi-hop question", config=retrieval_config)
```

### Agentic retrieval with GraphKnowledgeBase

```python
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient

# Create an LLM client for agentic retrieval
llm_client = OpenAIModelClient(
    api_key="sk-xxxxx",
    base_url="http://xxxxx",
    model="<model_name>",
)

graph_kb = GraphKnowledgeBase(
    config=kb_config,
    vector_store=vector_store,
    embed_model=embed_model,
    llm_client=llm_client,
)

# Enable agentic retrieval via RetrievalConfig
retrieval_config = RetrievalConfig(
    top_k=5,
    agentic=True,
    use_graph=True,
    graph_expansion=True,
)

results = await graph_kb.retrieve("complex multi-hop question", config=retrieval_config)
```

Under the hood, the knowledge base wraps its internal retriever with an `AgenticRetriever`, which performs iterative query rewriting and RRF fusion. When used with `GraphKnowledgeBase`, graph-specific features such as graph expansion and triple linking are automatically enabled unless disabled through RetrievalConfig.

> **Note**: Agentic retrieval requires an `llm_client` to be configured on the knowledge base. The `AgenticRetriever` itself accepts any `Retriever` subclass, so it works with vector, sparse, hybrid, and graph retrievers alike. To create kb_config, vector_store and embed_model, refer the complete knowledge base example below.

# Complete Knowledge Base Usage Code Example

```python
import asyncio

from openjiuwen.core.retrieval.common.config import (
    EmbeddingConfig,
    KnowledgeBaseConfig,
    RetrievalConfig,
    VectorStoreConfig,
)
from openjiuwen.core.retrieval.embedding.openai_embedding import OpenAIEmbedding
from openjiuwen.core.retrieval.indexing.indexer.milvus_indexer import MilvusIndexer
from openjiuwen.core.retrieval.indexing.processor.chunker.chunking import TextChunker
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import AutoFileParser
from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase
from openjiuwen.core.retrieval.vector_store.milvus_store import MilvusVectorStore


async def create_kb() -> None:
    """Create knowledge base"""
    # Define knowledge base configuration
    kb_config = KnowledgeBaseConfig(
        kb_id="test_kb_001",
        index_type="hybrid",  # Supports vector, bm25, hybrid
        use_graph=False,
        chunk_size=512,
        chunk_overlap=50,
    )

    # Create vector store
    # milvus_uri represents the host address of the Milvus service (default is http://localhost:19530), milvus_token is the key for authentication (default is empty)
    vector_store_config = VectorStoreConfig(
        collection_name=f"kb_{kb_config.kb_id}_chunks",
        distance_metric="cosine",
    )
    milvus_uri = "http://localhost:19530"
    milvus_token = ""
    vector_store = MilvusVectorStore(
        config=vector_store_config,
        milvus_uri=milvus_uri,
        milvus_token=milvus_token,
    )

    # Create embedding model
    # model_name represents the embedding model name used, base_url represents the address for requesting the embedding API, api_key represents the secret key, max_retries represents the maximum number of retries, timeout represents the request timeout
    embedding_config = EmbeddingConfig(
        model_name="xxxxx",
        api_key="sk-xxxxx",
        base_url="http://xxxxx",
    )

    # You can specify embedding dimension or leave it at default
    embed_model = OpenAIEmbedding(config=embedding_config, max_retries=3, timeout=60, dimension=128)

    # Create index manager (shares VectorStoreConfig with vector store)
    indexer = MilvusIndexer(
        config=vector_store_config,
        milvus_uri=milvus_uri,
        milvus_token=milvus_token,
    )

    # Create document parser (automatically recognizes file formats)
    parser = AutoFileParser()

    # Create text chunker
    chunker = TextChunker(
        chunk_size=kb_config.chunk_size,
        chunk_overlap=kb_config.chunk_overlap,
        chunk_unit="token",  # Supports char or token
    )

    # Create knowledge base instance
    knowledge_base = SimpleKnowledgeBase(
        config=kb_config,
        vector_store=vector_store,
        embed_model=embed_model,
        parser=parser,
        chunker=chunker,
        index_manager=indexer,
    )

    return knowledge_base


async def add_documents(knowledge_base: SimpleKnowledgeBase) -> None:
    """Add documents"""
    # Prepare document file paths
    file_paths = [
        "./documents/doc1.pdf",
        "./documents/doc2.txt",
        "./documents/doc3.md",
    ]

    # Parse files into a list of Document objects
    documents = await knowledge_base.parse_files(file_paths)

    # Add doc id
    for doc_idx, doc in enumerate(documents, 1):
        doc.id_ = f"doc_id_{doc_idx}"

    # Add documents to knowledge base (automatically chunk and build index)
    doc_ids = await knowledge_base.add_documents(documents)
    print(f"Successfully added {len(doc_ids)} documents, document IDs: {doc_ids}")


async def retrieval(knowledge_base: SimpleKnowledgeBase) -> None:
    # Configure retrieval parameters
    retrieval_config = RetrievalConfig(
        top_k=3,  # Return top 3 results
        filters=None,  # Metadata filter conditions (optional)
    )

    # Execute retrieval
    query = "What is artificial intelligence?"
    results = await knowledge_base.retrieve(query, config=retrieval_config)

    # Print retrieval results
    for i, result in enumerate(results, 1):
        print(f"\nResult {i}:")
        print(f"  Text: {result.text[:100]}...")
        print(f"  Similarity: {result.score:.4f}")
        print(f"  Document ID: {result.doc_id}")
        print(f"  Chunk ID: {result.chunk_id}")
        print(f"  meta: {result.metadata}")


async def delete_document_id(knowledge_base: SimpleKnowledgeBase) -> None:
    doc_ids_to_delete = ["doc_id_1", "doc_id_2"]
    success = await knowledge_base.delete_documents(doc_ids_to_delete)
    print(f"Delete result: {success}")


if __name__ == "__main__":
    kb = asyncio.run(create_kb())
    asyncio.run(add_documents(kb))
    asyncio.run(retrieval(kb))
    asyncio.run(delete_document_id(kb))
```

Output Example

```
1. Add documents to knowledge base
   Successfully added 2 documents: ['doc_id_1', 'doc_id_2']

2. Retrieve relevant documents
   Query: Application scenarios of artificial intelligence
   Found 3 relevant results:
     1. Similarity: 0.8523, Text: Artificial intelligence has wide applications in healthcare, finance, education and other fields...

3. Get knowledge base statistics
   Knowledge Base ID: test_kb_001
   Index Type: hybrid
   Index Info: {'exists': True, 'collection_name': 'kb_test_kb_001_chunks', 'count': 15}

4. Update documents
   Successfully updated 1 document

5. Delete documents
   Delete result: True
```

