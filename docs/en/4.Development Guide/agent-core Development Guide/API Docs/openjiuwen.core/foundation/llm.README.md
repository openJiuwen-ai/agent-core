# llm

`openjiuwen.core.foundation.llm` is the core module in openJiuwen for unified management and invocation of large model instances.

**Detailed API Documentation**: [llm.md](./llm/llm.md)

**Classes**:

| CLASS | DESCRIPTION |
|-------|-------------|
| **Model** | Large model wrapper class, providing unified invocation interface. |
| **BaseModelClient** | Large model client abstract base class. |
| **OpenAIModelClient** | OpenAI-compatible large model client implementation. |
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
