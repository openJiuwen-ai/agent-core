# openjiuwen.core.retrieval.reranker.dashscope_reranker

## class openjiuwen.core.retrieval.reranker.dashscope_reranker.DashscopeReranker

继承 [StandardReranker](./standard_reranker.md)，通过 httpx 调用阿里云 DashScope **多模态排序**（`/text-rerank`）HTTP 接口，请求路径为类属性 `end_point`（`/services/rerank/text-rerank/text-rerank`）。与直接使用 `dashscope` Python SDK 不同，本实现走异步/同步 HTTP，接口风格与 [StandardReranker](./standard_reranker.md) 一致。产品说明与模型限制见阿里云文档：[通用文本排序模型 API](https://help.aliyun.com/zh/model-studio/developer-reference/text-rerank-api)（国际站：[Text rerank API](https://www.alibabacloud.com/help/en/model-studio/text-rerank-api)）。

`openjiuwen.extensions.vendor_specific.aliyun_reranker` 中的 `AliyunReranker` 已弃用，请改用本类。

```python
DashscopeReranker(config: RerankerConfig, max_retries: int = 3, retry_wait: float = 0.1, extra_headers: Optional[dict] = None, verify: bool | str | ssl.SSLContext = True, **kwargs)
```

初始化方式与 [StandardReranker](./standard_reranker.md) 相同。`RerankerConfig` 中需设置 DashScope 的 `api_base`、`api_key` 以及排序模型名（`model`）；`api_base` 与 `end_point` 拼接后应能访问官方文档中的排序接口地址（常见根路径形如 `https://dashscope.aliyuncs.com/api/v1`，以实际环境为准）。字段说明见 [RerankerConfig](../common/config.md)。

**参数**：

* **config**(RerankerConfig)：重排序器配置。
* **max_retries**(int)：最大重试次数。默认值：3。
* **retry_wait**(float)：重试等待时间（秒）。默认值：0.1。
* **extra_headers**(dict, 可选)：额外的请求头。默认值：None。
* **verify**(bool | str | ssl.SSLContext)：SSL 验证设置。默认值：True。
* **kwargs**：传入 httpx 客户端的其它关键字参数（与 `StandardReranker` 一致）。

### async rerank

```python
rerank(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

调用 DashScope 排序接口，返回文档 ID 到相关性得分的映射。

**参数**：

* **query**(str | Document)：查询文本；也可传入 `Document`（使用其 `text`）或 [MultimodalDocument](../common/document.md)（使用 `dashscope_input` 作为 `input.query`）。
* **doc**(list[str | Document])：待排序候选列表，元素为 `str`、`Document` 或 `MultimodalDocument`。当列表中存在多模态条目时，纯字符串元素会被包装为 `{"text": "..."}` 以满足接口结构。
* **instruct**(bool | str)：仅当值为**非空字符串**时，写入请求体 `parameters.instruct`，作为自定义任务说明；为 `True` 或 `False` 时不发送该字段。
* **kwargs**：会合并进请求体中的 `parameters`（例如可覆盖 `top_n`；未指定时 `top_n` 默认为当前候选文档数量）。

**返回**：

**dict[str, float]**，文档 ID 到相关性得分的映射。

### rerank_sync

```python
rerank_sync(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

与 `rerank` 相同逻辑的同步版本。

**参数**：

* **query**(str | Document)：同 `rerank`。
* **doc**(list[str | Document])：同 `rerank`。
* **instruct**(bool | str)：同 `rerank`。
* **kwargs**：同 `rerank`。

**返回**：

**dict[str, float]**，文档 ID 到相关性得分的映射。
