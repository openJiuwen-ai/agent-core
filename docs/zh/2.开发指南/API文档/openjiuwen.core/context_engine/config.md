# openjiuwen.core.context_engine.config

## class openjiuwen.core.context_engine.config.ContextEngineConfig
```python
class openjiuwen.core.context_engine.config.ContextEngineConfig()
```
表示上下文引擎的完整配置项，用于控制上下文引擎的各种行为。

**参数**：

- **max_context_message_num**(int, 可选)：表示任意上下文中允许的消息总数上限。默认值：`None`，表示不启用任何硬限制。
- **default_window_message_num**(int, 可选)：表示当创建滑动窗口未显式指定token数或消息数时，需保留的最新消息数量。若显式设置，值必须大于`0`。默认值：`None`，表示无限制。
- **default_window_round_num**(int, 可选)：表示创建滑动窗口时需保留的最新对话轮次数量。一个轮次定义为：从用户消息开始，至下一条不含工具调用的助手消息结束（即工具调用轮次中的最终回复）。若助手执行工具调用，该过程会跨多条消息，但在逻辑上计为一个轮次。该配置可在不指定精确消息数的前提下，有效维持对话连贯性。若显式设置，值必须大于`0`，在基于轮次的消息截断逻辑中本配置优先级高于`default_window_message_num`；默认值：`None`，表示禁用基于轮次的窗口机制。
- **enable_kv_cache_release**(bool, 可选)：表示是否启动释放已卸载消息的键值缓存（KV-cache）以降低显卡（GPU）内存压力。注意：需要模型支持KV-Cache主动释放接口。默认值：`False`。
- **enable_reload**(bool, 可选)：表示是否启用消息重载接口，自动重新加载已卸载消息的功能。启用后，上下文引擎在获取上下文窗口时自动添加重载提示词，添加重载工具即可在agent需要详细信息时重载原文消息。默认值：`False`。

**样例**：

```python
>>> from openjiuwen.core.context_engine import ContextEngineConfig
>>>
>>> config = ContextEngineConfig(
...     max_context_message_num=500,
...     default_window_message_num=100,
...     default_window_round_num=10,
...     enable_kv_cache_release=False,
...     enable_reload=True,
... )
>>> config.default_window_message_num
100
```