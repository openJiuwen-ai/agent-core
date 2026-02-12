# openjiuwen.core.retrieval.reranker.base

## class openjiuwen.core.retrieval.reranker.base.Reranker

重排序器抽象基类，提供统一的接口用于文档重排序。

### abstractmethod async rerank

```python
rerank(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

重排序文档并返回文档到相关性得分的映射。

**参数**：

* **query**(str)：查询字符串。
* **doc**(list[str | Document])：待重排序的文档列表。
* **instruct**(bool | str)：是否提供指令给重排序器，传入字符串可自定义指令。默认值：True。
* **kwargs**：可变参数，用于传递其他额外的配置参数。

**返回**：

**dict[str, float]**，返回文档ID到相关性得分的映射。

### abstractmethod rerank_sync

```python
rerank_sync(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

重排序文档并返回文档到相关性得分的映射（同步版本）。

**参数**：

* **query**(str)：查询字符串。
* **doc**(list[str | Document])：待重排序的文档列表。
* **instruct**(bool | str)：是否提供指令给重排序器，传入字符串可自定义指令。默认值：True。
* **kwargs**：可变参数，用于传递其他额外的配置参数。

**返回**：

**dict[str, float]**，返回文档ID到相关性得分的映射。

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/retrieval/` 目录下的示例代码，包括：
> - `showcase_reranker.py` - 重排序器示例
