# LLM Protocol Consolidation

Protocol implementations are reduced to two:

- OpenAI-compatible (`OpenAIModelClient`)
- Anthropic (`AnthropicModelClient`)

`OpenAIAccount` remains a separate OAuth client. `IntelliRouter` remains a routing wrapper. Neither is a third vendor protocol.

## How legacy `client_provider` values work

You can still set `DeepSeek`, `OpenRouter`, `SiliconFlow`, `DashScope`, `InferenceAffinity`, or `AscendAffinity`. When a client is created, the framework maps those names onto the OpenAI-compatible implementation and attaches the matching `endpoint_profile` / `extensions` (DashScope multimodal, Ascend KV affinity, and so on).

Recommended usage:

```python
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

model = Model(
    model_client_config=ModelClientConfig(
        client_provider="DashScope",  # or DeepSeek / OpenRouter / SiliconFlow / AscendAffinity ...
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-...",
    ),
    model_config=ModelRequestConfig(model="qwen-plus"),
)
```

`Model.model_client_config.client_provider` keeps the name you configured. The inner client provider is normalized to `OpenAI`; the original value is stored on `legacy_client_provider`. Treat the `Model` field as the public name.

## Do not import removed client classes

These classes and modules are gone. Use `Model` plus a string `client_provider` instead:

- `DashScopeModelClient`
- `DeepSeekModelClient`
- `OpenRouterModelClient`
- `SiliconFlowModelClient`
- `InferenceAffinityModelClient`
- `AscendAffinityModelClient`

Image / speech / video generation is served by `OpenAIModelClient` when `client_provider="DashScope"` (or `endpoint_profile="dashscope"`).
