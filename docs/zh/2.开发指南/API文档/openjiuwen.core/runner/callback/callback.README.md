# callback

`openjiuwen.core.runner.callback` 提供生产级异步回调框架（Async Callback Framework），专为 asyncio 设计，支持事件驱动、优先级执行、过滤、链式执行与回滚、性能指标与生命周期钩子。

**模块索引**：

| 类型  | 名称                                                                                                                                                                                                                                   | 说明                   |
|-----|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------|
| 类   | [AsyncCallbackFramework](framework.md)                                                                                                                                                                                               | 异步回调框架主类             |
| 类   | [CallbackChain](chain.md)                                                                                                                                                                                                            | 带回滚的回调链              |
| 枚举  | [FilterAction](enums.md#enum-filteraction)、[ChainAction](enums.md#enum-chainaction)、[HookType](enums.md#enum-hooktype)                                                                                                               | 过滤动作、链动作、钩子类型        |
| 数据类 | [CallbackMetrics](models.md#class-callbackmetrics)、[FilterResult](models.md#class-filterresult)、[ChainContext](models.md#class-chaincontext)、[ChainResult](models.md#class-chainresult)、[CallbackInfo](models.md#class-callbackinfo) | 指标、过滤结果、链上下文与结果、回调信息 |
| 过滤器 | [EventFilter](filters.md#class-eventfilter)、[RateLimitFilter](filters.md#class-ratelimitfilter)、[CircuitBreakerFilter](filters.md#class-circuitbreakerfilter) 等                                                                      | 事件过滤与限流、熔断等          |

**相关文档**：

- [异步回调框架](../../../../高阶用法/异步回调框架.md)：高阶用法中的开发指南与示例。
