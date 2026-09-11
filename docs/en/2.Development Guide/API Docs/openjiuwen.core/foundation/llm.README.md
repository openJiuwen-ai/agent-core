# llm

`openjiuwen.core.foundation.llm` is the core module in openJiuwen for unified management and invocation of large model instances.

**Detailed API Documentation**: [llm.md](./llm/llm.md)

**Classes**:

| CLASS | DESCRIPTION |
|-------|-------------|
| **Model** | Large model wrapper class, providing unified invocation interface. |
| **BaseModelClient** | Large model client abstract base class. |
| **OpenAIModelClient** | OpenAI-compatible large model client implementation. |
| **OpenAIAccountModelClient** | OpenAI account OAuth large model client implementation, signing in via OAuth credentials with no `api_key` required. |
| **AnthropicModelClient** | Anthropic protocol client. Legacy `client_provider` names such as `DashScope` and `DeepSeek` are OpenAI-compatible aliases. See [LLM Protocol Consolidation](../../Basic%20Functions/LLM%20Protocol%20Consolidation.md). |
| **BaseOutputParser** | Output parser abstract base class. |
| **JsonOutputParser** | JSON format output parser. |
| **MarkdownOutputParser** | Markdown format output parser. |
| **ModelRequestConfig** | Model request configuration class. |
| **ModelClientConfig** | Model client configuration class. |
| **BaseModelInfo** | Model information base class. |
| **BaseMessage** | Message base class. |
| **AssistantMessage** | Assistant message class. |
| **UserMessage** | User message class. |
| **SystemMessage** | System message class. |
| **ToolMessage** | Tool message class. |
| **AssistantMessageChunk** | Assistant message streaming chunk class. |
| **ToolCall** | Tool call class. |
| **ImageGenerationResponse** | Image generation response class, containing generated image URLs or Base64 data. |
| **AudioGenerationResponse** | Audio generation response class, containing generated audio URLs or binary data. |
| **VideoGenerationResponse** | Video generation response class, containing generated video URLs or binary data. |
