# retrieval

`openjiuwen.core.retrieval`是openJiuwen框架的检索模块，提供知识库管理、文档索引构建、向量检索、图检索等能力。

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/retrieval/` 目录下的示例代码，包括：
> - `showcase_text_embedding.py` - 文本嵌入示例
> - `showcase_multimodal_embedding.py` - 多模态嵌入示例
> - `showcase_reranker.py` - 重排序器示例

**Classes**：

| CLASS                                                         | DESCRIPTION     |
|---------------------------------------------------------------|-----------------|
| [KnowledgeBase](./retrieval/knowledge_base.md)                | 知识库抽象基类。         |
| [SimpleKnowledgeBase](./retrieval/simple_knowledge_base.md)   | 简单知识库实现。         |
| [GraphKnowledgeBase](./retrieval/graph_knowledge_base.md)     | 图知识库实现。          |
| [Retriever](./retrieval/retriever/base.md)                    | 检索器抽象基类。         |
| [VectorRetriever](./retrieval/retriever/vector_retriever.md)  | 向量检索器。            |
| [SparseRetriever](./retrieval/retriever/sparse_retriever.md)  | 稀疏检索器（BM25）。     |
| [HybridRetriever](./retrieval/retriever/hybrid_retriever.md)  | 混合检索器。            |
| [GraphRetriever](./retrieval/retriever/graph_retriever.md)    | 图检索器。              |
| [AgenticRetriever](./retrieval/retriever/agentic_retriever.md)| 智能检索器。            |
| [VectorStore](./retrieval/vector_store/base.md)               | 向量存储抽象基类。       |
| [ChromaVectorStore](./retrieval/vector_store/chroma_store.md) | ChromaDB向量存储实现。   |
| [MilvusVectorStore](./retrieval/vector_store/milvus_store.md)| Milvus向量存储实现。     |
| [Embedding](./retrieval/embedding/base.md)                     | 嵌入模型抽象基类。       |
| [APIEmbedding](./retrieval/embedding/api_embedding.md)        | API嵌入模型实现。        |
| [OpenAIEmbedding](./retrieval/embedding/openai_embedding.md) | OpenAI嵌入模型实现。     |
| [VLLMEmbedding](./retrieval/embedding/vllm_embedding.md)      | vLLM嵌入模型实现。        |
| [Reranker](./retrieval/reranker/base.md)                       | 重排序器抽象基类。       |
| [StandardReranker](./retrieval/reranker/standard_reranker.md) | 标准重排序器实现。       |
| [ChatReranker](./retrieval/reranker/chat_reranker.md)         | 聊天重排序器实现。       |
| [Indexer](./retrieval/indexing/indexer/base.md)               | 索引管理器抽象基类。     |
| [ChromaIndexer](./retrieval/indexing/indexer/chroma_indexer.md)| ChromaDB索引管理器实现。 |
| [MilvusIndexer](./retrieval/indexing/indexer/milvus_indexer.md)| Milvus索引管理器实现。   |
| [Processor](./retrieval/indexing/processor/base.md)                | 处理器抽象基类。         |
| [Parser](./retrieval/indexing/processor/parser/base.md)           | 文档解析器抽象基类。     |
| [AutoFileParser](./retrieval/indexing/processor/parser/auto_file_parser.md) | 自动文件解析器。         |
| [Chunker](./retrieval/indexing/processor/chunker/base.md)         | 文本分块器抽象基类。     |
| [CharChunker](./retrieval/indexing/processor/chunker/char_chunker.md) | 基于字符的分块器。       |
| [TokenizerChunker](./retrieval/indexing/processor/chunker/tokenizer_chunker.md) | 基于tokenizer的分块器。 |
| [TextChunker](./retrieval/indexing/processor/chunker/chunking.md) | 文本分块器（支持字符/token）。 |
| [Extractor](./retrieval/indexing/processor/extractor/base.md)      | 提取器抽象基类。         |
| [TripleExtractor](./retrieval/indexing/processor/extractor/triple_extractor.md) | 三元组提取器。          |

