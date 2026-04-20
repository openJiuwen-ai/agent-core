# reranker

`openjiuwen.core.retrieval.reranker` 提供了重排序器的抽象接口和实现。

**Classes**：

| CLASS | DESCRIPTION | 详细 API |
|-------|-------------|----------|
| **Reranker** | 重排序器抽象基类。 | [base.md](./base.md) |
| **StandardReranker** | 标准重排序器实现。 | [standard_reranker.md](./standard_reranker.md) |
| **DashscopeReranker** | 阿里云 DashScope 文本排序（HTTP）实现。 | [dashscope_reranker.md](./dashscope_reranker.md) |
| **ChatReranker** | 聊天重排序器实现。 | [chat_reranker.md](./chat_reranker.md) |
