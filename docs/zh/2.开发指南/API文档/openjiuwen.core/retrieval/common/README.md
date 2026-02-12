# common

`openjiuwen.core.retrieval.common` 提供了检索模块的通用类和配置。

**详细 API 文档**：[callbacks.md](./callbacks.md)、[config.md](./config.md)、[document.md](./document.md)、[retrieval_result.md](./retrieval_result.md)、[triple.md](./triple.md)、[triple_beam.md](./triple_beam.md)、[triple_memory.md](./triple_memory.md)

**Classes**：

| CLASS | DESCRIPTION | 详细 API |
|-------|-------------|----------|
| **BaseCallback** | 回调函数抽象基类。 | [callbacks.md](./callbacks.md) |
| **TqdmCallback** | 基于 tqdm 的进度回调实现。 | [callbacks.md](./callbacks.md) |
| **KnowledgeBaseConfig** | 知识库配置类。 | [config.md](./config.md) |
| **RetrievalConfig** | 检索配置类。 | [config.md](./config.md) |
| **IndexConfig** | 索引配置类。 | [config.md](./config.md) |
| **VectorStoreConfig** | 向量存储配置类。 | [config.md](./config.md) |
| **EmbeddingConfig** | 嵌入模型配置类。 | [config.md](./config.md) |
| **RerankerConfig** | 重排序器配置类。 | [config.md](./config.md) |
| **Document** | 文档类。 | [document.md](./document.md) |
| **TextChunk** | 文本块类。 | [document.md](./document.md) |
| **MultimodalDocument** | 多模态文档类。 | [document.md](./document.md) |
| **SearchResult** | 搜索结果类。 | [retrieval_result.md](./retrieval_result.md) |
| **RetrievalResult** | 检索结果类。 | [retrieval_result.md](./retrieval_result.md) |
| **Triple** | 三元组类。 | [triple.md](./triple.md) |
| **TripleBeam** | 三元组束类。 | [triple_beam.md](./triple_beam.md) |
| **TripleMemory** | 三元组记忆类。 | [triple_memory.md](./triple_memory.md) |
