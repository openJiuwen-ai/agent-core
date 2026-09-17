# LLM 协议收敛

协议实现只保留两套：

- OpenAI 兼容（`OpenAIModelClient`）
- Anthropic（`AnthropicModelClient`）

`OpenAIAccount` 仍走独立 OAuth 客户端；`IntelliRouter` 仍是路由封装。它们不是第三、第四套厂商协议。

## 旧 `client_provider` 怎么用

配置里仍可写 `DeepSeek`、`OpenRouter`、`SiliconFlow`、`DashScope`、`AscendAffinity`。创建客户端时，框架把它们映射为 OpenAI 兼容实现，并带上对应的 `endpoint_profile` / `extensions`（例如 DashScope 多模态、Ascend KV affinity）。

推荐写法：

```python
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

model = Model(
    model_client_config=ModelClientConfig(
        client_provider="DashScope",  # 或 DeepSeek / OpenRouter / SiliconFlow / AscendAffinity ...
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-...",
    ),
    model_config=ModelRequestConfig(model="qwen-plus"),
)
```

`Model.model_client_config.client_provider` 仍是你写下的旧名字。内部 client 上的 provider 会归一成 `OpenAI`，旧名字在 `legacy_client_provider`。对外请认 `Model` 上的名字。

## 不要再 import 已删的客户端类

下列类和模块已删除，请改用上面的 `Model` + 字符串 `client_provider`：

- `DashScopeModelClient`
- `DeepSeekModelClient`
- `OpenRouterModelClient`
- `SiliconFlowModelClient`
- `InferenceAffinityModelClient`
- `AscendAffinityModelClient`

生图 / 语音 / 视频：`client_provider="DashScope"`（或 `endpoint_profile="dashscope"`）时，由 `OpenAIModelClient` 提供 `generate_image` / `generate_speech` / `generate_video`。
