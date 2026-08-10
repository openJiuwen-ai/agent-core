# openjiuwen.core.context_engine.processor.forked.compressor.session_memory_compressor

## class openjiuwen.core.context_engine.processor.forked.compressor.session_memory_compressor.SessionMemoryCompressorConfig

`SessionMemoryCompressor` 的配置类。该处理器将会话级长期笔记（session memory）物化为一条 `<memory_block_session>` 摘要消息，替换笔记已覆盖的历史消息前缀。**该处理器默认关闭（`enabled=False`）**，需要通过配置覆盖显式启用。

* **enabled**(bool，可选)：是否启用该处理器。默认值：`False`。
* **trigger_context_ratio**(float，可选)：上下文 token 数占上下文容量的比例达到该值时，才允许将笔记物化进上下文。取值范围 (0, 1)。默认值：`0.8`。
* **session_memory_path**(str | None，可选)：自定义会话笔记文件路径；为 `None` 时使用默认路径 `{workspace}/context/{session_id}_context/session_memory/session_context.md`。默认值：`None`。
* **max_notes_chars**(int，可选)：笔记内容允许物化的最大字符数，超过则跳过本次物化。默认值：`120000`。
* **memory**(SessionMemoryConfig，可选)：后台异步维护会话笔记的配置，见下文 `SessionMemoryConfig`。默认值：`SessionMemoryConfig()`。

**约束**：笔记由配套的后台更新任务在独立的笔记文件中维护，仅在笔记已生成、且上下文达到 `trigger_context_ratio` 时才会物化；物化只替换笔记已覆盖的消息前缀，未覆盖的消息原样保留。

## class openjiuwen.core.context_engine.context.session_memory_manager.SessionMemoryConfig

后台会话笔记更新任务（`SessionMemoryManager`）的配置类，定义于 `openjiuwen.core.context_engine.context.session_memory_manager`，作为 `SessionMemoryCompressorConfig.memory` 传入。

* **update_trigger_context_ratio**(float，可选)：上下文 token 数占上下文容量的比例达到该值时，触发一次后台笔记更新。取值范围 (0, 1)。默认值：`0.7`。
* **model**(ModelRequestConfig | None，可选)：用于执行笔记更新的模型请求配置；为 `None` 时继承 Agent 的模型配置。默认值：`None`。
* **model_client**(ModelClientConfig | None，可选)：用于执行笔记更新的模型服务配置；为 `None` 时继承 Agent 的模型服务配置。默认值：`None`。
* **update_mode**(Literal["agent_edit", "direct_replace"]，可选)：笔记更新方式。`"agent_edit"` 由专用更新 Agent 通过 `edit_file` 工具编辑笔记文件；`"direct_replace"` 由模型直接输出完整的新笔记内容并整体替换。默认值：`"agent_edit"`。
* **direct_replace_max_retries**(int，可选）：`update_mode="direct_replace"` 时模型调用失败的最大重试次数。默认值：`2`。
* **enable_debug_dump**(bool，可选)：是否将笔记更新的调试记录落盘。默认值：`False`。
* **debug_dump_dir**(str | None，可选)：调试记录存放目录；为 `None` 时使用默认目录。默认值：`None`。

## class openjiuwen.core.context_engine.processor.forked.compressor.session_memory_compressor.SessionMemoryCompressor

```python
SessionMemoryCompressor(config: SessionMemoryCompressorConfig)
```

`SessionMemoryCompressor` 继承自 [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor)，在获取上下文窗口时介入：当 `enabled=True`、上下文 token 数达到 `trigger_context_ratio` 且会话笔记已生成时，读取笔记文件，将其包装为一条 `<memory_block_session>` 消息（附带含义说明、冲突处理策略与覆盖范围标记），替换笔记已覆盖的历史消息前缀。接口与基类一致，详见 [ContextProcessor](base.md#class-openjiuwencorecontext_engineprocessorbasecontextprocessor)。

**参数**：

* **config**(SessionMemoryCompressorConfig)：处理器配置，见上文。

**样例**：

该处理器依赖 workspace 与会话状态，推荐通过 `ContextProcessorRail` 覆盖默认处理器链中的同名配置来启用：

```python
from openjiuwen.core.context_engine.processor.forked.compressor.session_memory_compressor import (
    SessionMemoryCompressorConfig,
)
from openjiuwen.core.context_engine.context.session_memory_manager import SessionMemoryConfig
from openjiuwen.harness.rails.context_engineer.context_processor_rail import ContextProcessorRail

rail = ContextProcessorRail(
    preset=True,
    processors=[
        (
            "SessionMemoryCompressor",
            SessionMemoryCompressorConfig(
                enabled=True,
                memory=SessionMemoryConfig(update_trigger_context_ratio=0.7),
            ),
        )
    ],
)
```
