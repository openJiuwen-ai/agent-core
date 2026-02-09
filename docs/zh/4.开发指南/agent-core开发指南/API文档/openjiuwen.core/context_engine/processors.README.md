# processor

`openjiuwen.core.context_engine.processor` 是 openJiuwen 中用于扩展和管理上下文处理逻辑的核心模块。Processor 可在消息写入（add_messages）或窗口输出（get_context_window）阶段介入，用于控制上下文规模、降低 token 消耗。

**Classes**：

| CLASS | DESCRIPTION |
|-------|-------------|
| **ContextProcessor** | 上下文处理器抽象基类，定义 trigger / on 钩子与状态持久化接口。 |
| **MessageOffloader** | 消息卸载器，对超阈值大消息裁剪后 offload，不调用 LLM。 |
| **MessageOffloaderConfig** | MessageOffloader 配置类。 |
| **MessageSummaryOffloader** | 消息摘要卸载器，经 LLM 生成摘要后再 offload。 |
| **MessageSummaryOffloaderConfig** | MessageSummaryOffloader 配置类。 |
| **DialogueCompressor** | 对话轮压缩器，将完整 tool-call 轮压缩为一条摘要。 |
| **DialogueCompressorConfig** | DialogueCompressor 配置类。 |
| **RoundLevelCompressor** | 轮级压缩器，将连续简单对话轮压缩为两条摘要。 |
| **RoundLevelCompressorConfig** | RoundLevelCompressor 配置类。 |
