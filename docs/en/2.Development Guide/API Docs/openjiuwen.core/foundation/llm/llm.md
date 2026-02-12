# openjiuwen.core.foundation.llm

`openjiuwen.core.foundation.llm` is the unified **large model invocation and management module** in openJiuwen, responsible for:

- Providing `Model` unified invocation entry point, creating and delegating to corresponding ModelClient based on `client_provider`;
- Defining `BaseModelClient` abstract base class and `OpenAIModelClient`, `SiliconFlowModelClient` implementations;
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

Generate images (text-to-image or image-to-image). **Note**: This method is only available when using DashScope client.

For detailed documentation, see [DashScopeModelClient.generate_image()](#class-openjiuwencorefoundationllmmodel_clientsdashscope_model_clientdashscopemodelclient).

### async generate_speech(...) -> AudioGenerationResponse

Generate speech (text-to-speech). **Note**: This method is only available when using DashScope client.

For detailed documentation, see [DashScopeModelClient.generate_speech()](#class-openjiuwencorefoundationllmmodel_clientsdashscope_model_clientdashscopemodelclient).

### async generate_video(...) -> VideoGenerationResponse

Generate videos (text-to-video or image-to-video). **Note**: This method is only available when using DashScope client.

For detailed documentation, see [DashScopeModelClient.generate_video()](#class-openjiuwencorefoundationllmmodel_clientsdashscope_model_clientdashscopemodelclient).

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

## class openjiuwen.core.foundation.llm.model_clients.siliconflow_model_client.SiliconFlowModelClient

```
class openjiuwen.core.foundation.llm.model_clients.siliconflow_model_client.SiliconFlowModelClient(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

Inherits `BaseModelClient`, interfaces with SiliconFlow vendor format, configuration similar to OpenAI.

**Parameters**:

* **model_config** (ModelRequestConfig): Model request parameters.
* **model_client_config** (ModelClientConfig): Client configuration.

---

## class openjiuwen.core.foundation.llm.model_clients.dashscope_model_client.DashScopeModelClient

```
class openjiuwen.core.foundation.llm.model_clients.dashscope_model_client.DashScopeModelClient(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

Inherits `OpenAIModelClient`, interfaces with Alibaba Cloud DashScope (Tongyi Qianwen) service. In addition to supporting standard conversational capabilities, it provides **multimodal generation features**, including image generation, speech synthesis, and video generation.

**Parameters**:

* **model_config** (ModelRequestConfig): Model request parameters.
* **model_client_config** (ModelClientConfig): Client configuration.

### async generate_image(messages: List[UserMessage], \*, model: Optional[str] = None, size: Optional[str] = "1664*928", negative_prompt: Optional[str] = None, n: Optional[int] = 1, prompt_extend: bool = True, watermark: bool = False, seed: int = 0, **kwargs) -> ImageGenerationResponse

Asynchronously invoke DashScope image generation API, supporting text-to-image (T2I) and image-to-image (I2I) generation.

**Parameters**:

* **messages** (List[UserMessage]): Must contain exactly one `UserMessage`. Content can be:
  - **String**: Pure text prompt (text-to-image, T2I)
  - **List**: Mixed content containing text and images (image-to-image, I2I), supports 1-3 reference images
* **model** (str, optional): Model name to use. Default: `None`, uses model_name from model_config.
  - `"qwen-image-max"`: High-quality image generation (**does not support n>1 batch generation**)
  - `"wan2.6-image"`: General-purpose image generation, supports I2I
* **size** (str, optional): Size of generated image, format: `"width*height"`. Default: `"1664*928"`.
  - Common sizes: `"1024*1024"`, `"1664*928"`, `"2048*2048"`, etc.
* **negative_prompt** (str, optional): Negative prompt to exclude unwanted elements. Recommended in English, e.g.:
  - `"blurry, low quality, watermark, text, cropped, worst quality, jpeg artifacts"`
  - Default: `None`
* **n** (int, optional): Number of images to generate. Default: `1`.
  - **Important**: `qwen-image-max` model only supports `n=1`, otherwise raises `ValidationError`
* **prompt_extend** (bool, optional): Whether to automatically extend/enhance the prompt for better results. Default: `True`.
* **watermark** (bool, optional): Whether to add watermark to generated images. Default: `False`.
* **seed** (int, optional): Random seed for reproducible generation. Set to `0` for random generation. Default: `0`.
* **kwargs**: Other DashScope-specific parameters.

**Returns**:

**ImageGenerationResponse**, containing the following fields:
* `model` (str): Model name used.
* `images` (List[str]): List of generated image URLs.
* `images_base64` (List[str]): List of Base64-encoded images (optional).
* `created` (int): Creation timestamp (optional).

**Exceptions**:

* **ValidationError**: Raised when parameter validation fails, including:
  - messages list length is not 1
  - Number of reference images exceeds 3
  - Using `qwen-image-max` with `n > 1`
* **ModelError**: Raised when API call fails.

**Example**:

```python
>>> import os
>>> import asyncio
>>> from openjiuwen.core.foundation.llm import (
>>>     Model,
>>>     ModelRequestConfig,
>>>     ModelClientConfig,
>>>     UserMessage,
>>> )
>>>
>>> async def demo_generate_image():
>>>     # 1. Configure DashScope client
>>>     model_config = ModelRequestConfig(model_name="qwen-image-max")
>>>     client_config = ModelClientConfig(
>>>         client_id="dashscope_img",
>>>         client_provider="DashScope",
>>>         api_key=os.getenv("DASHSCOPE_API_KEY"),
>>>         api_base="https://example.com/api/v1"
>>>     )
>>>     model = Model(model_client_config=client_config, model_config=model_config)
>>>
>>>     # 2. Text-to-image (T2I)
>>>     messages = [UserMessage(content="A cute orange cat playing in a garden under sunlight, ultra-high definition")]
>>>     response = await model.generate_image(
>>>         messages=messages,
>>>         size="1024*1024",
>>>         negative_prompt="blurry, low quality, watermark, text",
>>>         seed=42
>>>     )
>>>     print(f"Generated image URL: {response.images[0]}")
>>>
>>>     # 3. Image-to-image (I2I) - Using multiple reference images
>>>     messages_i2i = [UserMessage(content=[
>>>         {"text": "Merge these images to create a new scene in watercolor style"},
>>>         {"image": "https://example.com/input1.jpg"},
>>>         {"image": "https://example.com/input2.jpg"}
>>>     ])]
>>>     response_i2i = await model.generate_image(
>>>         messages=messages_i2i,
>>>         model="wan2.6-image",
>>>         size="1664*928"
>>>     )
>>>     print(f"I2I result: {response_i2i.images[0]}")
>>>
>>> asyncio.run(demo_generate_image())
Generated image URL: https://example.com/...
I2I result: https://example.com/...
```

### async generate_speech(messages: List[UserMessage], \*, model: Optional[str] = None, voice: Optional[str] = "Cherry", language_type: Optional[str] = "Auto", **kwargs) -> AudioGenerationResponse

Asynchronously invoke DashScope speech synthesis API to convert text into natural, fluent speech.

**Parameters**:

* **messages** (List[UserMessage]): Must contain at least one `UserMessage` with text content to convert to speech.
* **model** (str, optional): Model name to use. Default: `None`, uses model_name from model_config.
  - `"qwen3-tts-flash"`: Fast speech synthesis (recommended)
  - `"qwen3-tts"`: Standard speech synthesis
* **voice** (str, optional): Voice character selection, supports 47 different voice styles. Default: `"Cherry"`.
  - **Chinese Female Voices**: Cherry, Serena, Momo, Vivian, Moon, Maia, etc.
  - **Chinese Male Voices**: Kai, Nofish, Ethan, Ryan, Aiden, etc.
  - **English Voices**: Jennifer, Bella, Ryan, Ethan, Vincent, etc.
  - See **Supported Voice List** below for complete list
* **language_type** (str, optional): Language type. Default: `"Auto"` for automatic detection.
  - Options: `"Chinese"`, `"English"`, `"German"`, `"Italian"`, `"Portuguese"`, `"Spanish"`, `"Japanese"`, `"Korean"`, `"French"`, `"Russian"`
* **kwargs**: Other DashScope-specific parameters.

**Returns**:

**AudioGenerationResponse**, containing the following fields:
* `model` (str): Model name used
* `audio_url` (str): Generated audio URL (optional)
* `audio_data` (bytes): Audio binary data (optional)
* `duration` (float): Audio duration in seconds (optional)
* `format` (str): Audio format (e.g., `"mp3"`, `"wav"`) (optional)

**Exceptions**:

* **ValidationError**: Raised when text content is empty.
* **ModelError**: Raised when API call fails.

**Example**:

```python
>>> import os
>>> import asyncio
>>> from openjiuwen.core.foundation.llm import (
>>>     Model,
>>>     ModelRequestConfig,
>>>     ModelClientConfig,
>>>     UserMessage,
>>> )
>>>
>>> async def demo_generate_speech():
>>>     # 1. Configure DashScope client
>>>     model_config = ModelRequestConfig(model_name="qwen3-tts-flash")
>>>     client_config = ModelClientConfig(
>>>         client_id="dashscope_tts",
>>>         client_provider="DashScope",
>>>         api_key=os.getenv("DASHSCOPE_API_KEY"),
>>>         api_base="https://example.com/api/v1",
>>>     )
>>>     model = Model(model_client_config=client_config, model_config=model_config)
>>>
>>>     # 2. Basic speech synthesis (Chinese)
>>>     messages = [UserMessage(content="Hello, welcome to Tongyi Qianwen speech synthesis service. This is a test audio.")]
>>>     response = await model.generate_speech(messages=messages)
>>>     print(f"Audio URL: {response.audio_url}")
>>>     print(f"Audio format: {response.format}")
>>>     print(f"Audio duration: {response.duration} seconds")
>>>
>>>     # 3. Custom voice and language (English male voice)
>>>     messages_en = [UserMessage(content="Hello, welcome to our AI voice synthesis service. This is a demo.")]
>>>     response_en = await model.generate_speech(
>>>         messages=messages_en,
>>>         voice="Ethan",
>>>         language_type="English"
>>>     )
>>>     print(f"English audio: {response_en.audio_url}")
>>>
>>>     # 4. Long text speech synthesis
>>>     long_text = "Artificial intelligence technology is developing rapidly." * 50  # Simulate long text
>>>     messages_long = [UserMessage(content=long_text)]
>>>     response_long = await model.generate_speech(
>>>         messages=messages_long,
>>>         voice="Serena"
>>>     )
>>>     print(f"Long text audio duration: {response_long.duration} seconds")
>>>
>>> asyncio.run(demo_generate_speech())
Audio URL: https://example.com/...
Audio format: mp3
Audio duration: 3.5 seconds
English audio: https://example.com/...
Long text audio duration: 42.8 seconds
```

**Supported Voice List**:
Cherry, Serena, Ethan, Chelsie, Momo, Vivian, Moon, Maia, Kai, Nofish, Bella, Jennifer, Ryan, Katerina, Aiden, Eldric Sage, Mia, Mochi, Bellona, Vincent, Bunny, Neil, Elias, Arthur, Nini, Ebona, Seren, Pip, Stella, Bodega, Sonrisa, Alek, Dolce, Sohee, Ono Anna, Lenn, Emilien, Andre, Radio Gol, Jada, Dylan, Li, Marcus, Roy, Peter, Sunny, Eric, Rocky, Kiki

**Supported Language Types**:
Chinese, English, German, Italian, Portuguese, Spanish, Japanese, Korean, French, Russian

### async generate_video(messages: List[UserMessage], \*, img_url: Optional[str] = None, audio_url: Optional[str] = None, model: Optional[str] = None, size: Optional[str] = None, resolution: Optional[str] = None, duration: Optional[int] = 5, prompt_extend: bool = True, watermark: bool = False, negative_prompt: Optional[str] = None, seed: Optional[int] = None, **kwargs) -> VideoGenerationResponse

Asynchronously invoke DashScope video generation API, supporting text-to-video (T2V) and image-to-video (I2V) generation.

**Parameters**:

* **messages** (List[UserMessage]): Must contain exactly one `UserMessage` with text description of the video to generate.
* **img_url** (str, optional): Input image URL for image-to-video (I2V) mode. Default: `None` (uses text-to-video T2V mode).
  - Supported formats: Public URL, local file path (`file://` prefix), base64-encoded image
* **audio_url** (str, optional): Background audio URL, can be combined with text or image to generate video with audio. Default: `None`.
* **model** (str, optional): Model name to use. Default: `None`, uses model_name from model_config.
  - `"wan2.6-t2v"`: Text-to-video (T2V)
  - `"wan2.6-i2v-flash"`: Image-to-video (I2V, fast)
  - `"wan2.6-i2v-standard"`: Image-to-video (I2V, standard quality)
* **size** (str, optional): Video size, **only for text-to-video (T2V)**. Format: `"width*height"`, e.g., `"1280*720"`. Default: `None`.
* **resolution** (str, optional): Video resolution, **only for image-to-video (I2V)**. Options: `"720P"`, `"1080P"`. Default: `None`.
* **duration** (int, optional): Video duration in seconds. Default: `5`.
  - Supported range: typically 5-10 seconds
* **prompt_extend** (bool, optional): Whether to automatically extend/enhance the prompt for better results. Default: `True`.
* **watermark** (bool, optional): Whether to add watermark to generated video. Default: `False`.
* **negative_prompt** (str, optional): Negative prompt to control unwanted video features, e.g., `"blurry, low quality, shaky, distorted"`. Default: `None`.
* **seed** (int, optional): Random seed for reproducible generation. Default: `None`.
* **kwargs**: Other DashScope-specific parameters.

**Returns**:

**VideoGenerationResponse**, containing the following fields:
* `model` (str): Model name used
* `video_url` (str): Generated video URL
* `video_data` (bytes): Video binary data (optional)
* `duration` (float): Video duration in seconds (optional)
* `resolution` (str): Video resolution (optional)
* `format` (str): Video format (default `"mp4"`)

**Exceptions**:

* **ValidationError**: Raised when parameter validation fails (e.g., messages count is not 1, content is empty, etc.).
* **ModelError**: Raised when API call fails.

**Example**:

```python
>>> import os
>>> import asyncio
>>> from openjiuwen.core.foundation.llm import (
>>>     Model,
>>>     ModelRequestConfig,
>>>     ModelClientConfig,
>>>     UserMessage,
>>> )
>>>
>>> async def demo_generate_video():
>>>     # 1. Configure DashScope client
>>>     model_config = ModelRequestConfig(model_name="wan2.6-t2v")
>>>     client_config = ModelClientConfig(
>>>         client_id="dashscope_video",
>>>         client_provider="DashScope",
>>>         api_key=os.getenv("DASHSCOPE_API_KEY"),
>>>         api_base="https://example.com/api/v1",
>>>     )
>>>     model = Model(model_client_config=client_config, model_config=model_config)
>>>
>>>     # 2. Text-to-video (T2V)
>>>     messages = [UserMessage(content="A cute white rabbit happily running and jumping on a green meadow")]
>>>     response = await model.generate_video(
>>>         messages=messages,
>>>         size="1280*720",
>>>         duration=5,
>>>         negative_prompt="blurry, low quality, shaky, distorted"
>>>     )
>>>     print(f"Generated video URL: {response.video_url}")
>>>     print(f"Video duration: {response.duration} seconds")
>>>     print(f"Video resolution: {response.resolution}")
>>>
>>>     # 3. Image-to-video (I2V)
>>>     messages_i2v = [UserMessage(content="Animate the scene in the image, clouds drifting slowly, leaves swaying gently")]
>>>     response_i2v = await model.generate_video(
>>>         messages=messages_i2v,
>>>         img_url="https://example.com/landscape.jpg",
>>>         model="wan2.6-i2v-flash",
>>>         resolution="720P",
>>>         duration=5
>>>     )
>>>     print(f"I2V result: {response_i2v.video_url}")
>>>
>>>     # 4. Image-to-video + audio fusion
>>>     messages_audio = [UserMessage(content="A singer performing on stage")]
>>>     response_audio = await model.generate_video(
>>>         messages=messages_audio,
>>>         img_url="https://example.com/singer.jpg",
>>>         audio_url="https://example.com/background_music.mp3",
>>>         model="wan2.6-i2v-standard",
>>>         resolution="1080P",
>>>         duration=10
>>>     )
>>>     print(f"Video with audio: {response_audio.video_url}")
>>>
>>> asyncio.run(demo_generate_video())
Generated video URL: https://example.com/...
Video duration: 5.0 seconds
Video resolution: 1280*720
I2V result: https://example.com/...
Video with audio: https://example.com/...
```

---

## class openjiuwen.core.foundation.llm.schema.config.ProviderType

Defines supported model provider types.

* **OpenAI**: Represents OpenAI provider.
* **SiliconFlow**: Represents SiliconFlow provider.

---

## class openjiuwen.core.foundation.llm.schema.config.ModelClientConfig

Client configuration data class.

* **client_id** (str): Unique client identifier for registration in Runner. Default value: automatically generated by `uuid.uuid4()`.
* **client_provider** (Union[ProviderType, str]): Provider identifier. Enum values: `OpenAI`, `SiliconFlow`.
* **api_key** (str): API key.
* **api_base** (str): API base URL.
* **timeout** (float): Request timeout in seconds. Value range greater than 0. Default value: `60.0`.
* **max_retries** (int): Maximum retry count. Default value: `3`.
* **verify_ssl** (bool): Whether to verify SSL certificate. Default value: `True`.
* **ssl_cert** (str, optional): SSL certificate path. Required when `verify_ssl` is `True`. Default value: `None`.

---

## class openjiuwen.core.foundation.llm.schema.config.ModelRequestConfig

Model parameter configuration data class for a single request.

* **model_name** (str): Model name (alias `model`). Default value: `""`.
* **temperature** (float): Temperature parameter, controlling output randomness. Value range [0, 1]. Default value: `0.95`.
* **top_p** (float): top_p sampling parameter. Value range [0, 1]. Default value: `0.1`.
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
