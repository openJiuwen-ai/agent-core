# LLM 协议收敛

协议实现只保留两套：

- OpenAI 兼容（`OpenAIModelClient`）
- Anthropic（`AnthropicModelClient`）

`OpenAIAccount` 仍走独立 OAuth 客户端；`IntelliRouter` 仍是路由封装。它们不是第三、第四套厂商协议。

## 旧 `client_provider` 怎么用

配置里仍可写 `DeepSeek`、`OpenRouter`、`SiliconFlow`、`DashScope`、`InferenceAffinity`、`AscendAffinity`。创建客户端时，框架把它们映射为 OpenAI 兼容实现，并带上对应的 `endpoint_profile` / `extensions`（例如 DashScope 多模态、Ascend KV affinity）。

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
- `HuaweiMaasModelClient`（能力经 `maas` endpoint profile 保留，见下节）

生图 / 语音 / 视频：`client_provider="DashScope"`（或 `endpoint_profile="dashscope"`）时，由 `OpenAIModelClient` 提供 `generate_image` / `generate_speech` / `generate_video`。

## Huawei MaaS 与 x-span-id 链路追踪

`HuaweiMaasModelClient` 类已删除，但其能力保留：`client_provider="HuaweiMaas"` 作为 legacy 别名收敛到 OpenAI 兼容客户端，并挂载 `maas` endpoint profile——该 profile 给每个请求自动注入 `x-span-id`（逐请求生成的 uuid4 hex），供华为云 ModelArts MaaS 网关（如 `https://api.modelarts-maas.com/v1`）做请求级链路追踪：

```python
model = Model(
    model_client_config=ModelClientConfig(
        client_provider="HuaweiMaas",  # 或 client_provider="OpenAI", endpoint_profile="maas"
        api_base="https://api.modelarts-maas.com/v1",
        api_key="sk-...",
    ),
    model_config=ModelRequestConfig(model="..."),
)
```

业务代码零改动：调用侧无需为链路追踪传 `custom_headers`。若某次调用需要自定义 span id，可传 `custom_headers={"x-span-id": ...}`——注意 profile transform 在最后生效，会覆盖请求级 `x-span-id`，与原客户端行为一致。注入逻辑位于 `maas` profile 声明的 `inject_x_span_id` request header transform，其他 OpenAI 兼容端点不受影响。
