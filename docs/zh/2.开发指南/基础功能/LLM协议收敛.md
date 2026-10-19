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
- `HuaweiMaasModelClient`（迁移方式不同，见下节）

生图 / 语音 / 视频：`client_provider="DashScope"`（或 `endpoint_profile="dashscope"`）时，由 `OpenAIModelClient` 提供 `generate_image` / `generate_speech` / `generate_video`。

## Huawei MaaS 与 x-span-id 链路追踪

`client_provider="HuaweiMaas"` 已随本次收敛删除，且不设别名：该配置会直接报错（unavailable model provider）。被删的 `HuaweiMaasModelClient` 唯一增量能力是给每个请求注入 `x-span-id`（uuid4 hex），供华为云 ModelArts MaaS 网关（如 `https://api.modelarts-maas.com/v1`）做请求级链路追踪。

迁移方式：直连 MaaS 网关改用 OpenAI 兼容配置，`x-span-id` 改在调用侧注入——每次调用生成新 span id：

```python
import uuid

msg = await model.invoke(messages, custom_headers={"x-span-id": uuid.uuid4().hex})
```

`custom_headers` 逐调用合并进请求头；固定头也可直接配在 `ModelClientConfig.custom_headers`。若希望对业务代码透明，可在宿主层包装客户端的 `invoke` / `stream`，按 `api_base` 判断 MaaS 端点后统一注入。
