# llm

`openjiuwen.core.foundation.llm`是openJiuwen中用于统一管理和调用大模型实例的核心模块。

**详细 API 文档**：[llm.md](./llm/llm.md)

**Classes**：

| CLASS | DESCRIPTION |
|-------|-------------|
| **Model** | 大模型封装类，提供统一的调用接口。 |
| **BaseModelClient** | 大模型客户端抽象基类。 |
| **OpenAIModelClient** | OpenAI兼容的大模型客户端实现。 |
| **DashScopeModelClient** | DashScope（通义千问）大模型客户端实现，支持对话、图像生成、语音合成和视频生成等多模态功能。 |
| **BaseOutputParser** | 输出解析器抽象基类。 |
| **JsonOutputParser** | JSON格式输出解析器。 |
| **MarkdownOutputParser** | Markdown格式输出解析器。 |
| **ModelRequestConfig** | 模型请求配置类。 |
| **ModelClientConfig** | 模型客户端配置类。 |
| **BaseModelInfo** | 模型信息基类。 |
| **BaseMessage** | 消息基类。 |
| **AssistantMessage** | 助手消息类。 |
| **UserMessage** | 用户消息类。 |
| **SystemMessage** | 系统消息类。 |
| **ToolMessage** | 工具消息类。 |
| **AssistantMessageChunk** | 助手消息流式块类。 |
| **ToolCall** | 工具调用类。 |
| **ImageGenerationResponse** | 图像生成响应类，包含生成的图片URL或Base64数据。 |
| **AudioGenerationResponse** | 音频生成响应类，包含生成的音频URL或二进制数据。 |
| **VideoGenerationResponse** | 视频生成响应类，包含生成的视频URL或二进制数据。 |
