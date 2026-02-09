# retrieval

`openjiuwen.core.retrieval` is the retrieval module of the openJiuwen framework, providing knowledge base management, document indexing, vector retrieval, graph retrieval, and other capabilities.

**Classes**:

| CLASS                                                         | DESCRIPTION     |
|---------------------------------------------------------------|-----------------|
| [KnowledgeBase](./retrieval/knowledge_base.md)                | Knowledge base abstract base class.         |
| [SimpleKnowledgeBase](./retrieval/simple_knowledge_base.md)   | Simple knowledge base implementation.         |
| [GraphKnowledgeBase](./retrieval/graph_knowledge_base.md)     | Graph knowledge base implementation.          |
| [Retriever](./retrieval/retriever/base.md)                    | Retriever abstract base class.         |
| [VectorRetriever](./retrieval/retriever/vector_retriever.md)  | Vector retriever.            |
| [SparseRetriever](./retrieval/retriever/sparse_retriever.md)  | Sparse retriever (BM25).     |
| [HybridRetriever](./retrieval/retriever/hybrid_retriever.md)  | Hybrid retriever.            |
| [GraphRetriever](./retrieval/retriever/graph_retriever.md)    | Graph retriever.              |
| [AgenticRetriever](./retrieval/retriever/agentic_retriever.md)| Agentic retriever.            |
| [VectorStore](./retrieval/vector_store/base.md)               | Vector store abstract base class.       |
| [ChromaVectorStore](./retrieval/vector_store/chroma_store.md) | ChromaDB vector store implementation.   |
| [MilvusVectorStore](./retrieval/vector_store/milvus_store.md)| Milvus vector store implementation.     |
| [Embedding](./retrieval/embedding/base.md)                     | Embedding model abstract base class.       |
| [APIEmbedding](./retrieval/embedding/api_embedding.md)        | API embedding model implementation.        |
| [OllamaEmbedding](./retrieval/embedding/ollama_embedding.md)  | Ollama embedding model implementation.     |
| [Indexer](./retrieval/indexing/indexer/base.md)               | Index manager abstract base class.     |
| [ChromaIndexer](./retrieval/indexing/indexer/chroma_indexer.md)| ChromaDB index manager implementation. |
| [MilvusIndexer](./retrieval/indexing/indexer/milvus_indexer.md)| Milvus index manager implementation.   |
| [Processor](./retrieval/indexing/processor/base.md)                | Processor abstract base class.         |
| [Parser](./retrieval/indexing/processor/parser/base.md)           | Document parser abstract base class.     |
| [AutoFileParser](./retrieval/indexing/processor/parser/auto_file_parser.md) | Auto file parser.         |
| [Chunker](./retrieval/indexing/processor/chunker/base.md)         | Text chunker abstract base class.     |
| [CharChunker](./retrieval/indexing/processor/chunker/char_chunker.md) | Character-based chunker.       |
| [TokenizerChunker](./retrieval/indexing/processor/chunker/tokenizer_chunker.md) | Tokenizer-based chunker. |
| [TextChunker](./retrieval/indexing/processor/chunker/chunking.md) | Text chunker (supports character/token). |
| [Extractor](./retrieval/indexing/processor/extractor/base.md)      | Extractor abstract base class.         |
| [TripleExtractor](./retrieval/indexing/processor/extractor/triple_extractor.md) | Triple extractor.          |

