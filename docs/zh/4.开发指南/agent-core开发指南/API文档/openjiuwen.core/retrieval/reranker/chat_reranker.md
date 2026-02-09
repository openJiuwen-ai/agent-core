# openjiuwen.core.retrieval.reranker.chat_reranker

## class openjiuwen.core.retrieval.reranker.chat_reranker.ChatReranker

基于聊天完成的重排序器实现，支持任何提供logprobs的聊天完成API。

```python
ChatReranker(config: RerankerConfig, max_retries: int = 3, retry_wait: float = 0.1, extra_headers: Optional[dict] = None, verify: bool | str | ssl.SSLContext = True, **kwargs)
```

初始化聊天重排序器。

**参数**：

* **config**(RerankerConfig)：重排序器配置，必须包含`yes_no_ids`字段（两个整数的序列）。
* **max_retries**(int)：最大重试次数。默认值：3。
* **retry_wait**(float)：重试等待时间（秒）。默认值：0.1。
* **extra_headers**(dict, 可选)：额外的请求头。默认值：None。
* **verify**(bool | str | ssl.SSLContext)：SSL验证设置。默认值：True。
* **kwargs**：可变参数，用于传递其他额外的配置参数。

**说明**：

ChatReranker是实验性功能，需要服务支持logprobs功能。输入文档列表必须只包含一个文档。

### test_compatibility

```python
test_compatibility() -> bool
```

测试所选服务是否兼容基于聊天完成的重排序。

**返回**：

**bool**，如果服务兼容则返回True，否则返回False。

### async rerank

```python
rerank(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

重排序文档并返回文档到相关性得分的映射。

**参数**：

* **query**(str)：查询字符串。
* **doc**(list[str | Document])：待重排序的文档列表（必须只包含一个文档）。
* **instruct**(bool | str)：是否提供指令给重排序器，传入字符串可自定义指令。默认值：True。
* **kwargs**：可变参数，用于传递其他额外的配置参数。

**返回**：

**dict[str, float]**，返回文档ID到相关性得分的映射。

### rerank_sync

```python
rerank_sync(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

重排序文档并返回文档到相关性得分的映射（同步版本）。

**参数**：

* **query**(str)：查询字符串。
* **doc**(list[str | Document])：待重排序的文档列表（必须只包含一个文档）。
* **instruct**(bool | str)：是否提供指令给重排序器，传入字符串可自定义指令。默认值：True。
* **kwargs**：可变参数，用于传递其他额外的配置参数。

**返回**：

**dict[str, float]**，返回文档ID到相关性得分的映射。

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/retrieval/` 目录下的示例代码，包括：
> - `showcase_reranker.py` - 重排序器示例
