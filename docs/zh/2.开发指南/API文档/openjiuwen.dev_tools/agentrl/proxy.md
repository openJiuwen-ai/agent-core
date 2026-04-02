# openjiuwen.dev_tools.agentrl.proxy

`openjiuwen.dev_tools.agentrl.proxy` 是 agentrl 的**推理代理层**，负责：

- 提供 **BackendProxy**，在训练步之间后端 vLLM 地址变化时，仍为 Agent 提供稳定的 OpenAI 兼容推理基 URL（`/v1`）。

vLLM 推理服务的启停与地址由 **verl / Ray 训练链路**管理；Agent 侧通过 `BackendProxy` 使用稳定的推理基 URL。

## class openjiuwen.dev_tools.agentrl.proxy.backend_proxy.BackendProxy

```python
class openjiuwen.dev_tools.agentrl.proxy.backend_proxy.BackendProxy(llm_timeout_seconds: float = 30000, model_name: str = "agentrl")
```

在守护线程中运行的反向代理。

自动在启动时选择空闲端口——无需手动配置。

**Usage**：

```python
proxy = BackendProxy()
await proxy.start()            # 自动选择空闲端口
print(proxy.url)              # 例如 http://127.0.0.1:54321
proxy.update_backend_servers(["10.0.0.1:8000", "10.0.0.2:8000"])
await proxy.stop()
```

### __init__(self, llm_timeout_seconds: float = 30000, model_name: str = "agentrl") -> None

初始化后端代理。

**参数**：

* **llm_timeout_seconds**(float，可选)：LLM 请求超时时间（毫秒）。默认值：`30000`。
* **model_name**(str，可选)：模型名称。默认值：`"agentrl"`。

### property port(self) -> int

返回代理监听的端口（启动前为 0）。

### property url(self) -> str

返回代理的基础 URL（例如 http://127.0.0.1:54321）。

### update_backend_servers(self, servers) -> None

替换活动的后端服务器列表。

**参数**：

* **servers**：服务器地址列表或单个地址字符串。

### start(self) -> None

在守护线程中启动 Flask 代理并等待健康检查通过。

### stop(self) -> None

停止代理服务器线程并释放资源。

### start_sync(self) -> None

`start()` 的阻塞包装器。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.dev_tools.agentrl.proxy.backend_proxy import BackendProxy
>>> 
>>> async def demo_proxy():
>>>     proxy = BackendProxy()
>>>     
>>>     # 启动代理服务器
>>>     await proxy.start()
>>>     print(f"Proxy started at: {proxy.url}")
>>>     
>>>     # 更新后端服务器列表
>>>     proxy.update_backend_servers(["10.0.0.1:8000", "10.0.0.2:8000"])
>>>     
>>>     # 停止代理
>>>     await proxy.stop()
>>>     print("Proxy stopped")
>>> 
>>> asyncio.run(demo_proxy())
Proxy started at: http://127.0.0.1:54321
Proxy stopped
```
