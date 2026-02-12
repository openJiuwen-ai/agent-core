# openjiuwen.core.foundation.llm

`openjiuwen.core.foundation.llm` 是 openJiuwen 中统一的**大模型调用与管理模块**，负责：

- 提供 `Model` 统一调用入口，根据 `client_provider` 创建并委托给对应 ModelClient；
- 定义 `BaseModelClient` 抽象基类及 `OpenAIModelClient`、`SiliconFlowModelClient` 实现；
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

生成图像（文本生图或图生图）。**注意**：此方法仅在使用DashScope客户端时可用。

详细文档请参见 [DashScopeModelClient.generate_image()](#class-openjiuwencorefoundationllmmodel_clientsdashscope_model_clientdashscopemodelclient)。

### async generate_speech(...) -> AudioGenerationResponse

生成语音（文本转语音）。**注意**：此方法仅在使用DashScope客户端时可用。

详细文档请参见 [DashScopeModelClient.generate_speech()](#class-openjiuwencorefoundationllmmodel_clientsdashscope_model_clientdashscopemodelclient)。

### async generate_video(...) -> VideoGenerationResponse

生成视频（文本生视频或图生视频）。**注意**：此方法仅在使用DashScope客户端时可用。

详细文档请参见 [DashScopeModelClient.generate_video()](#class-openjiuwencorefoundationllmmodel_clientsdashscope_model_clientdashscopemodelclient)。

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

## class openjiuwen.core.foundation.llm.model_clients.siliconflow_model_client.SiliconFlowModelClient

```
class openjiuwen.core.foundation.llm.model_clients.siliconflow_model_client.SiliconFlowModelClient(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

继承 `BaseModelClient`，对接SiliconFlow厂商格式，配置方式与 OpenAI 类似。

**参数**：

* **model_config**(ModelRequestConfig)：模型请求参数。
* **model_client_config**(ModelClientConfig)：客户端配置。

---

## class openjiuwen.core.foundation.llm.model_clients.dashscope_model_client.DashScopeModelClient

```
class openjiuwen.core.foundation.llm.model_clients.dashscope_model_client.DashScopeModelClient(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

继承 `OpenAIModelClient`，对接阿里云DashScope（通义千问）服务，除了支持标准的对话能力外，还提供**多模态生成功能**，包括图像生成、语音合成和视频生成。

**参数**：

* **model_config**(ModelRequestConfig)：模型请求参数。
* **model_client_config**(ModelClientConfig)：客户端配置。

### async generate_image(messages: List[UserMessage], \*, model: Optional[str] = None, size: Optional[str] = "1664*928", negative_prompt: Optional[str] = None, n: Optional[int] = 1, prompt_extend: bool = True, watermark: bool = False, seed: int = 0, **kwargs) -> ImageGenerationResponse

异步调用DashScope图像生成API，支持文本生成图像（text-to-image）和图像生成图像（image-to-image）。

**参数**：

* **messages**(List[UserMessage])：必须包含恰好一条`UserMessage`。内容可为：
  - **字符串**：纯文本提示词（文本生图，T2I）
  - **列表**：包含文本和图片的混合内容（图生图，I2I），支持1-3张参考图片
* **model**(str，可选)：使用的模型名称。默认值：`None`，使用model_config中的model_name。
  - `"qwen-image-max"`：高质量图像生成（**不支持n>1批量生成**）
  - `"wan2.6-image"`：通用图像生成，支持图生图
* **size**(str，可选)：生成图片的尺寸，格式为 `"宽度*高度"`。默认值：`"1664*928"`。
  - 常用尺寸：`"1024*1024"`、`"1664*928"`、`"2048*2048"`等
* **negative_prompt**(str，可选)：负面提示词，用于排除不想要的元素。建议使用英文，如：
  - `"blurry, low quality, watermark, text, cropped, worst quality, jpeg artifacts"`
  - 默认值：`None`
* **n**(int，可选)：生成图片数量。默认值：`1`。
  - **重要**：`qwen-image-max`模型仅支持`n=1`，否则会抛出`ValidationError`
* **prompt_extend**(bool，可选)：是否自动扩展提示词以获得更好效果。默认值：`True`。
* **watermark**(bool，可选)：是否添加水印。默认值：`False`。
* **seed**(int，可选)：随机种子，用于生成可复现的结果。设置为`0`表示随机生成。默认值：`0`。
* **kwargs**：其他DashScope特定参数。

**返回**：

**ImageGenerationResponse**，包含以下字段：
* `model`(str): 使用的模型名称。
* `images`(List[str]): 生成的图片URL列表。
* `images_base64`(List[str]): Base64编码的图片列表（可选）。
* `created`(int): 创建时间戳（可选）。

**异常**：

* **ValidationError**：当参数验证失败时抛出，包括：
  - messages 列表长度不为 1
  - 参考图片数量超过 3 张
  - 使用`qwen-image-max`时 `n > 1`
* **ModelError**：当API调用失败时抛出。

**样例**：

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
>>>     # 1. 配置 DashScope 客户端
>>>     model_config = ModelRequestConfig(model_name="qwen-image-max")
>>>     client_config = ModelClientConfig(
>>>         client_id="dashscope_img",
>>>         client_provider="DashScope",
>>>         api_key=os.getenv("DASHSCOPE_API_KEY"),
>>>         api_base="https://example.com/api/v1"
>>>     )
>>>     model = Model(model_client_config=client_config, model_config=model_config)
>>>
>>>     # 2. 文本生成图片（T2I）
>>>     messages = [UserMessage(content="一只可爱的橘猫在阳光下的花园里玩耍，超高清画质")]
>>>     response = await model.generate_image(
>>>         messages=messages,
>>>         size="1024*1024",
>>>         negative_prompt="blurry, low quality, watermark, text",
>>>         seed=42
>>>     )
>>>     print(f"生成的图片URL: {response.images[0]}")
>>>
>>>     # 3. 图生图（I2I）- 使用多张参考图
>>>     messages_i2i = [UserMessage(content=[
>>>         {"text": "将这些图片融合，创造一个水彩画风格的新场景"},
>>>         {"image": "https://example.com/input1.jpg"},
>>>         {"image": "https://example.com/input2.jpg"}
>>>     ])]
>>>     response_i2i = await model.generate_image(
>>>         messages=messages_i2i,
>>>         model="wan2.6-image",
>>>         size="1664*928"
>>>     )
>>>     print(f"图生图结果: {response_i2i.images[0]}")
>>>
>>> asyncio.run(demo_generate_image())
生成的图片URL: https://example.com/...
图生图结果: https://example.com/...
```

### async generate_speech(messages: List[UserMessage], \*, model: Optional[str] = None, voice: Optional[str] = "Cherry", language_type: Optional[str] = "Auto", **kwargs) -> AudioGenerationResponse

异步调用DashScope语音合成API，将文本转换为自然流畅的语音。

**参数**：

* **messages**(List[UserMessage])：必须包含至少一条`UserMessage`，内容为需要转换为语音的文本。
* **model**(str，可选)：使用的模型名称。默认值：`None`，使用model_config中的model_name。
  - `"qwen3-tts-flash"`：快速语音合成（推荐）
  - `"qwen3-tts"`：标准语音合成
* **voice**(str，可选)：语音角色选择，支持47种不同风格的声音。默认值：`"Cherry"`。
  - **中文女声**：Cherry, Serena, Momo, Vivian, Moon, Mai 等
  - **中文男声**：Kai, Nofish, Ethan, Ryan, Aiden等
  - **英文声音**：Jennifer, Bella, Ryan, Ethan, Vincent等
  - 完整列表见下方 **支持的声音列表**
* **language_type**(str，可选)：语言类型。默认值：`"Auto"`自动检测。
  - 可选值：`"Chinese"`, `"English"`, `"German"`, `"Italian"`, `"Portuguese"`, `"Spanish"`, `"Japanese"`, `"Korean"`, `"French"`, `"Russian"`
* **kwargs**：其他DashScope特定参数。

**返回**：

**AudioGenerationResponse**，包含以下字段：
* `model`(str): 使用的模型名称
* `audio_url`(str): 生成的音频 URL（可选）
* `audio_data`(bytes): 音频二进制数据（可选）
* `duration`(float): 音频时长（秒）（可选）
* `format`(str): 音频格式（如`"mp3"`、`"wav"`）（可选）

**异常**：

* **ValidationError**：当文本内容为空时抛出。
* **ModelError**：当API调用失败时抛出。

**样例**：

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
>>>     # 1. 配置 DashScope 客户端
>>>     model_config = ModelRequestConfig(model_name="qwen3-tts-flash")
>>>     client_config = ModelClientConfig(
>>>         client_id="dashscope_tts",
>>>         client_provider="DashScope",
>>>         api_key=os.getenv("DASHSCOPE_API_KEY"),
>>>         api_base="https://example.com/api/v1",
>>>     )
>>>     model = Model(model_client_config=client_config, model_config=model_config)
>>>
>>>     # 2. 基础语音合成（中文）
>>>     messages = [UserMessage(content="你好，欢迎使用通义千问语音合成服务。这是一段测试语音。")]
>>>     response = await model.generate_speech(messages=messages)
>>>     print(f"音频URL: {response.audio_url}")
>>>     print(f"音频格式: {response.format}")
>>>     print(f"音频时长: {response.duration}秒")
>>>
>>>     # 3. 自定义声音和语言（英文男声）
>>>     messages_en = [UserMessage(content="Hello, welcome to our AI voice synthesis service. This is a demo.")]
>>>     response_en = await model.generate_speech(
>>>         messages=messages_en,
>>>         voice="Ethan",
>>>         language_type="English"
>>>     )
>>>     print(f"英文音频: {response_en.audio_url}")
>>>
>>>     # 4. 长文本语音合成
>>>     long_text = "人工智能技术正在快速发展。" * 50  # 模拟长文本
>>>     messages_long = [UserMessage(content=long_text)]
>>>     response_long = await model.generate_speech(
>>>         messages=messages_long,
>>>         voice="Serena"
>>>     )
>>>     print(f"长文本音频时长: {response_long.duration}秒")
>>>
>>> asyncio.run(demo_generate_speech())
音频URL: https://example.com/...
音频格式: mp3
音频时长: 3.5秒
英文音频: https://example.com/...
长文本音频时长: 42.8秒
```

**支持的声音列表**：
Cherry, Serena, Ethan, Chelsie, Momo, Vivian, Moon, Maia, Kai, Nofish, Bella, Jennifer, Ryan, Katerina, Aiden, Eldric Sage, Mia, Mochi, Bellona, Vincent, Bunny, Neil, Elias, Arthur, Nini, Ebona, Seren, Pip, Stella, Bodega, Sonrisa, Alek, Dolce, Sohee, Ono Anna, Lenn, Emilien, Andre, Radio Gol, Jada, Dylan, Li, Marcus, Roy, Peter, Sunny, Eric, Rocky, Kiki

**支持的语言类型**：
Chinese, English, German, Italian, Portuguese, Spanish, Japanese, Korean, French, Russian

### async generate_video(messages: List[UserMessage], \*, img_url: Optional[str] = None, audio_url: Optional[str] = None, model: Optional[str] = None, size: Optional[str] = None, resolution: Optional[str] = None, duration: Optional[int] = 5, prompt_extend: bool = True, watermark: bool = False, negative_prompt: Optional[str] = None, seed: Optional[int] = None, **kwargs) -> VideoGenerationResponse

异步调用DashScope视频生成 API，支持文本生成视频（text-to-video）和图像生成视频（image-to-video）。

**参数**：

* **messages**(List[UserMessage])：必须包含恰好一条`UserMessage`，内容为视频描述文本。
* **img_url**(str，可选)：输入图片URL，用于图生视频（I2V）模式。默认值：`None`（使用文本生视频 T2V 模式）。
  - 支持格式：公开 URL、本地文件路径（`file://` 前缀）、base64编码图片
* **audio_url**(str，可选)：背景音频URL，可与文本或图片结合生成带音频的视频。默认值：`None`。
* **model**(str，可选)：使用的模型名称。默认值：`None`，使用model_config中的model_name。
  - `"wan2.6-t2v"`：文本生成视频（T2V）
  - `"wan2.6-i2v-flash"`：图像生成视频（I2V，快速）
  - `"wan2.6-i2v-standard"`：图像生成视频（I2V，标准质量）
* **size**(str，可选)：视频尺寸，**仅用于文本生视频（T2V）**。格式为 `"宽度*高度"`，如`"1280*720"`。默认值：`None`。
* **resolution**(str，可选)：视频分辨率，**仅用于图生视频（I2V）**。可选值：`"720P"`、`"1080P"`。默认值：`None`。
* **duration**(int，可选)：视频时长（秒）。默认值：`5`。
  - 支持范围：通常为5-10秒
* **prompt_extend**(bool，可选)：是否自动扩展提示词以获得更好效果。默认值：`True`。
* **watermark**(bool，可选)：是否添加水印。默认值：`False`。
* **negative_prompt**(str，可选)：负面提示词，用于控制不需要的视频特征，如 `"blurry, low quality, shaky, distorted"`。默认值：`None`。
* **seed**(int，可选)：随机种子，用于生成可复现的结果。默认值：`None`。
* **kwargs**：其他 DashScope 特定参数。

**返回**：

**VideoGenerationResponse**，包含以下字段：
* `model`(str): 使用的模型名称
* `video_url`(str): 生成的视频URL
* `video_data`(bytes): 视频二进制数据（可选）
* `duration`(float): 视频时长（秒）（可选）
* `resolution`(str): 视频分辨率（可选）
* `format`(str): 视频格式（默认`"mp4"`）

**异常**：

* **ValidationError**：当参数验证失败时抛出（如消息数量不为1、内容为空等）。
* **ModelError**：当API调用失败时抛出。

**样例**：

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
>>>     # 1. 配置 DashScope 客户端
>>>     model_config = ModelRequestConfig(model_name="wan2.6-t2v")
>>>     client_config = ModelClientConfig(
>>>         client_id="dashscope_video",
>>>         client_provider="DashScope",
>>>         api_key=os.getenv("DASHSCOPE_API_KEY"),
>>>         api_base="https://example.com/api/v1",
>>>     )
>>>     model = Model(model_client_config=client_config, model_config=model_config)
>>>
>>>     # 2. 文本生成视频（T2V）
>>>     messages = [UserMessage(content="一只可爱的小白兔在绿色的草地上快乐地奔跑跳跃")]
>>>     response = await model.generate_video(
>>>         messages=messages,
>>>         size="1280*720",
>>>         duration=5,
>>>         negative_prompt="blurry, low quality, shaky, distorted"
>>>     )
>>>     print(f"生成的视频URL: {response.video_url}")
>>>     print(f"视频时长: {response.duration}秒")
>>>     print(f"视频分辨率: {response.resolution}")
>>>
>>>     # 3. 图生视频（I2V）
>>>     messages_i2v = [UserMessage(content="让图片中的场景动起来，云朵缓缓飘动，树叶轻轻摇曳")]
>>>     response_i2v = await model.generate_video(
>>>         messages=messages_i2v,
>>>         img_url="https://example.com/landscape.jpg",
>>>         model="wan2.6-i2v-flash",
>>>         resolution="720P",
>>>         duration=5
>>>     )
>>>     print(f"图生视频结果: {response_i2v.video_url}")
>>>
>>>     # 4. 图生视频 + 音频融合
>>>     messages_audio = [UserMessage(content="一个歌手在舞台上表演")]
>>>     response_audio = await model.generate_video(
>>>         messages=messages_audio,
>>>         img_url="https://example.com/singer.jpg",
>>>         audio_url="https://example.com/background_music.mp3",
>>>         model="wan2.6-i2v-standard",
>>>         resolution="1080P",
>>>         duration=10
>>>     )
>>>     print(f"带音频的视频: {response_audio.video_url}")
>>>
>>> asyncio.run(demo_generate_video())
生成的视频URL: https://example.com/...
视频时长: 5.0秒
视频分辨率: 1280*720
图生视频结果: https://example.com/...
带音频的视频: https://example.com/...
```

---

## class openjiuwen.core.foundation.llm.schema.config.ProviderType

定义了支持的模型服务商类型。

* **OpenAI**：表示 OpenAI 服务商。
* **SiliconFlow**：表示 SiliconFlow 服务商。

---

## class openjiuwen.core.foundation.llm.schema.config.ModelClientConfig

客户端配置数据类。

* **client_id**(str)：客户端唯一标识，用于在 Runner 中注册。默认值：由 `uuid.uuid4()` 自动生成。
* **client_provider**(Union[ProviderType, str])：服务商标识。枚举值：`OpenAI`、`SiliconFlow`。
* **api_key**(str)：API 密钥。
* **api_base**(str)：API 基础 URL。
* **timeout**(float)：请求超时，单位：秒。取值范围大于 0。默认值：`60.0`。
* **max_retries**(int)：最大重试次数。默认值：`3`。
* **verify_ssl**(bool)：是否验证 SSL 证书。默认值：`True`。
* **ssl_cert**(str，可选)：SSL 证书路径。当 `verify_ssl` 为 `True` 时必填。默认值：`None`。

---

## class openjiuwen.core.foundation.llm.schema.config.ModelRequestConfig

单次请求的模型参数配置数据类。

* **model_name**(str)：模型名称（别名 `model`）。默认值：`""`。
* **temperature**(float)：温度参数，控制输出的随机性。取值范围为[0, 1]。默认值：`0.95`。
* **top_p**(float)：top_p 采样参数。取值范围为[0, 1]。默认值：`0.1`。
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

> **说明**：`model_client_config` 不可为 `None`；`client_provider` 当前支持 `"OpenAI"`、`"SiliconFlow"`，其他值会抛出异常并提示支持的类型。未传的 `temperature`、`top_p`、`max_tokens`、`stop` 等将使用 `model_config` 中的默认值。
