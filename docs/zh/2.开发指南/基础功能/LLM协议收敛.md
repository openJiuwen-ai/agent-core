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

生图 / 语音 / 视频：`client_provider="DashScope"`（或 `endpoint_profile="dashscope"`）时，由 `OpenAIModelClient` 提供 `generate_image` / `generate_speech` / `generate_video`。

## `api_base` 语义（迁移注意）

OpenAI 兼容实现把 `api_base` **原样**交给 OpenAI SDK（SDK 自行在末尾拼接 `/chat/completions`），不会追加 `/v1`。网关自定义路径（如 `https://gw.example.com/llm`）或带 query 的地址都能按原样生效。

只有走原始 HTTP 网关路径的场景（`endpoint_profile` 为 `ascend_affinity` / `inference_affinity` 的 KV affinity 流）才沿用旧 AscendAffinity/InferenceAffinity 客户端的归一化规则：自动补 `/v1`，并容忍 `/v1/chat/completions` 结尾的写法。

**从旧版本迁移**：如果原来依赖"随便填个 host、框架自动补 `/v1`"的行为，请把 `api_base` 写全（例如 `https://host/v1`），否则请求会打到错误路径。
