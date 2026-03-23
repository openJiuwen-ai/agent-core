# memory.graph

`openjiuwen.core.memory.graph` 是图记忆（Graph Memory）子模块，基于知识图谱维护对话与文档中的实体、关系与情节（episode），支持通过 LLM 抽取实体与关系、与已有图数据去重合并，以及可配置的混合检索（语义 + 全文）与可选重排。

**文档**：

| 文档 | 说明 |
|------|------|
| [config](./config.md) | 图记忆配置：EpisodeType、AddMemStrategy、SearchConfig 及相关策略类型。 |
| [extraction](./extraction.md) | 实体与关系抽取：多语言模型基类、类型定义、抽取模型、提示组装与响应解析。 |
| [graph_memory](./graph_memory.md) | 图记忆核心类 GraphMemory：对外接口（构造、嵌入/重排挂载、检索策略、写入与检索）。 |
