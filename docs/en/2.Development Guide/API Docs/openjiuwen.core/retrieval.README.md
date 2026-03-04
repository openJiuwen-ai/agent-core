# retrieval

`openjiuwen.core.retrieval` is the retrieval module of the openJiuwen framework, providing knowledge base management, document indexing, vector retrieval, graph retrieval, and other capabilities.

> **Reference Examples**: For more usage examples, please refer to the example code in the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository under the `examples/retrieval/` directory, including:
> - `showcase_text_embedding.py` - Text embedding examples
> - `showcase_multimodal_embedding.py` - Multimodal embedding examples
> - `showcase_reranker.py` - Reranker examples

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
| [OpenAIEmbedding](./retrieval/embedding/openai_embedding.md) | OpenAI embedding model implementation.     |
| [VLLMEmbedding](./retrieval/embedding/vllm_embedding.md)      | vLLM embedding model implementation.        |
| [Reranker](./retrieval/reranker/base.md)                       | Reranker abstract base class.       |
| [StandardReranker](./retrieval/reranker/standard_reranker.md) | Standard reranker implementation.       |
| [ChatReranker](./retrieval/reranker/chat_reranker.md)         | Chat reranker implementation.       |
| [Indexer](./retrieval/indexing/indexer/base.md)               | Index manager abstract base class.     |
| [ChromaIndexer](./retrieval/indexing/indexer/chroma_indexer.md)| ChromaDB index manager implementation. |
| [MilvusIndexer](./retrieval/indexing/indexer/milvus_indexer.md)| Milvus index manager implementation.   |
| [Processor](./retrieval/indexing/processor/base.md)                | Processor abstract base class.         |
| [Parser](./retrieval/indexing/processor/parser/base.md)           | Document parser abstract base class.     |
| [AutoParser](./retrieval/indexing/processor/parser/auto_parser.md) | Unified parser (files and URLs). |
| [AutoLinkParser](./retrieval/indexing/processor/parser/auto_link_parser.md) | Link parser (WeChat articles, web pages). |
| [AutoFileParser](./retrieval/indexing/processor/parser/auto_file_parser.md) | Auto file parser (by extension).         |
| [Chunker](./retrieval/indexing/processor/chunker/base.md)         | Text chunker abstract base class.     |
| [CharChunker](./retrieval/indexing/processor/chunker/char_chunker.md) | Character-based chunker.       |
| [TokenizerChunker](./retrieval/indexing/processor/chunker/tokenizer_chunker.md) | Tokenizer-based chunker. |
| [TextChunker](./retrieval/indexing/processor/chunker/chunking.md) | Text chunker (supports character/token). |
| [Extractor](./retrieval/indexing/processor/extractor/base.md)      | Extractor abstract base class.         |
| [TripleExtractor](./retrieval/indexing/processor/extractor/triple_extractor.md) | Triple extractor.          |

