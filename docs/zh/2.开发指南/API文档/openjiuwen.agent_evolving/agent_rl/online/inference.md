# openjiuwen.agent_evolving.agent_rl.online.inference

vLLM 推理服务的 LoRA 热加载通知器。通过调用 vLLM 原生的 `/v1/load_lora_adapter` 与 `/v1/unload_lora_adapter` 端点，在不重启服务的前提下为指定用户热加载（或卸载）LoRA 适配器。在线 RL 训练完成并发布 LoRA 后，由调度器调用此类通知 vLLM 应用新权重。

## class openjiuwen.agent_evolving.agent_rl.online.inference.notifier.InferenceNotifier

```python
class InferenceNotifier(vllm_base_url: str, timeout: float = 120.0, http_client: Optional[httpx.AsyncClient] = None)
```

异步 HTTP 客户端，负责通知 vLLM 热加载/卸载 LoRA 适配器。

**参数**：

* **vllm_base_url**(str)：vLLM 服务的基础 URL（末尾 `/` 会被去除），例如 `http://vllm.local`。
* **timeout**(float)：单次请求超时（秒）。默认值：`120.0`。
* **http_client**(Optional[httpx.AsyncClient]，可选)：外部注入的异步 HTTP 客户端。为 `None` 时，通知器会创建并持有自己的 `httpx.AsyncClient`。默认值：`None`。

**说明**：

- 当传入外部 `http_client` 时，通知器不持有该客户端，`close()` 不会关闭它；自行创建的客户端则由通知器持有并在关闭时释放。

### async def close

```python
async def close() -> None
```

关闭底层 HTTP 客户端。

**说明**：

- 仅当通知器自行创建（持有）HTTP 客户端时才会执行关闭；若客户端由外部注入，则该方法为空操作。可在异步上下文中安全调用。

### async def notify_update

```python
async def notify_update(user_id: str, lora_path: str) -> None
```

通知 vLLM 为指定用户热加载 LoRA 适配器。

**参数**：

* **user_id**(str)：用户标识符，同时作为 vLLM 的 `lora_name`。加载后，请求中指定该 `lora_name` 即可自动应用新权重。
* **lora_path**(str)：LoRA 权重目录的绝对路径。

**说明**：

- 向 `{vllm_base_url}/v1/load_lora_adapter` 发送 HTTP POST，JSON body 为：

```json
{
  "lora_name": "<user_id>",
  "lora_path": "<lora_path>",
  "load_inplace": true
}
```

- 使用 `self.timeout` 作为请求超时。
- 当 HTTP 状态码 `>= 400` 时，抛出 `RuntimeError`，消息形如 `vLLM load_lora_adapter failed: status=<code>, body=<body 前 400 字符>`。
- 成功时记录 INFO 日志：`LoRA hot-loaded for user %s: %s`。

### async def unload

```python
async def unload(user_id: str) -> None
```

卸载指定用户的 LoRA 适配器（可用于清理非活跃用户）。

**参数**：

* **user_id**(str)：要卸载的 `lora_name`（用户标识符）。

**说明**：

- 向 `{vllm_base_url}/v1/unload_lora_adapter` 发送 HTTP POST，JSON body 为 `{"lora_name": "<user_id>"}`。
- 通过 `resp.raise_for_status()` 抛出 HTTP 错误（与 `notify_update` 的自定义 `RuntimeError` 不同）。
- 成功时记录 INFO 日志：`LoRA unloaded for user %s`。

## 被使用情况

`InferenceNotifier` 在以下位置被构造或调用：

- [ppo_executor.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/scheduler/ppo_executor.py)：`PPOTrainingExecutor` 构造时接收 `Optional[InferenceNotifier]`；`aclose()` 中调用 `close()`；LoRA 发布成功后调用 `notify_update(user_id, published_lora_path)`（失败视为非致命警告）。
- [online_training_scheduler.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/scheduler/online_training_scheduler.py)：构造 `PPOTrainingExecutor` 时透传 `notifier`。
- [services.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/launcher/services.py)：`start_online_training_scheduler` 中构造 `InferenceNotifier(runtime.inference_url)` 并传入调度器。
- [rl_optimizer.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/optimizer/rl_optimizer.py)：`setup_inference(vllm_url)` 存储 URL，构建时构造 `InferenceNotifier` 并传入 `OnlineTrainingScheduler`。
- [test_gateway_support.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/test_gateway_support.py)：测试注入伪造 `httpx.AsyncClient` 验证 `notify_update` 的请求路径与 body，并验证外部客户端不被 `close()` 关闭。
