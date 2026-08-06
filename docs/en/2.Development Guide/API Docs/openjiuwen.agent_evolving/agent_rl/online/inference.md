# openjiuwen.agent_evolving.agent_rl.online.inference

LoRA hot-loading notifier for the vLLM inference service. By calling vLLM's native `/v1/load_lora_adapter` and `/v1/unload_lora_adapter` endpoints, it hot-loads (or unloads) a LoRA adapter for a specific user without restarting the service. After online RL training completes and publishes a LoRA, the scheduler calls this class to notify vLLM to apply the new weights.

## class openjiuwen.agent_evolving.agent_rl.online.inference.notifier.InferenceNotifier

```python
class InferenceNotifier(vllm_base_url: str, timeout: float = 120.0, http_client: Optional[httpx.AsyncClient] = None)
```

Asynchronous HTTP client responsible for notifying vLLM to hot-load/unload LoRA adapters.

**Parameters**:

* **vllm_base_url**(str): Base URL of the vLLM service (trailing `/` is stripped), e.g. `http://vllm.local`.
* **timeout**(float): Per-request timeout in seconds. Default: `120.0`.
* **http_client**(Optional[httpx.AsyncClient], optional): Externally injected async HTTP client. When `None`, the notifier creates and owns its own `httpx.AsyncClient`. Default: `None`.

**Notes**:

- When an external `http_client` is passed in, the notifier does not own the client and `close()` will not close it; a self-created client is owned by the notifier and released on close.

### async def close

```python
async def close() -> None
```

Closes the underlying HTTP client.

**Notes**:

- Closes the HTTP client only when the notifier created (owns) it; if the client was externally injected, this method is a no-op. Safe to call from an async context.

### async def notify_update

```python
async def notify_update(user_id: str, lora_path: str) -> None
```

Notifies vLLM to hot-load the LoRA adapter for the specified user.

**Parameters**:

* **user_id**(str): User identifier, also used as vLLM's `lora_name`. After loading, requests specifying this `lora_name` automatically apply the new weights.
* **lora_path**(str): Absolute path to the LoRA weights directory.

**Notes**:

- Sends an HTTP POST to `{vllm_base_url}/v1/load_lora_adapter` with JSON body:

```json
{
  "lora_name": "<user_id>",
  "lora_path": "<lora_path>",
  "load_inplace": true
}
```

- Uses `self.timeout` as the request timeout.
- When the HTTP status code is `>= 400`, raises `RuntimeError` with message like `vLLM load_lora_adapter failed: status=<code>, body=<first 400 chars of body>`.
- On success, logs at INFO: `LoRA hot-loaded for user %s: %s`.

### async def unload

```python
async def unload(user_id: str) -> None
```

Unloads the LoRA adapter for the specified user (useful for cleaning up inactive users).

**Parameters**:

* **user_id**(str): The `lora_name` (user identifier) to unload.

**Notes**:

- Sends an HTTP POST to `{vllm_base_url}/v1/unload_lora_adapter` with JSON body `{"lora_name": "<user_id>"}`.
- Raises HTTP errors via `resp.raise_for_status()` (unlike `notify_update`'s custom `RuntimeError`).
- On success, logs at INFO: `LoRA unloaded for user %s`.

## Usage

`InferenceNotifier` is constructed or called in the following locations:

- [ppo_executor.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/scheduler/ppo_executor.py): `PPOTrainingExecutor` receives `Optional[InferenceNotifier]` in its constructor; `aclose()` calls `close()`; after a successful LoRA publish, calls `notify_update(user_id, published_lora_path)` (failures are treated as non-fatal warnings).
- [online_training_scheduler.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/scheduler/online_training_scheduler.py): Forwards `notifier` when constructing `PPOTrainingExecutor`.
- [services.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/launcher/services.py): `start_online_training_scheduler` constructs `InferenceNotifier(runtime.inference_url)` and passes it to the scheduler.
- [rl_optimizer.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/optimizer/rl_optimizer.py): `setup_inference(vllm_url)` stores the URL; at build time constructs `InferenceNotifier` and passes it to `OnlineTrainingScheduler`.
- [test_gateway_support.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/test_gateway_support.py): Test injects a fake `httpx.AsyncClient` to verify `notify_update`'s request path and body, and verifies that an external client is not closed by `close()`.
