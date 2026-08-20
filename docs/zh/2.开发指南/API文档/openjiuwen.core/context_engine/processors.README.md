# processor

`openjiuwen.core.context_engine.processor` 是 openJiuwen 中用于扩展和管理上下文处理逻辑的核心模块。Processor 可在消息写入（add_messages）或窗口输出（get_context_window）阶段介入，用于控制上下文规模、降低 token 消耗。

**Classes**：

| CLASS | DESCRIPTION |
|-------|-------------|
| **ContextProcessor** | 上下文处理器抽象基类，定义 trigger / on 钩子与状态持久化接口。 |
| **MessageOffloader** | 消息卸载器，对超阈值大消息裁剪后 offload，不调用 LLM。 |
| **MessageOffloaderConfig** | MessageOffloader 配置类。 |
| **MessageSummaryOffloader** | 消息卸载器，对超阈值的 tool 消息按规则压缩并 offload，不调用 LLM。 |
| **MessageSummaryOffloaderConfig** | MessageSummaryOffloader 配置类。 |
| **DialogueCompressor** | 对话压缩器，将当前轮之前的历史消息压缩为一条摘要。 |
| **DialogueCompressorConfig** | DialogueCompressor 配置类。 |
| **CurrentRoundCompressor** | 当前轮压缩器，摘要最新用户请求之后已经完成的工作。 |
| **CurrentRoundCompressorConfig** | CurrentRoundCompressor 配置类。 |
| **RoundLevelCompressor** | 轮级压缩器，仅保留最近若干条消息，将更早的历史压缩为一条跨轮检查点摘要。 |
| **RoundLevelCompressorConfig** | RoundLevelCompressor 配置类。 |
| **SessionMemoryCompressor** | 将异步维护的会话笔记物化为模型上下文块。默认关闭。 |
| **SessionMemoryCompressorConfig** | SessionMemoryCompressor 配置类。 |
| **ReasoningToolLoopCompactProcessor** | 检测并折叠连续重复的推理/工具调用轮，防止模型陷入死循环。 |
| **ReasoningToolLoopCompactProcessorConfig** | ReasoningToolLoopCompactProcessor 配置类。 |

## 默认处理器链

Harness 通过 `ContextProcessorRail(preset=True)` 组装默认的上下文处理器链，
按固定顺序执行 `MessageSummaryOffloader`、`SessionMemoryCompressor`（默认关闭）、
`ReasoningToolLoopCompactProcessor`、`DialogueCompressor`、`CurrentRoundCompressor`、
`RoundLevelCompressor`。链路与启用示例参见
[上下文引擎](../../高阶用法/上下文引擎.md)。
