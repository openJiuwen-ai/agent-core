# callback

`openjiuwen.core.runner.callback` provides a production-ready async callback framework (Async Callback Framework) for asyncio: event-driven execution, priority ordering, filtering, chaining with rollback, metrics, and lifecycle hooks.

**Module index**:

| Type | Name | Description |
|------|------|-------------|
| Class | [AsyncCallbackFramework](framework.md) | Main framework class |
| Class | [CallbackChain](chain.md) | Callback chain with rollback |
| Enums | [FilterAction](enums.md#enum-filteraction), [ChainAction](enums.md#enum-chainaction), [HookType](enums.md#enum-hooktype) | Filter action, chain action, hook type |
| Data classes | [CallbackMetrics](models.md#class-callbackmetrics), [FilterResult](models.md#class-filterresult), [ChainContext](models.md#class-chaincontext), [ChainResult](models.md#class-chainresult), [CallbackInfo](models.md#class-callbackinfo) | Metrics, filter result, chain context/result, callback info |
| Filters | [EventFilter](filters.md#class-eventfilter), [RateLimitFilter](filters.md#class-ratelimitfilter), [CircuitBreakerFilter](filters.md#class-circuitbreakerfilter), etc. | Event filtering, rate limit, circuit breaker |

**Related docs**:

- [Async Callback Framework](../../../../Advanced%20Usage/Async%20Callback%20Framework.md): development guide and examples in Advanced Usage.
