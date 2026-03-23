# memory.graph

`openjiuwen.core.memory.graph` is the Graph Memory submodule: it maintains a knowledge graph of entities, relations, and episodes from conversations and documents, supports LLM-based entity/relation extraction, merge and deduplication with existing graph data, and configurable hybrid (semantic + full-text) search with optional reranking.

**Docs**:

| Doc | Description |
|-----|-------------|
| [config](./config.md) | Graph memory config: EpisodeType, AddMemStrategy, SearchConfig, and related strategy types. |
| [extraction](./extraction.md) | Entity and relation extraction: multilingual base models, type definitions, extraction models, prompt assembly, and response parsing. |
| [graph_memory](./graph_memory.md) | GraphMemory class: public API (constructor, embedder/reranker attachment, search strategy registration, add_memory, search). |
