# openjiuwen.core.foundation.llm

`openjiuwen.core.foundation.llm` 是 openJiuwen 中统一的**大模型调用与管理模块**，负责：

- 提供 `Model` 统一调用入口，根据 `client_provider` 选择协议实现（OpenAI 兼容或 Anthropic）；
- 定义 `BaseModelClient` 抽象基类及 `OpenAIModelClient`、`AnthropicModelClient` 实现；旧厂商名是别名，见[LLM 协议收敛](../../../基础功能/LLM协议收敛.md)；
- 提供模型请求/客户端配置（`ModelRequestConfig`、`ModelClientConfig`）及消息、流式块、工具调用等 Schema；
- 提供输出解析器抽象（`BaseOutputParser`）及 `JsonOutputParser`实现。

---

## class openjiuwen.core.foundation.llm.model.Model

```
class openjiuwen.core.foundation.llm.model.Model(model_client_config: ModelClientConfig, model_config: ModelRequestConfig = None)
```

统一的 LLM 调用入口：根据 `model_client_config.client_provider` 从注册表选择 ModelClient 实现并创建实例，将 `invoke`、`stream` 委托给该 ModelClient。

**参数**：

* **model_client_config**(ModelClientConfig)：客户端配置（api_key、api_base、client_provider、client_id 等）。若为 `None`，抛出 `JiuWenBaseException`。
* **model_config**(ModelRequestConfig，可选)：模型请求参数（model_name、temperature、top_p、max_tokens、stop 等）。默认值：`None`。

### async invoke(messages: Union[str, List[BaseMessage], List[dict]], *, tools: Union[List[ToolInfo], List[dict], None] = None, temperature: Optional[float] = None, top_p: Optional[float] = None, max_tokens: Optional[int] = None, stop: Union[Optional[str], None] = None, model: str = None, output_parser: Optional[BaseOutputParser] = None, timeout: float = None, **kwargs) -> AssistantMessage

异步调用 LLM，返回完整助手消息。

**参数**：

* **messages**(Union[str, List[BaseMessage], List[dict]])：输入内容；可为单字符串、BaseMessage 列表或已序列化的 dict 列表。
* **tools**(Union[List[ToolInfo], List[dict], None]，可选)：工具列表。默认值：`None`。
* **temperature**(float，可选)：采样温度，控制模型输出的随机性。取值范围为[0, 1]。默认值：`None`，使用 model_config 中的值。
* **top_p**(float，可选)：top_p 采样参数。取值范围为[0, 1]。默认值：`None`。
* **max_tokens**(int，可选)：最大生成 token 数。默认值：`None`。
* **stop**(str，可选)：停止序列。默认值：`None`。
* **model**(str，可选)：模型名，优先于 model_config 中的 model_name。默认值：`None`。
* **output_parser**(BaseOutputParser，可选)：输出解析器，用于解析模型返回的内容。默认值：`None`。
* **timeout**(float，可选)：请求超时，单位：秒。默认值：`None`。
* **kwargs**：可变参数，透传给 ModelClient。

**返回**：

**AssistantMessage**，助手回复消息，包含 content、tool_calls、usage_metadata 等字段信息。

**异常**：

* **BaseError**：异常，当配置错误或模型调用失败时抛出。

**样例**：

```python
>>> import os
>>> import asyncio
>>> from openjiuwen.core.foundation.llm import (
>>>     Model,
>>>     ModelRequestConfig,
>>>     ModelClientConfig,
>>>     UserMessage,
>>>     AssistantMessage,
>>> )
>>> 
>>> async def demo_invoke():
>>>     # 1. 构造配置与 Model
>>>     model_config = ModelRequestConfig(
>>>         model_name="your_model_name",
>>>         temperature=0.7,
>>>         max_tokens=1024,
>>>     )
>>>     client_config = ModelClientConfig(
>>>         client_id="my_llm",
>>>         client_provider="OpenAI",
>>>         api_key=os.getenv("OPENAI_API_KEY", "your_api_key"),
>>>         api_base=os.getenv("OPENAI_API_BASE", "your_api_base"),
>>>     )
>>>     model = Model(model_client_config=client_config, model_config=model_config)
>>> 
>>>     # 2. 字符串输入调用
>>>     response: AssistantMessage = await model.invoke("你好，请用一句话介绍你自己。")
>>>     print(response.content)
>>> 
>>> asyncio.run(demo_invoke())
你好！我是一个AI助手，可以帮助你解答问题和完成各种任务。
```

### async stream(messages: Union[str, List[BaseMessage], List[dict]], *, tools: Union[List[ToolInfo], List[dict], None] = None, temperature: Optional[float] = None, top_p: Optional[float] = None, max_tokens: Optional[int] = None, stop: Union[Optional[str], None] = None, model: str = None, output_parser: Optional[BaseOutputParser] = None, timeout: float = None, **kwargs) -> AsyncIterator[AssistantMessageChunk]

异步流式调用 LLM，逐块返回助手消息。

**参数**：

* **messages**(Union[str, List[BaseMessage], List[dict]])：输入内容；可为单字符串、BaseMessage 列表或已序列化的 dict 列表。
* **tools**(Union[List[ToolInfo], List[dict], None]，可选)：工具列表。默认值：`None`。
* **temperature**(float，可选)：采样温度，控制模型输出的随机性。取值范围为[0, 1]。默认值：`None`，使用 model_config 中的值。
* **top_p**(float，可选)：top_p 采样参数。取值范围为[0, 1]。默认值：`None`。
* **max_tokens**(int，可选)：最大生成 token 数。默认值：`None`。
* **stop**(str，可选)：停止序列。默认值：`None`。
* **model**(str，可选)：模型名，优先于 model_config 中的 model_name。默认值：`None`。
* **output_parser**(BaseOutputParser，可选)：输出解析器，用于解析模型返回的内容。默认值：`None`。
* **timeout**(float，可选)：请求超时，单位：秒。默认值：`None`。
* **kwargs**：可变参数，透传给 ModelClient。

**返回**：

**AsyncIterator[AssistantMessageChunk]**，异步迭代器，每次 yield 一个 `AssistantMessageChunk`。

**异常**：

* **BaseError**：异常，当配置错误或模型调用失败时抛出。

**样例**：

```python
```python
>>> import os
>>> import asyncio
>>> from openjiuwen.core.foundation.llm import (
>>>     Model,
>>>     ModelRequestConfig,
>>>     ModelClientConfig,
>>>     UserMessage,
>>>     AssistantMessage,
>>> )
>>> 
>>> async def demo_stream():
>>>     # 1. 构造配置与 Model
>>>     model_config = ModelRequestConfig(
>>>         model_name="your_model_name",
>>>         temperature=0.7,
>>>         max_tokens=1024,
>>>     )
>>>     client_config = ModelClientConfig(
>>>         client_id="my_llm",
>>>         client_provider="OpenAI",
>>>         api_key=os.getenv("OPENAI_API_KEY", "your_api_key"),
>>>         api_base=os.getenv("OPENAI_API_BASE", "your_api_base"),
>>>     )
>>>     model = Model(model_client_config=client_config, model_config=model_config)
>>> 
>>>     # 2. 字符串输入调用
>>>     response: AssistantMessage = await model.stream("你好")
>>>     print(response.content)
>>> 
>>> asyncio.run(demo_stream())
你好
！有什么
我可以帮你的
吗？```
```

### async generate_image(...) -> ImageGenerationResponse

生成图像（文本生图或图生图）。**注意**：仅在 `client_provider="DashScope"` 或 `endpoint_profile="dashscope"` 时可用，由 `OpenAIModelClient` 实现。

### async generate_speech(...) -> AudioGenerationResponse

生成语音（文本转语音）。**注意**：仅在 DashScope 别名 / `endpoint_profile="dashscope"` 时可用。

### async generate_video(...) -> VideoGenerationResponse

生成视频（文本生视频或图生视频）。**注意**：仅在 DashScope 别名 / `endpoint_profile="dashscope"` 时可用。

---

## class openjiuwen.core.foundation.llm.model_clients.base_model_client.BaseModelClient

```
class openjiuwen.core.foundation.llm.model_clients.base_model_client.BaseModelClient(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

大模型客户端抽象基类，所有 ModelClient 实现需继承此类并实现 `invoke`、`stream` 等抽象方法；提供消息/工具转 dict、请求参数构建等辅助方法。开发者可基于此类扩展自定义的模型客户端。

**参数**：

* **model_config**(ModelRequestConfig)：模型请求参数（temperature、top_p、model_name 等）。
* **model_client_config**(ModelClientConfig)：客户端配置（api_key、api_base、timeout、verify_ssl、ssl_cert 等）。

### abstractmethod async invoke(messages: Union[str, List[BaseMessage], List[dict]], *, tools: Union[List[ToolInfo], List[dict], None] = None, temperature: Optional[float] = None, top_p: Optional[float] = None, model: str = None, max_tokens: Optional[int] = None, stop: Union[Optional[str], None] = None, output_parser: Optional[BaseOutputParser] = None, timeout: float = None, **kwargs) -> AssistantMessage

异步调用 LLM，返回单条完整助手消息。子类必须实现此方法。

**参数**：

* **messages**(Union[str, List[BaseMessage], List[dict]])：输入内容；可为单字符串、BaseMessage 列表或已序列化的 dict 列表。
* **tools**(Union[List[ToolInfo], List[dict], None]，可选)：工具列表。默认值：`None`。
* **temperature**(float，可选)：采样温度，控制模型输出的随机性。取值范围为[0, 1]。默认值：`None`，使用 model_config 中的值。
* **top_p**(float，可选)：top_p 采样参数。取值范围为[0, 1]。默认值：`None`。
* **max_tokens**(int，可选)：最大生成 token 数。默认值：`None`。
* **stop**(str，可选)：停止序列。默认值：`None`。
* **model**(str，可选)：模型名，优先于 model_config 中的 model_name。默认值：`None`。
* **output_parser**(BaseOutputParser，可选)：输出解析器，用于解析模型返回的内容。默认值：`None`。
* **timeout**(float，可选)：请求超时，单位：秒。默认值：`None`。
* **kwargs**：可变参数，透传给 ModelClient。

**返回**：

**AssistantMessage**，模型响应消息。

### abstractmethod async stream(messages: Union[str, List[BaseMessage], List[dict]], *, tools: Union[List[ToolInfo], List[dict], None] = None, temperature: Optional[float] = None, top_p: Optional[float] = None, model: str = None, max_tokens: Optional[int] = None, stop: Union[Optional[str], None] = None, output_parser: Optional[BaseOutputParser] = None, timeout: float = None, **kwargs) -> AsyncIterator[AssistantMessageChunk]

异步流式调用 LLM，逐块返回助手消息。子类必须实现此方法。

**参数**：

* **messages**(Union[str, List[BaseMessage], List[dict]])：输入内容；可为单字符串、BaseMessage 列表或已序列化的 dict 列表。
* **tools**(Union[List[ToolInfo], List[dict], None]，可选)：工具列表。默认值：`None`。
* **temperature**(float，可选)：采样温度，控制模型输出的随机性。取值范围为[0, 1]。默认值：`None`，使用 model_config 中的值。
* **top_p**(float，可选)：top_p 采样参数。取值范围为[0, 1]。默认值：`None`。
* **max_tokens**(int，可选)：最大生成 token 数。默认值：`None`。
* **stop**(str，可选)：停止序列。默认值：`None`。
* **model**(str，可选)：模型名，优先于 model_config 中的 model_name。默认值：`None`。
* **output_parser**(BaseOutputParser，可选)：输出解析器，用于解析模型返回的内容。默认值：`None`。
* **timeout**(float，可选)：请求超时，单位：秒。默认值：`None`。
* **kwargs**：可变参数，透传给 ModelClient。

**返回**：

**AsyncIterator[AssistantMessageChunk]**，流式响应块的异步迭代器。

---

## class openjiuwen.core.foundation.llm.model_clients.openai_model_client.OpenAIModelClient

```
class openjiuwen.core.foundation.llm.model_clients.openai_model_client.OpenAIModelClient(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

继承 `BaseModelClient`，对接兼容OpenAI格式的API，使用 `ModelClientConfig` 的 api_key、api_base、timeout、verify_ssl、ssl_cert 等；实现 `invoke`、`stream`，支持 tool_calls、output_parser。

**参数**：

* **model_config**(ModelRequestConfig)：模型请求参数。
* **model_client_config**(ModelClientConfig)：客户端配置。

---

## SiliconFlowModelClient（已删除）

请使用 `Model` + `client_provider="SiliconFlow"`。实现为 `OpenAIModelClient` 别名映射。

---

## class openjiuwen.core.foundation.llm.model_clients.openai_account_model_client.OpenAIAccountModelClient

```
class openjiuwen.core.foundation.llm.model_clients.openai_account_model_client.OpenAIAccountModelClient(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

继承 `BaseModelClient`，对接 OpenAI 账户后端（`chatgpt.com/backend-api/codex`）。该 provider 不要求在 `ModelClientConfig` 中配置 `api_key`，而是在请求时通过 `OpenAIAccountAuthManager` 从本地 OAuth 凭据存储中解析访问令牌，并在令牌过期时自动刷新；实现 `invoke`、`stream`，支持 tool_calls、output_parser。

**参数**：

* **model_config**(ModelRequestConfig)：模型请求参数。
* **model_client_config**(ModelClientConfig)：客户端配置，`client_provider` 需为 `"OpenAIAccount"`，`api_key` 可省略。

---

## DashScopeModelClient（已删除）

请使用 `Model` + `client_provider="DashScope"`。多模态接口由 `OpenAIModelClient` 在 DashScope 别名下提供。

---
## class openjiuwen.core.foundation.llm.schema.config.ProviderType

定义了支持的模型服务商类型。

* **OpenAI**：表示 OpenAI 服务商。
* **OpenAIAccount**：表示使用 OpenAI 账户 OAuth 凭据接入的服务商，无需 `api_key`。
* **SiliconFlow**：表示 SiliconFlow 服务商。

---

## class openjiuwen.core.foundation.llm.schema.config.ModelClientConfig

客户端配置数据类。

* **client_id**(str)：客户端唯一标识，用于在 Runner 中注册。默认值：由 `uuid.uuid4()` 自动生成。
* **client_provider**(Union[ProviderType, str])：服务商标识。枚举值：`OpenAI`、`OpenAIAccount`、`SiliconFlow`。
* **api_key**(str)：API 密钥。当 `client_provider` 为 `OpenAIAccount` 时可省略（默认值：`""`），其余内置 provider 均为必填。
* **api_base**(str)：API 基础 URL。对所有内置 provider（含 `OpenAIAccount`）均为必填，不会自动填充默认值。
* **timeout**(float)：请求超时，单位：秒。取值范围大于 0。默认值：`60.0`。
* **max_retries**(int)：最大重试次数。默认值：`3`。
* **verify_ssl**(bool)：是否验证 SSL 证书。默认值：`True`。
* **ssl_cert**(str，可选)：SSL 证书路径。当 `verify_ssl` 为 `True` 时必填。默认值：`None`。

---

## class openjiuwen.core.foundation.llm.schema.config.ModelRequestConfig

单次请求的模型参数配置数据类。

* **model_name**(str)：模型名称（别名 `model`）。默认值：`""`。
* **temperature**(float)：温度参数，控制输出的随机性。取值范围为[0, 1]。默认值：`0.95`。
* **top_p**(float)：top_p 采样参数。取值范围为[0, 1]。默认值：`0.95`。
* **max_tokens**(int，可选)：最大生成 token 数。默认值：`None`。
* **stop**(str，可选)：停止序列。默认值：`None`。

---

## class openjiuwen.core.foundation.llm.schema.message.BaseMessage

消息基类。

* **role**(str)：消息角色。
* **content**(Union[str, List[Union[str, dict]]])：消息内容。默认值：`""`。
* **name**(str，可选)：消息发送者名称。默认值：`None`。

---

## class openjiuwen.core.foundation.llm.schema.message.UserMessage

用户消息，继承自 `BaseMessage`。

* **role**(str)：固定为 `"user"`。

---

## class openjiuwen.core.foundation.llm.schema.message.SystemMessage

系统消息，继承自 `BaseMessage`。

* **role**(str)：固定为 `"system"`。

---

## class openjiuwen.core.foundation.llm.schema.message.AssistantMessage

助手消息，继承自 `BaseMessage`。

* **role**(str)：固定为 `"assistant"`。
* **tool_calls**(List[ToolCall]，可选)：工具调用列表。默认值：`None`。
* **usage_metadata**(UsageMetadata，可选)：用量元数据。默认值：`None`。
* **finish_reason**(str)：完成原因，可为 `"stop"` 或 `"tool_calls"`。默认值：`"null"`。
* **parser_content**(Any，可选)：经解析器处理后的内容。默认值：`None`。
* **reasoning_content**(str，可选)：推理内容（部分模型支持）。默认值：`None`。

---

## class openjiuwen.core.foundation.llm.schema.message.ToolMessage

工具消息，继承自 `BaseMessage`。

* **role**(str)：固定为 `"tool"`。
* **tool_call_id**(str)：对应的工具调用 ID。

---

## class openjiuwen.core.foundation.llm.schema.message.UsageMetadata

元数据。

* **code**(int)：响应状态码。默认值：`0`。
* **err_msg**(str)：错误消息。默认值：`""`。
* **prompt**(str)：提示词。默认值：`""`。
* **task_id**(str)：任务 ID。默认值：`""`。
* **model_name**(str)：模型名称。默认值：`""`。
* **total_latency**(float)：总延迟时间。默认值：`0.0`。
* **first_token_time**(str)：首 token 时间。默认值：`""`。
* **request_start_time**(str)：请求开始时间。默认值：`""`。
* **input_tokens**(int)：输入 token 数。默认值：`0`。
* **output_tokens**(int)：输出 token 数。默认值：`0`。
* **total_tokens**(int)：总 token 数。默认值：`0`。
* **cache_tokens**(int)：缓存 token 数。默认值：`0`。

---

## class openjiuwen.core.foundation.llm.schema.message_chunk.BaseMessageChunk

消息块基类，继承自 `BaseMessage`，支持合并内容操作（字符串或列表拼接）。

---

## class openjiuwen.core.foundation.llm.schema.message_chunk.AssistantMessageChunk

助手消息的流式块，继承自 `AssistantMessage` 和 `BaseMessageChunk`。支持与另一 `AssistantMessageChunk` 合并（content、tool_calls 按 id 合并片段，usage_metadata、finish_reason 等取后者或合并）。

---

## class openjiuwen.core.foundation.llm.schema.tool_call.ToolCall

单次工具调用数据类。

* **id**(str，可选)：工具调用 ID。
* **type**(str)：工具调用类型。
* **name**(str)：工具名称。
* **arguments**(str)：工具参数（JSON 字符串）。
* **index**(int，可选)：工具调用索引，用于区分多次工具调用。默认值：`None`。
* **response_item_id**(str，可选)：部分协议中用于区分响应条目 ID 与工具调用 ID 的厂商响应条目 ID。默认值：`None`。

---

## class openjiuwen.core.foundation.llm.schema.generation_response.GenerationResponse

```
class openjiuwen.core.foundation.llm.schema.generation_response.GenerationResponse()
```

生成响应基类，所有生成类响应（图像、语音、视频）的父类。

**字段**：

* **model**(str，可选)：使用的模型名称。默认值：`None`。

---

## class openjiuwen.core.foundation.llm.schema.generation_response.ImageGenerationResponse

```
class openjiuwen.core.foundation.llm.schema.generation_response.ImageGenerationResponse()
```

图像生成响应类，继承自`GenerationResponse`。用于返回图像生成API的结果。

**字段**：

* **images**(List[str])：生成的图片URL列表。默认值：`None`。
* **images_base64**(List[str])：Base64编码的图片列表。默认值：`None`。
* **created**(int，可选)：创建时间戳。默认值：`None`。

**配置**：

* **model_config**：`ConfigDict(arbitrary_types_allowed=True)`，允许使用任意类型（如bytes等非标准类型）。

**样例**：

```python
>>> from openjiuwen.core.foundation.llm.schema.generation_response import ImageGenerationResponse
>>>
>>> response = ImageGenerationResponse(
>>>     model="qwen-image-max",
>>>     images=["https://example.com/image1.jpg"],
>>>     created=1704038400
>>> )
>>> print(response.model)
qwen-image-max
>>> print(response.images[0])
https://example.com/image1.jpg
```

---

## class openjiuwen.core.foundation.llm.schema.generation_response.AudioGenerationResponse

```
class openjiuwen.core.foundation.llm.schema.generation_response.AudioGenerationResponse()
```

音频/语音生成响应类，继承自`GenerationResponse`。用于返回语音合成API的结果。

**字段**：

* **audio_url**(str，可选)：生成的音频URL。默认值：`None`。
* **audio_data**(bytes，可选)：音频二进制数据。默认值：`None`。
* **duration**(float，可选)：音频时长（秒）。默认值：`None`。
* **format**(str，可选)：音频格式（如`"mp3"`、`"wav"`等）。默认值：`"mp3"`。

**配置**：

* **model_config**：`ConfigDict(arbitrary_types_allowed=True)`，允许使用任意类型（如bytes等非标准类型）。

**样例**：

```python
>>> from openjiuwen.core.foundation.llm.schema.generation_response import AudioGenerationResponse
>>>
>>> response = AudioGenerationResponse(
>>>     model="qwen3-tts-flash",
>>>     audio_url="https://example.com/audio1.mp3",
>>>     duration=3.5,
>>>     format="mp3"
>>> )
>>> print(response.duration)
3.5
>>> print(response.format)
mp3
```

---

## class openjiuwen.core.foundation.llm.schema.generation_response.VideoGenerationResponse

```
class openjiuwen.core.foundation.llm.schema.generation_response.VideoGenerationResponse()
```

视频生成响应类，继承自`GenerationResponse`。用于返回视频生成API的结果。

**字段**：

* **video_url**(str，可选)：生成的视频URL。默认值：`None`。
* **video_data**(bytes，可选)：视频二进制数据。默认值：`None`。
* **duration**(float，可选)：视频时长（秒）。默认值：`None`。
* **resolution**(str，可选)：视频分辨率（如`"1920x1080"`）。默认值：`None`。
* **format**(str，可选)：视频格式（如`"mp4"`、`"avi"`等）。默认值：`"mp4"`。

**配置**：

* **model_config**：`ConfigDict(arbitrary_types_allowed=True)`，允许使用任意类型（如bytes等非标准类型）。

**样例**：

```python
>>> from openjiuwen.core.foundation.llm.schema.generation_response import VideoGenerationResponse
>>>
>>> response = VideoGenerationResponse(
>>>     model="wan2.6-t2v",
>>>     video_url="https://example.com/video1.mp4",
>>>     duration=5.0,
>>>     resolution="1280*720",
>>>     format="mp4"
>>> )
>>> print(response.duration)
5.0
>>> print(response.resolution)
1280*720
```

---

## class openjiuwen.core.foundation.llm.model_clients.anthropic_model_client.AnthropicModelClient

```
class openjiuwen.core.foundation.llm.model_clients.anthropic_model_client.AnthropicModelClient(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

继承 `BaseModelClient`，对接 Anthropic Messages API（Claude 系列模型）。实现 `invoke`、`stream`，支持 tool_calls、output_parser 与提示词缓存（`cache_control`）。

该客户端维护进程级 `AsyncAnthropic` 客户端缓存池，按 `api_key`、`api_base`、`verify_ssl`、`ssl_cert` 分桶复用 httpx 长连接；可通过 `connection_key` 计算连接标识，通过 `aclose` / `aclose_connections` 关闭连接池。`_normalize_base_url` 会自动剥离 `api_base` 末尾的 `/v1`，避免与 SDK 自动追加的 `/v1/messages` 重复拼接。

**参数**：

* **model_config**(ModelRequestConfig)：模型请求参数。
* **model_client_config**(ModelClientConfig)：客户端配置，`client_provider` 需为 `"Anthropic"`。

---

## 已删除的厂商客户端

`DeepSeekModelClient`、`AscendAffinityModelClient`、`InferenceAffinityModelClient`、`OpenRouterModelClient` 已删除。请使用 `Model` + 对应 `client_provider` 字符串。

---
## class openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.IntelliRouterModelClient

```
class openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.IntelliRouterModelClient(model_config: ModelRequestConfig, model_client_config: ModelClientConfig, router: Optional[ReliableRouter] = None)
```

继承 `BaseModelClient`，封装 `intelli_router.ReliableRouter`，提供 API 池化、智能路由、自动重试、流式输出与多模态生成（图像/语音/视频）。实现 `invoke`、`stream`；`generate_image` / `generate_speech` / `generate_video` 会解析 provider 并委托 DashScope 执行。

**参数**：

* **model_config**(ModelRequestConfig)：模型请求参数。
* **model_client_config**(ModelClientConfig)：客户端配置，`client_provider` 需为 `"intelli_router"`。路由相关参数从 `__pydantic_extra__` 提取（见下 `IntelliRouterClientConfig`）。
* **router**(ReliableRouter，可选)：外部传入已构造的 `ReliableRouter` 实例；未传时按配置自动从模块级缓存获取或创建。

> **依赖**：需安装 `intelli-router` 包，否则构造时抛出 `ModelError`（`MODEL_SERVICE_CONFIG_ERROR`）。

---

## class openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.IntelliRouterClientConfig

```
class IntelliRouterClientConfig(deployments: list[dict] = [], strategy: str = "simple-shuffle", num_retries: int = 3, timeout: float = 30.0, strategy_kwargs: dict = {}, enable_health_check: bool = False, health_check_interval: float = 300.0, verify_ssl: bool = True, enable_observability: bool = False, web_dashboard_port: int = 0)
```

从 `ModelClientConfig` 抽取的 IntelliRouter 类型化配置（`@dataclass`）。通过 `from_model_client_config(config)` 类方法从 `ModelClientConfig.__pydantic_extra__` 读取以下字段：

* `intelli_router_deployments`：部署列表，每个部署含 `id`、`model_name`、`api_key`、`api_base`、`provider`、`tpm`、`rpm` 等。
* `intelli_router_strategy`(默认 `"simple-shuffle"`)、`intelli_router_strategy_kwargs`：路由策略及其参数。
* `intelli_router_num_retries`(默认 `3`)、`intelli_router_timeout`(默认 `30.0`)：重试次数与超时。
* `intelli_router_enable_health_check`(默认 `False`)、`intelli_router_health_check_interval`(默认 `300.0`)：健康检查开关与间隔。
* `intelli_router_enable_observability`(默认 `False`)、`intelli_router_web_dashboard_port`(默认 `0`)：可观测性与 Web 仪表盘端口。

---

## class openjiuwen.core.foundation.llm.output_parsers.output_parser.BaseOutputParser

输出解析器抽象基类。开发者可基于此类实现自定义输出解析器。

### abstractmethod async parse(inputs) -> Any

异步解析 LLM 输出。

**参数**：

* **inputs**：AssistantMessage 或其 content 字符串。

**返回**：

解析后的结果。

### abstractmethod async stream_parse(streaming_inputs: AsyncIterator) -> AsyncIterator[Any]

异步流式解析 LLM 输出。

**参数**：

* **streaming_inputs**(AsyncIterator)：AsyncIterator[AssistantMessageChunk] 流式输入。

**返回**：

**AsyncIterator[Any]**，解析结果片段的异步迭代器。

---

## class openjiuwen.core.foundation.llm.output_parsers.json_output_parser.JsonOutputParser

JSON 输出解析器，继承自 `BaseOutputParser`。从 AssistantMessage 或字符串中提取 `` ```json ... ``` `` 代码块并解析为 JSON 对象；支持流式解析。

### async parse(llm_output: Union[str, AssistantMessage]) -> Any

解析 LLM 输出中的 JSON 内容。

**参数**：

* **llm_output**(Union[str, AssistantMessage])：LLM 输出，可为字符串或 AssistantMessage。

**返回**：

解析后的 JSON 对象，解析失败返回 `None`。

### async stream_parse(streaming_inputs: AsyncIterator[Union[str, AssistantMessageChunk]]) -> AsyncIterator[Optional[Dict[str, Any]]]

流式解析 JSON 内容。

**参数**：

* **streaming_inputs**(AsyncIterator)：流式输入。

**返回**：

**AsyncIterator[Optional[Dict[str, Any]]]**，解析结果的异步迭代器。

---

> **说明**：`model_client_config` 不可为 `None`；`client_provider` 当前支持 `"OpenAI"`、`"OpenAIAccount"`、`"SiliconFlow"`、`"DashScope"`、`"Anthropic"`、`"DeepSeek"`、`"AscendAffinity"`、`"InferenceAffinity"`、`"OpenRouter"`、`"intelli_router"`，其他值会抛出异常并提示支持的类型。未传的 `temperature`、`top_p`、`max_tokens`、`stop` 等将使用 `model_config` 中的默认值。
