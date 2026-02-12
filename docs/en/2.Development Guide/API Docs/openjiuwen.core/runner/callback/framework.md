# openjiuwen.core.runner.callback.framework

Production-ready async callback framework for asyncio, with priority execution, filtering, chaining with rollback, metrics, and lifecycle hooks.

## class AsyncCallbackFramework

```python
class AsyncCallbackFramework(enable_metrics: bool = True, enable_logging: bool = True)
```

Async event-driven framework: callback registration, multiple trigger modes, filters, chains with rollback, metrics, and hooks. All operations are async.

**Parameters**:

* **enable_metrics** (bool, optional): Whether to collect performance metrics. Default: True.
* **enable_logging** (bool, optional): Whether to enable logging. Default: True.

### callbacks

```python
callbacks -> Dict[str, List[CallbackInfo]]
```

Event name to list of callback info (read-only).

### chains

```python
chains -> Dict[str, CallbackChain]
```

Event name to callback chain (read-only).

### circuit_breakers

```python
circuit_breakers -> Dict[str, CircuitBreakerFilter]
```

Circuit breaker key to instance (read-only).

### callback_filters

```python
callback_filters -> Dict[Callable, List[EventFilter]]
```

Callback to its dedicated filter list (read-only).

---

### on

```python
def on(
    self,
    event: str,
    *,
    priority: int = 0,
    once: bool = False,
    namespace: str = "default",
    tags: Optional[Set[str]] = None,
    filters: Optional[List[EventFilter]] = None,
    rollback_handler: Optional[Callable] = None,
    error_handler: Optional[Callable] = None,
    max_retries: int = 0,
    retry_delay: float = 0.0,
    timeout: Optional[float] = None
)
```

Decorator to register an async function as a callback for an event. Higher priority runs first.

**Parameters**:

* **event** (str): Event name.
* **priority** (int, optional): Priority. Default: 0.
* **once** (bool, optional): Execute only once. Default: False.
* **namespace** (str, optional): Namespace. Default: "default".
* **tags** (Optional[Set[str]], optional): Tags. Default: None.
* **filters** (Optional[List[EventFilter]], optional): Filters for this callback. Default: None.
* **rollback_handler** (Optional[Callable], optional): Function to call on rollback. Default: None.
* **error_handler** (Optional[Callable], optional): Function to call on error. Default: None.
* **max_retries** (int, optional): Max retries. Default: 0.
* **retry_delay** (float, optional): Retry delay in seconds. Default: 0.0.
* **timeout** (Optional[float], optional): Execution timeout in seconds. Default: None.

**Returns**: Decorator function.

---

### trigger_on_call

```python
def trigger_on_call(self, event: str, pass_result: bool = False, pass_args: bool = True)
```

Decorator: trigger the event when the decorated function is called (before execution; if pass_result is True, trigger again after with the result).

**Parameters**:

* **event** (str): Event name to trigger.
* **pass_result** (bool, optional): Pass function return value to callbacks. Default: False.
* **pass_args** (bool, optional): Pass function arguments to callbacks. Default: True.

**Returns**: Decorator function.

---

### emits

```python
def emits(self, event: str, result_key: str = "result", include_args: bool = False)
```

Decorator: trigger the event after the function returns, passing the return value under **result_key**.

**Parameters**:

* **event** (str): Event name to trigger.
* **result_key** (str, optional): Key for the result. Default: "result".
* **include_args** (bool, optional): Include original arguments. Default: False.

**Returns**: Decorator function.

---

### emits_stream

```python
def emits_stream(self, event: str, item_key: str = "item") -> Callable
```

Decorator for async generators: trigger the event on each yield, passing the item under **item_key**; the generator still yields to the caller as usual.

**Parameters**:

* **event** (str): Event name to trigger on each yield.
* **item_key** (str, optional): Key for the item. Default: "item".

**Returns**: Decorated async generator function.

---

### emit_around

```python
def emit_around(
    self,
    before_event: str,
    after_event: str,
    pass_args: bool = True,
    pass_result: bool = True,
    on_error_event: Optional[str] = None
)
```

Decorator: trigger before_event before execution and after_event after success; if an exception occurs and on_error_event is set, trigger that event.

**Parameters**:

* **before_event** (str): Event before execution.
* **after_event** (str): Event after success.
* **pass_args** (bool, optional): Pass function args to events. Default: True.
* **pass_result** (bool, optional): Pass result to after_event. Default: True.
* **on_error_event** (Optional[str], optional): Event on error. Default: None.

**Returns**: Decorator function.

---

### transform_io

```python
def transform_io(
    self,
    *,
    input_event: Optional[str] = None,
    output_event: Optional[str] = None,
    result_key: str = "result",
    input_transform: Optional[InputTransform] = None,
    output_transform: Optional[OutputTransform] = None,
)
```

Decorator to transform function inputs and outputs. Two modes: **event mode** (set input_event/output_event; framework triggers events and uses last callback return as transformed input or result); **direct callback mode** (set input_transform/output_transform; input_transform(*args, **kwargs) returns (new_args, new_kwargs), output_transform(value) returns new value). Supports async functions and async generators (output transform applied per item for generators).

**Parameters**:

* **input_event** (Optional[str], optional): Input transform event name. Default: None.
* **output_event** (Optional[str], optional): Output transform event name. Default: None.
* **result_key** (str, optional): Key for output event payload. Default: "result".
* **input_transform** (InputTransform, optional): Direct input transform. Default: None.
* **output_transform** (OutputTransform, optional): Direct output transform. Default: None.

**Returns**: Decorator that applies input/output transformation.

---

### register

```python
async def register(
    self,
    event: str,
    callback: Callable[..., Awaitable[Any]],
    *,
    priority: int = 0,
    once: bool = False,
    namespace: str = "default",
    tags: Optional[Set[str]] = None,
    filters: Optional[List[EventFilter]] = None,
    rollback_handler: Optional[Callable] = None,
    error_handler: Optional[Callable] = None,
    max_retries: int = 0,
    retry_delay: float = 0.0,
    timeout: Optional[float] = None
) -> None
```

Register an async callback for an event programmatically. Parameters match the **on** decorator.

---

### unregister

```python
async def unregister(self, event: str, callback: Callable) -> None
```

Unregister a callback from an event. Supports either the original callback or the wrapper returned by **@on**.

**Parameters**:

* **event** (str): Event name.
* **callback** (Callable): Callback to remove (original or wrapper).

---

### unregister_namespace

```python
async def unregister_namespace(self, namespace: str) -> None
```

Unregister all callbacks in the given namespace.

**Parameters**:

* **namespace** (str): Namespace.

---

### unregister_by_tags

```python
async def unregister_by_tags(self, tags: Set[str]) -> None
```

Unregister callbacks that have any of the given tags.

**Parameters**:

* **tags** (Set[str]): Tags to match.

---

### unregister_event

```python
async def unregister_event(self, event: str) -> None
```

Unregister all callbacks for the event and remove associated filters, chains, hooks, and circuit breakers.

**Parameters**:

* **event** (str): Event name.

---

### trigger

```python
async def trigger(self, event: str, *args, **kwargs) -> List[Any]
```

Trigger the event: run all registered callbacks in priority order (with filters, metrics, hooks) and collect results.

**Parameters**:

* **event** (str): Event name.
* **\*args**: Arguments for callbacks.
* **\*\*kwargs**: Keyword arguments for callbacks.

**Returns**: **List[Any]**, list of results from executed callbacks.

---

### trigger_delayed

```python
async def trigger_delayed(self, event: str, delay: float, *args, **kwargs) -> List[Any]
```

Trigger the event after a delay in seconds.

**Parameters**:

* **event** (str): Event name.
* **delay** (float): Delay in seconds.
* **\*args**: Arguments for callbacks.
* **\*\*kwargs**: Keyword arguments for callbacks.

**Returns**: **List[Any]**, callback results.

---

### trigger_chain

```python
async def trigger_chain(self, event: str, *args, **kwargs) -> ChainResult
```

Trigger as a chain: execute callbacks in priority order, passing each result to the next; supports rollback.

**Parameters**:

* **event** (str): Event name.
* **\*args**: Initial arguments for the chain.
* **\*\*kwargs**: Initial keyword arguments for the chain.

**Returns**: **ChainResult**, with final action, result, context, and optional exception.

---

### trigger_parallel

```python
async def trigger_parallel(self, event: str, *args, **kwargs) -> List[Any]
```

Trigger in parallel: run all callbacks concurrently with asyncio.gather; suited for I/O-bound work.

**Parameters**:

* **event** (str): Event name.
* **\*args**: Arguments for callbacks.
* **\*\*kwargs**: Keyword arguments for callbacks.

**Returns**: **List[Any]**, results from callbacks that succeeded.

---

### trigger_until

```python
async def trigger_until(
    self,
    event: str,
    condition: Callable[[Any], bool],
    *args,
    **kwargs
) -> Optional[Any]
```

Execute callbacks in priority order until one return value satisfies condition(result); then return that result.

**Parameters**:

* **event** (str): Event name.
* **condition** (Callable[[Any], bool]): Function that takes the callback result and returns bool.
* **\*args**: Arguments for callbacks.
* **\*\*kwargs**: Keyword arguments for callbacks.

**Returns**: **Optional[Any]**, first result that satisfies the condition, or None.

---

### trigger_with_timeout

```python
async def trigger_with_timeout(
    self,
    event: str,
    timeout: float,
    *args,
    **kwargs
) -> List[Any]
```

Trigger with an overall time limit; on timeout, return whatever results have been collected.

**Parameters**:

* **event** (str): Event name.
* **timeout** (float): Max execution time in seconds.
* **\*args**: Arguments for callbacks.
* **\*\*kwargs**: Keyword arguments for callbacks.

**Returns**: **List[Any]**, callback results (may be incomplete on timeout).

---

### trigger_stream

```python
async def trigger_stream(
    self,
    event: str,
    input_stream: AsyncIterator[Any],
    *args,
    **kwargs
) -> AsyncIterator[Any]
```

Trigger the event for each item from an async input stream; yield results as they are produced. Callbacks may return plain values or async generators.

**Parameters**:

* **event** (str): Event name.
* **input_stream** (AsyncIterator[Any]): Async input stream.
* **\*args**: Extra arguments for callbacks.
* **\*\*kwargs**: Extra keyword arguments for callbacks.

**Yields**: Results from callback execution for each stream item.

---

### trigger_generator

```python
async def trigger_generator(self, event: str, *args, **kwargs) -> AsyncIterator[Any]
```

Trigger the event and aggregate output: iterate async generators returned by callbacks and yield their items; yield non-generator return values as single items.

**Parameters**:

* **event** (str): Event name.
* **\*args**: Arguments for callbacks.
* **\*\*kwargs**: Keyword arguments for callbacks.

**Yields**: All output from callbacks (including generator items).

---

### add_filter

```python
def add_filter(self, event: str, filter_obj: EventFilter) -> None
```

Add a filter for a specific event.

**Parameters**:

* **event** (str): Event name.
* **filter_obj** (EventFilter): Filter instance.

---

### add_global_filter

```python
def add_global_filter(self, filter_obj: EventFilter) -> None
```

Add a filter that applies to all events.

**Parameters**:

* **filter_obj** (EventFilter): Filter instance.

---

### add_circuit_breaker

```python
def add_circuit_breaker(
    self,
    event: str,
    callback: Callable,
    failure_threshold: int = 5,
    timeout: float = 60.0
) -> None
```

Add circuit breaker protection for a callback.

**Parameters**:

* **event** (str): Event name.
* **callback** (Callable): Callback to protect.
* **failure_threshold** (int, optional): Failures before opening. Default: 5.
* **timeout** (float, optional): Wait before retry in seconds. Default: 60.0.

---

### add_hook

```python
def add_hook(self, event: str, hook_type: HookType, hook: Callable) -> None
```

Add a lifecycle hook for an event.

**Parameters**:

* **event** (str): Event name.
* **hook_type** (HookType): BEFORE, AFTER, ERROR, or CLEANUP.
* **hook** (Callable): Function to run (may be async).

---

### get_metrics

```python
def get_metrics(
    self,
    event: Optional[str] = None,
    callback: Optional[str] = None
) -> Dict[str, Dict[str, Any]]
```

Get performance metrics for callbacks.

**Parameters**:

* **event** (Optional[str], optional): Filter by event.
* **callback** (Optional[str], optional): Filter by callback name.

**Returns**: **Dict[str, Dict[str, Any]]**, key to metric dict (e.g. call_count, avg_time, error_rate).

---

### reset_metrics

```python
def reset_metrics(self) -> None
```

Clear all performance metrics.

---

### get_slow_callbacks

```python
def get_slow_callbacks(self, threshold: float = 1.0) -> List[Dict[str, Any]]
```

Return callbacks whose average execution time exceeds the threshold.

**Parameters**:

* **threshold** (float, optional): Min average time in seconds. Default: 1.0.

**Returns**: **List[Dict[str, Any]]**, slow callbacks sorted by avg_time descending.

---

### enable_event_history

```python
def enable_event_history(self, enabled: bool = True) -> None
```

Enable or disable event history recording.

**Parameters**:

* **enabled** (bool, optional): Whether to record. Default: True.

---

### get_event_history

```python
def get_event_history(
    self,
    event: Optional[str] = None,
    since: Optional[datetime] = None
) -> List[Dict[str, Any]]
```

Get recorded event history.

**Parameters**:

* **event** (Optional[str], optional): Filter by event.
* **since** (Optional[datetime], optional): Only records after this time.

**Returns**: **List[Dict[str, Any]]**, event records.

---

### replay_events

```python
async def replay_events(self, since: Optional[datetime] = None) -> None
```

Replay recorded events in order.

**Parameters**:

* **since** (Optional[datetime], optional): Replay only records after this time. Default: None.

---

### save_state

```python
def save_state(self, filepath: str) -> None
```

Save framework state to a file (metadata only, not callback functions).

**Parameters**:

* **filepath** (str): File path.

---

### list_events

```python
def list_events(self, namespace: Optional[str] = None) -> List[str]
```

List registered event names.

**Parameters**:

* **namespace** (Optional[str], optional): Only events that have callbacks in this namespace.

**Returns**: **List[str]**, event names.

---

### list_callbacks

```python
def list_callbacks(self, event: str) -> List[Dict[str, Any]]
```

List metadata for all callbacks registered for an event.

**Parameters**:

* **event** (str): Event name.

**Returns**: **List[Dict[str, Any]]**, callback info (name, priority, enabled, namespace, tags, once, max_retries, timeout, etc.).

---

### get_statistics

```python
def get_statistics(self) -> Dict[str, Any]
```

Get overall framework statistics (event count, total callbacks, namespaces, filter count, chain count, history size, metrics count).

**Returns**: **Dict[str, Any]**, statistics dictionary.
