# openjiuwen.core.context_engine.config

## `CompressionRecallConfig`

```python
class openjiuwen.core.context_engine.CompressionRecallConfig(
    enabled: bool = False,
    chunk_size_tokens: int = 3000,
    chunk_overlap_tokens: int = 300,
)
```

压缩原文归档与召回的 ContextEngine 级配置。启用后，压缩器在压缩前会把被替换的
原始消息归档，Harness 同时注册 `recall_compressed_context` 工具，模型可按需
找回被压缩掉的内容。

**参数：**

- `enabled`：是否启用压缩原文归档与召回。默认 `False`。
- `chunk_size_tokens`：归档检索块的目标 token 数。必须大于 `0`，默认 `3000`。
- `chunk_overlap_tokens`：相邻检索块重叠的 token 数。必须大于 `0` 且小于
  `chunk_size_tokens`，默认 `300`。

## `ContextEngineConfig`

```python
class openjiuwen.core.context_engine.ContextEngineConfig()
```

上下文引擎的完整配置。

**参数：**

- `max_context_message_num` (`int | None`)：单个 context 允许的消息总数硬上限。
  默认 `None`，表示不限制。
- `default_window_message_num` (`int | None`)：未显式指定窗口大小时保留的最新
  消息数量。默认 `None`，表示不按消息数截断；设置时必须大于 `0`。
- `default_window_round_num` (`int | None`)：窗口保留的最新完整对话轮次数。
  设置后先于按消息数截断生效。默认 `None`，表示不按对话轮截断。

两者均为 `None` 时，`get_context_window()` 不做任何窗口截断，返回完整对话
历史（仅受 `max_context_message_num` 缓冲区上限和上下文处理器的卸载/压缩
影响）。
- `enable_reload` (`bool`)：是否启用被卸载内容的 reload 协议。默认 `False`。
- `context_window_tokens` (`int | None`)：运行模型支持的总上下文窗口 token 数，
  供阈值计算和压缩遥测使用。默认 `None`。
- `model_name` (`str | None`)：上下文使用的模型名。默认 `None`。
- `model_context_window_tokens` (`dict[str, int] | None`)：模型名到上下文窗口
  token 数的显式映射。默认 `None`。
- `enable_openrouter_model_context_window_tokens` (`bool`)：是否从 OpenRouter
  获取模型窗口元数据。默认 `False`。
- `openrouter_request_timeout` (`float`)：请求 OpenRouter 元数据的超时时间，
  单位为秒。默认 `3.0`，必须大于 `0`。
- `compression_recall_config` (`CompressionRecallConfig`)：压缩原文归档与召回
  策略。默认创建一个关闭状态的 `CompressionRecallConfig`。
- `enable_context_debug` (`bool`)：上下文处理器统一调试开关。开启后各处理器在
  流水线各阶段（阈值检查、分段、压缩重试、前后对比）落盘 JSONL 调试记录。
  默认 `False`，关闭时无额外开销。
- `context_debug_dir` (`str | None`)：调试记录存放目录。为 `None` 时依次回退到
  环境变量 `OPENJIUWEN_CONTEXT_DEBUG_DIR` 和默认目录
  `{workspace}/context/{session_id}_context/context_debug/`。默认 `None`。

显式的 `context_window_tokens` 和 `model_context_window_tokens` 优先于远端
OpenRouter 元数据。

**样例：**

```python
from openjiuwen.core.context_engine import (
    CompressionRecallConfig,
    ContextEngineConfig,
)

config = ContextEngineConfig(
    default_window_round_num=10,
    context_window_tokens=128_000,
    compression_recall_config=CompressionRecallConfig(
        enabled=True,
        chunk_size_tokens=3000,
        chunk_overlap_tokens=300,
    ),
)
```

> **说明**：`enable_kv_cache_release` 已不属于 `ContextEngineConfig`，KV cache
> 释放请通过 Agent 侧配置开启：
> `ReActAgentConfig.configure_kv_cache_affinity(enable_kv_cache_release=True)`。
