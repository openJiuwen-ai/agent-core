# openjiuwen.core.foundation.llm

`openjiuwen.core.foundation.llm` is the unified **large model invocation and management module** in openJiuwen, responsible for:

- Providing `Model` as the unified entry; `client_provider` selects the OpenAI-compatible or Anthropic protocol;
- Defining `BaseModelClient` plus `OpenAIModelClient` and `AnthropicModelClient`; legacy vendor names are aliases, see [LLM Protocol Consolidation](../../../Basic%20Functions/LLM%20Protocol%20Consolidation.md);
- Providing model request/client configuration (`ModelRequestConfig`, `ModelClientConfig`) and schemas for messages, streaming chunks, tool calls, etc.;
- Providing output parser abstraction (`BaseOutputParser`) and `JsonOutputParser` implementation.

---

## class openjiuwen.core.foundation.llm.model.Model

```
class openjiuwen.core.foundation.llm.model.Model(model_client_config: ModelClientConfig, model_config: ModelRequestConfig = None)
```

Unified LLM invocation entry point: selects ModelClient implementation from registry based on `model_client_config.client_provider` and creates an instance, delegating `invoke` and `stream` to that ModelClient.

**Parameters**:

* **model_client_config** (ModelClientConfig): Client configuration (api_key, api_base, client_provider, client_id, etc.). If `None`, raises `JiuWenBaseException`.
* **model_config** (ModelRequestConfig, optional): Model request parameters (model_name, temperature, top_p, max_tokens, stop, etc.). Default value: `None`.

### async invoke(messages: Union[str, List[BaseMessage], List[dict]], *, tools: Union[List[ToolInfo], List[dict], None] = None, temperature: Optional[float] = None, top_p: Optional[float] = None, max_tokens: Optional[int] = None, stop: Union[Optional[str], None] = None, model: str = None, output_parser: Optional[BaseOutputParser] = None, timeout: float = None, **kwargs) -> AssistantMessage

Asynchronously invoke LLM, returning complete assistant message.

**Parameters**:

* **messages** (Union[str, List[BaseMessage], List[dict]]): Input content; can be a single string, BaseMessage list, or serialized dict list.
* **tools** (Union[List[ToolInfo], List[dict], None], optional): Tool list. Default value: `None`.
* **temperature** (float, optional): Sampling temperature, controlling randomness of model output. Value range [0, 1]. Default value: `None`, uses value from model_config.
* **top_p** (float, optional): top_p sampling parameter. Value range [0, 1]. Default value: `None`.
* **max_tokens** (int, optional): Maximum number of tokens to generate. Default value: `None`.
* **stop** (str, optional): Stop sequence. Default value: `None`.
* **model** (str, optional): Model name, takes precedence over model_name in model_config. Default value: `None`.
* **output_parser** (BaseOutputParser, optional): Output parser for parsing model-returned content. Default value: `None`.
* **timeout** (float, optional): Request timeout in seconds. Default value: `None`.
* **kwargs**: Variadic parameters, passed through to ModelClient.

**Returns**:

**AssistantMessage**, assistant reply message, containing fields such as content, tool_calls, usage_metadata.

**Exceptions**:

* **BaseError**: Exception raised when configuration error or model invocation fails.

**Example**:

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
>>>     # 1. Construct configuration and Model
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
>>>     # 2. String input invocation
>>>     response: AssistantMessage = await model.invoke("你好，请用一句话介绍你自己。")
>>>     print(response.content)
>>> 
>>> asyncio.run(demo_invoke())
你好！我是一个AI助手，可以帮助你解答问题和完成各种任务。
```

### async stream(messages: Union[str, List[BaseMessage], List[dict]], *, tools: Union[List[ToolInfo], List[dict], None] = None, temperature: Optional[float] = None, top_p: Optional[float] = None, max_tokens: Optional[int] = None, stop: Union[Optional[str], None] = None, model: str = None, output_parser: Optional[BaseOutputParser] = None, timeout: float = None, **kwargs) -> AsyncIterator[AssistantMessageChunk]

Asynchronously stream LLM invocation, returning assistant messages chunk by chunk.

**Parameters**:

* **messages** (Union[str, List[BaseMessage], List[dict]]): Input content; can be a single string, BaseMessage list, or serialized dict list.
* **tools** (Union[List[ToolInfo], List[dict], None], optional): Tool list. Default value: `None`.
* **temperature** (float, optional): Sampling temperature, controlling randomness of model output. Value range [0, 1]. Default value: `None`, uses value from model_config.
* **top_p** (float, optional): top_p sampling parameter. Value range [0, 1]. Default value: `None`.
* **max_tokens** (int, optional): Maximum number of tokens to generate. Default value: `None`.
* **stop** (str, optional): Stop sequence. Default value: `None`.
* **model** (str, optional): Model name, takes precedence over model_name in model_config. Default value: `None`.
* **output_parser** (BaseOutputParser, optional): Output parser for parsing model-returned content. Default value: `None`.
* **timeout** (float, optional): Request timeout in seconds. Default value: `None`.
* **kwargs**: Variadic parameters, passed through to ModelClient.

**Returns**:

**AsyncIterator[AssistantMessageChunk]**, async iterator, yields one `AssistantMessageChunk` each time.

**Exceptions**:

* **BaseError**: Exception raised when configuration error or model invocation fails.

**Example**:

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
>>>     # 1. Construct configuration and Model
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
>>>     # 2. String input invocation
>>>     async for chunk in model.stream("你好"):
>>>         print(chunk.content, end="")
>>> 
>>> asyncio.run(demo_stream())
你好！有什么我可以帮你的吗？
```

### async generate_image(...) -> ImageGenerationResponse

Generate images (text-to-image or image-to-image). **Note**: Available when `client_provider="DashScope"` or `endpoint_profile="dashscope"`, implemented by `OpenAIModelClient`.

### async generate_speech(...) -> AudioGenerationResponse

Generate speech (text-to-speech). **Note**: Available under the DashScope alias / `endpoint_profile="dashscope"`.

### async generate_video(...) -> VideoGenerationResponse

Generate videos (text-to-video or image-to-video). **Note**: Available under the DashScope alias / `endpoint_profile="dashscope"`.

---

## class openjiuwen.core.foundation.llm.model_clients.base_model_client.BaseModelClient

```
class openjiuwen.core.foundation.llm.model_clients.base_model_client.BaseModelClient(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

Large model client abstract base class. All ModelClient implementations must inherit from this class and implement abstract methods such as `invoke` and `stream`; provides helper methods for message/tool to dict conversion, request parameter construction, etc. Developers can extend custom model clients based on this class.

**Parameters**:

* **model_config** (ModelRequestConfig): Model request parameters (temperature, top_p, model_name, etc.).
* **model_client_config** (ModelClientConfig): Client configuration (api_key, api_base, timeout, verify_ssl, ssl_cert, etc.).

### abstractmethod async invoke(messages: Union[str, List[BaseMessage], List[dict]], *, tools: Union[List[ToolInfo], List[dict], None] = None, temperature: Optional[float] = None, top_p: Optional[float] = None, model: str = None, max_tokens: Optional[int] = None, stop: Union[Optional[str], None] = None, output_parser: Optional[BaseOutputParser] = None, timeout: float = None, **kwargs) -> AssistantMessage

Asynchronously invoke LLM, returning a single complete assistant message. Subclasses must implement this method.

**Parameters**:

* **messages** (Union[str, List[BaseMessage], List[dict]]): Input content; can be a single string, BaseMessage list, or serialized dict list.
* **tools** (Union[List[ToolInfo], List[dict], None], optional): Tool list. Default value: `None`.
* **temperature** (float, optional): Sampling temperature, controlling randomness of model output. Value range [0, 1]. Default value: `None`, uses value from model_config.
* **top_p** (float, optional): top_p sampling parameter. Value range [0, 1]. Default value: `None`.
* **max_tokens** (int, optional): Maximum number of tokens to generate. Default value: `None`.
* **stop** (str, optional): Stop sequence. Default value: `None`.
* **model** (str, optional): Model name, takes precedence over model_name in model_config. Default value: `None`.
* **output_parser** (BaseOutputParser, optional): Output parser for parsing model-returned content. Default value: `None`.
* **timeout** (float, optional): Request timeout in seconds. Default value: `None`.
* **kwargs**: Variadic parameters, passed through to ModelClient.

**Returns**:

**AssistantMessage**, model response message.

### abstractmethod async stream(messages: Union[str, List[BaseMessage], List[dict]], *, tools: Union[List[ToolInfo], List[dict], None] = None, temperature: Optional[float] = None, top_p: Optional[float] = None, model: str = None, max_tokens: Optional[int] = None, stop: Union[Optional[str], None] = None, output_parser: Optional[BaseOutputParser] = None, timeout: float = None, **kwargs) -> AsyncIterator[AssistantMessageChunk]

Asynchronously stream LLM invocation, returning assistant messages chunk by chunk. Subclasses must implement this method.

**Parameters**:

* **messages** (Union[str, List[BaseMessage], List[dict]]): Input content; can be a single string, BaseMessage list, or serialized dict list.
* **tools** (Union[List[ToolInfo], List[dict], None], optional): Tool list. Default value: `None`.
* **temperature** (float, optional): Sampling temperature, controlling randomness of model output. Value range [0, 1]. Default value: `None`, uses value from model_config.
* **top_p** (float, optional): top_p sampling parameter. Value range [0, 1]. Default value: `None`.
* **max_tokens** (int, optional): Maximum number of tokens to generate. Default value: `None`.
* **stop** (str, optional): Stop sequence. Default value: `None`.
* **model** (str, optional): Model name, takes precedence over model_name in model_config. Default value: `None`.
* **output_parser** (BaseOutputParser, optional): Output parser for parsing model-returned content. Default value: `None`.
* **timeout** (float, optional): Request timeout in seconds. Default value: `None`.
* **kwargs**: Variadic parameters, passed through to ModelClient.

**Returns**:

**AsyncIterator[AssistantMessageChunk]**, async iterator of streaming response chunks.

---

## class openjiuwen.core.foundation.llm.model_clients.openai_model_client.OpenAIModelClient

```
class openjiuwen.core.foundation.llm.model_clients.openai_model_client.OpenAIModelClient(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

Inherits `BaseModelClient`, interfaces with OpenAI-compatible format APIs, uses `ModelClientConfig`'s api_key, api_base, timeout, verify_ssl, ssl_cert, etc.; implements `invoke`, `stream`, supports tool_calls, output_parser.

**Parameters**:

* **model_config** (ModelRequestConfig): Model request parameters.
* **model_client_config** (ModelClientConfig): Client configuration.

---

## SiliconFlowModelClient (removed)

Use `Model` with `client_provider="SiliconFlow"`. Implementation is the OpenAI-compatible alias.

---

## class openjiuwen.core.foundation.llm.model_clients.openai_account_model_client.OpenAIAccountModelClient

```
class openjiuwen.core.foundation.llm.model_clients.openai_account_model_client.OpenAIAccountModelClient(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

Inherits `BaseModelClient`, interfaces with the OpenAI account backend (`chatgpt.com/backend-api/codex`). This provider does not require `api_key` in `ModelClientConfig`; instead, it resolves an access token at request time via `OpenAIAccountAuthManager` from the local OAuth credential store, and refreshes it automatically when it expires. Implements `invoke`, `stream`, and supports tool_calls, output_parser.

**Parameters**:

* **model_config** (ModelRequestConfig): Model request parameters.
* **model_client_config** (ModelClientConfig): Client configuration; `client_provider` must be `"OpenAIAccount"`, and `api_key` may be omitted.

---

## DashScopeModelClient (removed)

Use `Model` with `client_provider="DashScope"`. Multimodal APIs are on `OpenAIModelClient` under that alias.

---
## class openjiuwen.core.foundation.llm.schema.config.ProviderType

Defines supported model provider types.

* **OpenAI**: Represents OpenAI provider.
* **OpenAIAccount**: Represents the provider that connects using OpenAI account OAuth credentials, requiring no `api_key`.
* **SiliconFlow**: Represents SiliconFlow provider.

---

## class openjiuwen.core.foundation.llm.schema.config.ModelClientConfig

Client configuration data class.

* **client_id** (str): Unique client identifier for registration in Runner. Default value: automatically generated by `uuid.uuid4()`.
* **client_provider** (Union[ProviderType, str]): Provider identifier. Enum values: `OpenAI`, `OpenAIAccount`, `SiliconFlow`.
* **api_key** (str): API key. Optional when `client_provider` is `OpenAIAccount` (default value: `""`); required for every other built-in provider.
* **api_base** (str): API base URL. Required for every built-in provider, including `OpenAIAccount`; never auto-filled with a default.
* **timeout** (float): Request timeout in seconds. Value range greater than 0. Default value: `60.0`.
* **max_retries** (int): Maximum retry count. Default value: `3`.
* **verify_ssl** (bool): Whether to verify SSL certificate. Default value: `True`.
* **ssl_cert** (str, optional): SSL certificate path. Required when `verify_ssl` is `True`. Default value: `None`.

---

## class openjiuwen.core.foundation.llm.schema.config.ModelRequestConfig

Model parameter configuration data class for a single request.

* **model_name** (str): Model name (alias `model`). Default value: `""`.
* **temperature** (float): Temperature parameter, controlling output randomness. Value range [0, 1]. Default value: `0.95`.
* **top_p** (float): top_p sampling parameter. Value range [0, 1]. Default value: `0.95`.
* **max_tokens** (int, optional): Maximum number of tokens to generate. Default value: `None`.
* **stop** (str, optional): Stop sequence. Default value: `None`.

---

## class openjiuwen.core.foundation.llm.schema.message.BaseMessage

Message base class.

* **role** (str): Message role.
* **content** (Union[str, List[Union[str, dict]]]): Message content. Default value: `""`.
* **name** (str, optional): Message sender name. Default value: `None`.

---

## class openjiuwen.core.foundation.llm.schema.message.UserMessage

User message, inheriting from `BaseMessage`.

* **role** (str): Fixed as `"user"`.

---

## class openjiuwen.core.foundation.llm.schema.message.SystemMessage

System message, inheriting from `BaseMessage`.

* **role** (str): Fixed as `"system"`.

---

## class openjiuwen.core.foundation.llm.schema.message.AssistantMessage

Assistant message, inheriting from `BaseMessage`.

* **role** (str): Fixed as `"assistant"`.
* **tool_calls** (List[ToolCall], optional): Tool call list. Default value: `None`.
* **usage_metadata** (UsageMetadata, optional): Usage metadata. Default value: `None`.
* **finish_reason** (str): Completion reason, can be `"stop"` or `"tool_calls"`. Default value: `"null"`.
* **parser_content** (Any, optional): Content processed by parser. Default value: `None`.
* **reasoning_content** (str, optional): Reasoning content (supported by some models). Default value: `None`.

---

## class openjiuwen.core.foundation.llm.schema.message.ToolMessage

Tool message, inheriting from `BaseMessage`.

* **role** (str): Fixed as `"tool"`.
* **tool_call_id** (str): Corresponding tool call ID.

---

## class openjiuwen.core.foundation.llm.schema.message.UsageMetadata

Metadata.

* **code** (int): Response status code. Default value: `0`.
* **err_msg** (str): Error message. Default value: `""`.
* **prompt** (str): Prompt. Default value: `""`.
* **task_id** (str): Task ID. Default value: `""`.
* **model_name** (str): Model name. Default value: `""`.
* **total_latency** (float): Total latency. Default value: `0.0`.
* **first_token_time** (str): First token time. Default value: `""`.
* **request_start_time** (str): Request start time. Default value: `""`.
* **input_tokens** (int): Input token count. Default value: `0`.
* **output_tokens** (int): Output token count. Default value: `0`.
* **total_tokens** (int): Total token count. Default value: `0`.
* **cache_tokens** (int): Cache token count. Default value: `0`.

---

## class openjiuwen.core.foundation.llm.schema.message_chunk.BaseMessageChunk

Message chunk base class, inheriting from `BaseMessage`, supports content merge operations (string or list concatenation).

---

## class openjiuwen.core.foundation.llm.schema.message_chunk.AssistantMessageChunk

Streaming chunk of assistant message, inheriting from `AssistantMessage` and `BaseMessageChunk`. Supports merging with another `AssistantMessageChunk` (content, tool_calls merge fragments by id, usage_metadata, finish_reason, etc. take the latter or merge).

---

## class openjiuwen.core.foundation.llm.schema.tool_call.ToolCall

Single tool call data class.

* **id** (str, optional): Tool call ID.
* **type** (str): Tool call type.
* **name** (str): Tool name.
* **arguments** (str): Tool arguments (JSON string).
* **index** (int, optional): Tool call index, for distinguishing multiple tool calls. Default value: `None`.
* **response_item_id** (str, optional): Provider response item ID, used by protocols that distinguish response item IDs from tool call IDs. Default value: `None`.

---

## class openjiuwen.core.foundation.llm.schema.generation_response.GenerationResponse

```
class openjiuwen.core.foundation.llm.schema.generation_response.GenerationResponse()
```

Generation response base class, parent class for all generation response types (image, audio, video).

**Fields**:

* **model** (str, optional): Model name used for generation. Default value: `None`.

---

## class openjiuwen.core.foundation.llm.schema.generation_response.ImageGenerationResponse

```
class openjiuwen.core.foundation.llm.schema.generation_response.ImageGenerationResponse()
```

Image generation response class, inherits from `GenerationResponse`. Used to return results from image generation API.

**Fields**:

* **images** (List[str]): List of generated image URLs. Default value: `None`.
* **images_base64** (List[str]): List of Base64-encoded images. Default value: `None`.
* **created** (int, optional): Creation timestamp. Default value: `None`.

**Configuration**:

* **model_config**: `ConfigDict(arbitrary_types_allowed=True)`, allows using arbitrary types (such as bytes and other non-standard types).

**Example**:

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

Audio/speech generation response class, inherits from `GenerationResponse`. Used to return results from speech synthesis API.

**Fields**:

* **audio_url** (str, optional): URL of the generated audio. Default value: `None`.
* **audio_data** (bytes, optional): Binary audio data. Default value: `None`.
* **duration** (float, optional): Audio duration in seconds. Default value: `None`.
* **format** (str, optional): Audio format (e.g., `"mp3"`, `"wav"`, etc.). Default value: `"mp3"`.

**Configuration**:

* **model_config**: `ConfigDict(arbitrary_types_allowed=True)`, allows using arbitrary types (such as bytes and other non-standard types).

**Example**:

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

Video generation response class, inherits from `GenerationResponse`. Used to return results from video generation API.

**Fields**:

* **video_url** (str, optional): URL of the generated video. Default value: `None`.
* **video_data** (bytes, optional): Binary video data. Default value: `None`.
* **duration** (float, optional): Video duration in seconds. Default value: `None`.
* **resolution** (str, optional): Video resolution (e.g., `"1920x1080"`). Default value: `None`.
* **format** (str, optional): Video format (e.g., `"mp4"`, `"avi"`, etc.). Default value: `"mp4"`.

**Configuration**:

* **model_config**: `ConfigDict(arbitrary_types_allowed=True)`, allows using arbitrary types (such as bytes and other non-standard types).

**Example**:

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

## class openjiuwen.core.foundation.llm.output_parsers.output_parser.BaseOutputParser

Output parser abstract base class. Developers can implement custom output parsers based on this class.

### abstractmethod async parse(inputs) -> Any

Asynchronously parse LLM output.

**Parameters**:

* **inputs**: AssistantMessage or its content string.

**Returns**:

Parsed result.

### abstractmethod async stream_parse(streaming_inputs: AsyncIterator) -> AsyncIterator[Any]

Asynchronously stream parse LLM output.

**Parameters**:

* **streaming_inputs** (AsyncIterator): AsyncIterator[AssistantMessageChunk] streaming input.

**Returns**:

**AsyncIterator[Any]**, async iterator of parsed result fragments.

---

## class openjiuwen.core.foundation.llm.output_parsers.json_output_parser.JsonOutputParser

JSON output parser, inheriting from `BaseOutputParser`. Extracts `` ```json ... ``` `` code blocks from AssistantMessage or string and parses as JSON object; supports streaming parsing.

### async parse(llm_output: Union[str, AssistantMessage]) -> Any

Parse JSON content from LLM output.

**Parameters**:

* **llm_output** (Union[str, AssistantMessage]): LLM output, can be string or AssistantMessage.

**Returns**:

Parsed JSON object, returns `None` on parse failure.

### async stream_parse(streaming_inputs: AsyncIterator[Union[str, AssistantMessageChunk]]) -> AsyncIterator[Optional[Dict[str, Any]]]

Stream parse JSON content.

**Parameters**:

* **streaming_inputs** (AsyncIterator): Streaming input.

**Returns**:

**AsyncIterator[Optional[Dict[str, Any]]]**, async iterator of parsed results.

---

> **Note**: `model_client_config` cannot be `None`; `client_provider` currently supports `"OpenAI"`, `"SiliconFlow"`, other values will raise an exception and prompt supported types. Unpassed `temperature`, `top_p`, `max_tokens`, `stop`, etc. will use default values from `model_config`.
