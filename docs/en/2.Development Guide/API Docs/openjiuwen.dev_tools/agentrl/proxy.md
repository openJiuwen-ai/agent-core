# openjiuwen.dev_tools.agentrl.proxy

`openjiuwen.dev_tools.agentrl.proxy` is the **inference proxy layer** for agentrl, responsible for:

- Providing **BackendProxy**, which still gives Agents a **stable OpenAI-compatible inference base URL** (`/v1`) while backend vLLM addresses change between training steps.

vLLM inference service startup/shutdown and addresses are managed by the **verl / Ray** training stack; on the Agent side, use `BackendProxy` for a stable inference base URL.

## class openjiuwen.dev_tools.agentrl.proxy.backend_proxy.BackendProxy

```python
class openjiuwen.dev_tools.agentrl.proxy.backend_proxy.BackendProxy(llm_timeout_seconds: float = 30000, model_name: str = "agentrl")
```

Reverse proxy running in a daemon thread.

Automatically selects a free port at startup — no manual configuration needed.

**Usage**:

```python
proxy = BackendProxy()
await proxy.start()            # Automatically select free port
print(proxy.url)              # e.g. http://127.0.0.1:54321
proxy.update_backend_servers(["10.0.0.1:8000", "10.0.0.2:8000"])
await proxy.stop()
```

### __init__(self, llm_timeout_seconds: float = 30000, model_name: str = "agentrl") -> None

Initialize backend proxy.

**Parameters**:

* **llm_timeout_seconds**(float, optional): LLM request timeout (milliseconds). Default: `30000`.
* **model_name**(str, optional): Model name. Default: `"agentrl"`.

### property port(self) -> int

Returns the port the proxy listens on (0 before start).

### property url(self) -> str

Returns the proxy base URL (e.g. http://127.0.0.1:54321).

### update_backend_servers(self, servers) -> None

Replace the active backend server list.

**Parameters**:

* **servers**: List of server addresses or a single address string.

### start(self) -> None

Start Flask proxy in daemon thread and wait for health check to pass.

### stop(self) -> None

Stop proxy server thread and release resources.

### start_sync(self) -> None

Blocking wrapper for `start()`.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.dev_tools.agentrl.proxy.backend_proxy import BackendProxy
>>> 
>>> async def demo_proxy():
>>>     proxy = BackendProxy()
>>>     
>>>     # Start proxy server
>>>     await proxy.start()
>>>     print(f"Proxy started at: {proxy.url}")
>>>     
>>>     # Update backend server list
>>>     proxy.update_backend_servers(["10.0.0.1:8000", "10.0.0.2:8000"])
>>>     
>>>     # Stop proxy
>>>     await proxy.stop()
>>>     print("Proxy stopped")
>>> 
>>> asyncio.run(demo_proxy())
Proxy started at: http://127.0.0.1:54321
Proxy stopped
```
