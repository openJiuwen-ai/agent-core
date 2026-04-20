# Async Callback Framework

The Async Callback Framework is a production-ready, asyncio-oriented event-driven framework in openJiuwen for registering event callbacks, priority-based execution, filtering (rate limit, circuit breaker, validation), and chained execution with rollback. The Runner embeds this framework and exposes it via `Runner.callback_framework`.

**API reference**: Full class and method details are in the [callback API docs](../API%20Docs/openjiuwen.core/runner/callback/callback.README.md).

---

## Overview

### Core features

- **Async-first**: Built on asyncio with support for high concurrency.
- **Priority control**: Higher priority runs first; use this to order critical vs. auxiliary steps.
- **Filter system**: Global, event-level, and callback-level filters; 8 built-in filters and custom filters.
- **Chains and rollback**: Multi-step execution with each step’s result passed to the next; any step can trigger rollback of previously executed steps.
- **Trigger modes**: Sequential, parallel, conditional, timeout, delayed, stream, and generator aggregation (7 modes).
- **Decorators**: `@on` for registration; `trigger_on_call`, `emits`, `emit_around`, `emits_stream`, and `transform_io` for automatic triggering and I/O transformation.
- **Metrics**: Call count, duration, error rate; slow-callback inspection.
- **Lifecycle hooks**: BEFORE (before execution), AFTER (after execution), ERROR (on error), CLEANUP (cleanup).
- **Async generators**: Stream input via `trigger_stream` and stream/multi-source aggregation via `trigger_generator` and `emits_stream`.
- **Input/output transformation**: Transform function arguments and return values via events or direct callbacks; supports normal functions and generators.

### Module layout

Framework code lives under `openjiuwen/core/runner/callback/`:

- `framework.py`: Core class `AsyncCallbackFramework`
- `enums.py`: `FilterAction`, `ChainAction`, `HookType`
- `models.py`: `CallbackMetrics`, `FilterResult`, `ChainContext`, `ChainResult`, `CallbackInfo`
- `filters.py`: Built-in filter implementations
- `chain.py`: Callback chain and rollback
- `decorator.py`: Decorator implementations

---

## Quick start

Using the framework via the Runner:

```python
import asyncio
from openjiuwen.core.runner import Runner

framework = Runner.callback_framework

@framework.on("user_login", priority=10)
async def send_welcome(username: str):
    print(f"Sending welcome to {username}")
    return f"welcome_{username}"

async def main():
    results = await framework.trigger("user_login", username="alice")
    print(results)  # ['welcome_alice']

asyncio.run(main())
```

Without the Runner, create a framework instance directly:

```python
from openjiuwen.core.runner.callback import AsyncCallbackFramework

framework = AsyncCallbackFramework(enable_metrics=True)
@framework.on("my_event", priority=5)
async def handler(x: int):
    return x * 2

results = await framework.trigger("my_event", 3)  # [6]
```

---

## Callback registration (decorator way)

The decorator way is the most commonly used registration method, directly registering functions as callbacks through `@framework.on()` or `@framework.on_chain()` decorators.

### @framework.on: Regular event callback

Used to register regular event callbacks, supporting all trigger modes.

```python
@framework.on("user_login", priority=10, once=False, namespace="auth", tags={"user", "login"})
async def handle_login(username: str, **kwargs):
    print(f"User {username} logged in")
    return f"welcome_{username}"
```

**Parameter Description**:
- `event`: Event name, used to bind callback functions
- `priority`: Execution priority, higher numbers execute first (default: 0)
- `once`: Whether to execute only once, automatically disabled after execution (default: False)
- `namespace`: Namespace, used for grouping and managing callbacks (default: "default")
- `tags`: Tag set, used for classification and batch management (default: None)
- `filters`: Callback-specific filters (invalid in callback chains, default: None)
- `rollback_handler`: Rollback processing function (only valid in callback chains, default: None)
- `error_handler`: Error handling function (only valid in callback chains, default: None)
- `max_retries`: Maximum number of retries (default: 0)
- `retry_delay`: Retry delay time in seconds (default: 0.0)
- `timeout`: Execution timeout time in seconds (default: None)
- `callback_type`: Semantic type marker, e.g., "transform" (default: "")

### @framework.on_chain: Callback chain specific

Specifically used to register callbacks for callback chains, automatically setting `filters=None` (callback chains don't support filters).

```python
@framework.on_chain("order_process", priority=30, rollback_handler=rollback_payment)
async def process_payment(order: dict, **kwargs):
    order["paid"] = True
    return ChainResult(ChainAction.CONTINUE, result=order)

@framework.on_chain("order_process", priority=20, rollback_handler=rollback_inventory)
async def reserve_inventory(order: dict, **kwargs):
    if order.get("quantity", 0) > 100:
        return ChainResult(ChainAction.ROLLBACK, error=Exception("Insufficient inventory"))
    order["reserved"] = True
    return ChainResult(ChainAction.CONTINUE, result=order)
```

**Parameter Description**:
- `event`: Event name, used to bind callback functions
- `priority`: Execution priority, higher numbers execute first (default: 0)
- `once`: Whether to execute only once, automatically disabled after execution (default: False)
- `namespace`: Namespace, used for grouping and managing callbacks (default: "default")
- `tags`: Tag set, used for classification and batch management (default: None)
- `rollback_handler`: Rollback processing function, called in reverse order when chain execution fails (only valid in callback chains, default: None)
- `error_handler`: Error handling function, called when callback execution fails (only valid in callback chains, default: None)
- `max_retries`: Maximum number of retries (default: 0)
- `retry_delay`: Retry delay time in seconds (default: 0.0)
- `timeout`: Execution timeout time in seconds (default: None)
- `callback_type`: Semantic type marker, e.g., "transform" (default: "")

> **Note**: `@framework.on_chain` automatically sets `filters=None` because the current version does not support filters for callback chains.

## Dynamic callback registration (non-decorator way)

Besides the `@framework.on(...)` and `@framework.on_chain(...)` decorators, you can register callbacks at runtime with **register**, so you can add or
remove handlers based on configuration, plugins, or conditions.

### When to use

- **Conditional registration at runtime**: Register (or skip) a callback based on config flags, environment variables,
  or user permissions.
- **Plugins / extensions**: When a plugin loads, register its async handlers with the framework without using decorators
  at definition site.
- **Config-driven**: Read event names and callback references/paths from config or a database and call **register** in
  bulk at startup or reload.
- **Temporary registration**: Register for a single request or session, then call **unregister** when done to avoid
  keeping callbacks around.

### Using register

**register** is async and takes the same parameters as `@framework.on()`:

```python
await framework.register(
    "event_name",           # event name
    async_callback_func,    # async callable
    priority=0,
    once=False,
    namespace="default",
    tags=None,              # e.g. {"tag1", "tag2"}
    filters=None,           # e.g. [RateLimitFilter(...)]
    rollback_handler=None,
    error_handler=None,
    max_retries=0,
    retry_delay=0.0,
    timeout=None,
)
```

Example: registering multiple callbacks from config

```python
import asyncio
from openjiuwen.core.runner import Runner

framework = Runner.callback_framework

async def notify_email(user: str, **kwargs):
    print(f"[Email] Notify {user}")
    return "email_ok"

async def notify_sms(user: str, **kwargs):
    print(f"[SMS] Notify {user}")
    return "sms_ok"

# From config or DB: which channels are enabled at runtime
enabled_channels = ["email", "sms"]

channel_handlers = {
    "email": (notify_email, 20),
    "sms": (notify_sms, 10),
}

async def setup_callbacks():
    for ch in enabled_channels:
        if ch in channel_handlers:
            handler, priority = channel_handlers[ch]
            await framework.register("user_signup", handler, priority=priority, namespace="channels")
    print("Callbacks registered.")

async def main():
    await setup_callbacks()
    results = await framework.trigger("user_signup", user="alice")
    print(results)  # e.g. ['email_ok', 'sms_ok']

asyncio.run(main())
```

### Unregistering

- **unregister(event, callback)**: Remove one callback from an event (you can pass the original function or the wrapper
  returned by `@on`).
- **unregister_namespace(namespace)**: Remove all callbacks in that namespace.
- **unregister_by_tags(tags)**: Remove callbacks that have any of the given tags.
- **unregister_event(event)**: Remove all callbacks for the event and clear its filters, chain, hooks, and circuit
  breakers.

If you registered at runtime and want to remove callbacks later, keep a reference and call **unregister** when done:

```python
await framework.register("temp_event", my_async_handler, namespace="temp")
# ... use ...
await framework.unregister("temp_event", my_async_handler)

# Or remove by namespace
await framework.unregister_namespace("temp")
```

---

## Core concepts

### Events and callbacks

- **Event**: A string identifier (e.g. `"user_login"`, `"order.created"`) that groups callbacks.
- **Callback**: An async function that runs when the event is triggered, in priority order. Multiple callbacks can be registered for the same event.
- **Priority**: In `@framework.on(event, priority=N)`, higher N runs first; default is 0.

Prefer meaningful, dot-separated event names (e.g. `user.login`, `order.created`) and avoid generic names like `event1` or `do_something`.

### Execution flow (sequential trigger)

1. Check if the event is registered; if not, return an empty list.
2. Sort callbacks for that event by priority.
3. Run **BEFORE** hooks.
4. For each callback: if disabled, skip; otherwise run **global filters → event filters → callback-specific filters**.
5. A filter can return **CONTINUE**, **STOP** (abort entire trigger), **SKIP** (skip this callback), or **MODIFY** (continue with new arguments).
6. Execute the callback and collect the result; if `once=True`, disable the callback after execution.
7. If a callback raises, run **ERROR** hooks and continue with the next callback (the trigger is not aborted).
8. When done, run **AFTER** hooks; **CLEANUP** hooks run in a finally block.

### Filter hierarchy

- **Global filters**: `add_global_filter(filter)` — apply to all events (e.g. logging, monitoring).
- **Event filters**: `add_filter(event, filter)` — apply only to that event (e.g. rate limit, auth).
- **Callback filters**: Passed in `@framework.on(event, filters=[...])` — apply only to that callback.

Order of application: global → event → callback. Any layer can return STOP or SKIP to short-circuit or skip.

### Chain execution and rollback

- With **trigger_chain**, callbacks run in priority order; the **return value of the previous callback** is the **first positional argument** of the next; other `initial_kwargs` are unchanged. Use `kwargs["_chain_context"]` to access **ChainContext** (e.g. `get_last_result()`, `set_metadata()`).
- Each callback can return **ChainResult(ChainAction.CONTINUE, result=...)** to continue or **ChainResult(ChainAction.ROLLBACK, error=...)** to roll back.
- **ChainAction**: CONTINUE (next callback), BREAK (stop chain and return current result), RETRY (retry current callback), ROLLBACK (roll back).
- On ROLLBACK or callback exception, the framework invokes registered **rollback_handler**s in **reverse execution order** (e.g. refund, restore inventory).

---

## Trigger modes

| Method | Description | Use case |
|--------|-------------|----------|
| **trigger** | Run in priority order and collect all results | Default; when order or dependencies matter |
| **trigger_parallel** | Run concurrently; much faster for I/O-bound work | Multiple fetches, parallel requests |
| **trigger_until** | Run in order until a callback return satisfies condition(result); return that result | Fallback, primary/backup |
| **trigger_chain** | Chain execution with result passing and rollback | Order/payment-style flows |
| **trigger_with_timeout** | Run trigger with an overall time limit; on timeout return collected results | Bounded time, SLA |
| **trigger_delayed** | Run trigger after a delay in seconds | Scheduled or delayed tasks |
| **trigger_stream** | Trigger for each item of an async input stream and stream results | Large files, log streams, sensor data |
| **trigger_generator** | Run all callbacks and aggregate async generator or plain return values, yielding items | Multi-source aggregation, streaming API |

**Performance**: Prefer **trigger_parallel** for I/O-bound work; use **trigger** when CPU-bound or when there is shared state. Parallel can be about 2–4× faster in typical I/O cases (e.g. 3 callbacks × 0.15s: ~0.45s sequential vs ~0.2s parallel).

---

## Streaming and generators

### trigger_stream: streaming input

Trigger the event once per item from an **async input stream**, run callbacks per item, and stream results without loading everything into memory.

- Use for: line- or chunk-based file processing, real-time log analysis, streaming pipelines.
- Callbacks can return a plain value or an async generator; if a generator is returned, each yielded item is produced as output for that trigger.
- A single item failure does not stop the stream; the error is logged and metered, and that item produces no output.

Example: streaming log processing

```python
async def log_stream():
    with open("app.log") as f:
        for line in f:
            yield line.strip()

@framework.on("process_log")
async def analyze_log(line: str):
    if "ERROR" in line:
        return {"level": "error", "line": line}
    return {"level": "info", "line": line}

async for analysis in framework.trigger_stream("process_log", log_stream()):
    if analysis["level"] == "error":
        await alert_admin(analysis)
```

### trigger_generator: generator output aggregation

Run all callbacks for an event; if a callback returns an **async generator**, iterate it; otherwise treat the return value as a single item. Results are yielded in priority order for multi-source aggregation.

- Use for: multi-source aggregation (DB + cache + API), streaming API responses, report chunking.

### emits_stream: decorator stream trigger

Decorates an **async generator function** and triggers the given event on each **yield**, passing the yielded item as a keyword argument (default key `item`); the generator still yields to the caller as usual.

- Use for: real-time metrics, log streaming, progress, event sourcing.

Streaming vs batch: O(1) memory, low time-to-first-byte, unbounded data, real-time.

---

## Filters

### Built-in filters

| Filter | Purpose | Typical use |
|--------|---------|-------------|
| **RateLimitFilter** | Limit calls within a time window | Throttling |
| **CircuitBreakerFilter** | Open circuit after failure threshold; retry after timeout | Protecting downstream |
| **ValidationFilter** | Validate arguments with a custom function | Argument checks |
| **LoggingFilter** | Log event, callback name, arguments | Audit, debugging |
| **AuthFilter** | Require `user_role` in kwargs to match a role | Role-based access |
| **ParamModifyFilter** | modifier(*args, **kwargs) → (new_args, new_kwargs) | Transform or enrich args |
| **ConditionalFilter** | condition(event, callback, *args, **kwargs) → CONTINUE or action_on_false | Conditional execution |

All are in `openjiuwen.core.runner.callback` and extend **EventFilter**. Custom filters subclass **EventFilter** and implement `async def filter(self, event, callback, *args, **kwargs) -> FilterResult`.

### Example: rate limit and auth

```python
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback import RateLimitFilter, AuthFilter

framework = Runner.callback_framework
framework.add_filter("api_call", RateLimitFilter(max_calls=100, time_window=60.0))
framework.add_filter("admin_action", AuthFilter(required_role="admin"))

@framework.on("admin_action")
async def do_admin(user_id: str, user_role: str):
    return f"admin_ok_{user_id}"

await framework.trigger("admin_action", user_id="u1", user_role="admin")
```

### Circuit breaker

Use **add_circuit_breaker(event, callback, failure_threshold=5, timeout=60.0)** to attach a circuit breaker to a callback; the framework calls `record_success` / `record_failure` on success/failure. After the threshold, the callback is skipped until the timeout has passed.

---

## Callback chain and rollback

Use **trigger_chain** with **ChainResult** / **ChainAction** for multi-step flows; any step returning **ChainAction.ROLLBACK** or raising triggers **rollback_handler**s in **reverse order** (e.g. refund, restore inventory).

```python
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback import ChainAction, ChainResult

framework = Runner.callback_framework

async def rollback_payment(ctx):
    print("Rollback payment")

async def rollback_inventory(ctx):
    print("Rollback inventory")

@framework.on_chain("order_create", priority=30, rollback_handler=rollback_payment)
async def pay(order: dict, **kwargs):
    order["paid"] = True
    return ChainResult(ChainAction.CONTINUE, result=order)

@framework.on_chain("order_create", priority=20, rollback_handler=rollback_inventory)
async def reserve(order: dict, **kwargs):
    if order.get("quantity", 0) > 100:
        return ChainResult(ChainAction.ROLLBACK, error=Exception("Insufficient stock"))
    order["reserved"] = True
    return ChainResult(ChainAction.CONTINUE, result=order)

result = await framework.trigger_chain("order_create", order={"quantity": 150})
if result.action == ChainAction.ROLLBACK:
    print("Order failed, rolled back")
```

Use `kwargs["_chain_context"]` to get **ChainContext** and pass data between steps with `get_last_result()`, `set_metadata(key, value)`, `get_metadata(key)`.

---

## Decorators

### Registration and auto-trigger

- **@framework.on(event, priority=0, once=False, namespace="default", tags=None, filters=None, rollback_handler=None, error_handler=None, max_retries=0, retry_delay=0.0, timeout=None, callback_type="")**
  Register a function as a callback for the event. Higher priority runs first; `once=True` disables after one run; `error_handler` is called when the callback raises; `callback_type` is a semantic marker — `"transform"` marks the callback as transform-only.
  
  > **Note**: The `filters` parameter only takes effect when using regular trigger modes like `trigger` and `trigger_parallel`; it is not applied when using `trigger_chain`.

- **@framework.on_chain(event, priority=0, once=False, namespace="default", tags=None, rollback_handler=None, error_handler=None, max_retries=0, retry_delay=0.0, timeout=None, callback_type="")**
  Decorator specifically for callback chains. Similar to `@framework.on()` but automatically sets `filters=None` (callback chains don't support filters), and is more suitable for configuring rollback and error handling functions.
  
  > **Note**: The `filters` parameter only takes effect when using regular trigger modes like `trigger` and `trigger_parallel`; it is not applied when using `trigger_chain`.

- **@framework.on_chain(event, priority=0, once=False, namespace="default", tags=None, rollback_handler=None, error_handler=None, max_retries=0, retry_delay=0.0, timeout=None, callback_type="")**
  Decorator specifically for callback chains. Similar to `@framework.on()` but automatically sets `filters=None` (callback chains don't support filters), and is more suitable for configuring rollback and error handling functions.

- **@framework.trigger_on_call(event, pass_result=False, pass_args=True)**
  Trigger the event when the decorated function is **called** (optionally pass args or trigger again with result after return).

- **@framework.emits(event, result_key="result", include_args=False)**
  Trigger the event **after** the function returns, passing the return value under `result_key`; optionally include original args.

- **@framework.emit_around(before_event, after_event, pass_args=True, pass_result=True, on_error_event=None)**
  Trigger before_event before execution and after_event after success; if the function raises and on_error_event is set, trigger that event.

- **@framework.emits_stream(event, item_key="item")**
  Decorate an **async generator**; trigger the event on each yield and pass the item under `item_key`.

- **@framework.on_transform(event, *, priority=0)**
  Convenience decorator for registering transform-type callbacks. Equivalent to `@framework.on(event, callback_type="transform")`. Only triggered by `trigger_transform` and the event mode of `transform_io`; a regular `trigger` call will not invoke these callbacks.

### transform_io: input/output transformation

Transform a function’s **arguments** and **return value** in two ways:

1. **Event mode**: Set `input_event` / `output_event`. Before calling the function, trigger input_event; callbacks receive `(*args, **kwargs)` and return `(new_args, new_kwargs)`; the framework uses the last callback result as the new arguments. After return (or per generator item), trigger output_event; callbacks receive `result_key=value` and return the new value; the framework uses the last result as the final result. **Important**: event mode only triggers callbacks with `callback_type="transform"`, so you must register them with `@framework.on_transform(event)` or `@framework.on(event, callback_type="transform")`; callbacks registered with plain `@framework.on` are not invoked by `transform_io`.
2. **Direct callback mode**: Set `input_transform` / `output_transform`. `input_transform(*args, **kwargs)` returns `(new_args, new_kwargs)`; `output_transform(value)` returns the new value. Sync/async supported.

`output_mode` controls how the output transform is applied to generator functions:

* **`’frame’`** (default): `output_transform(item)` is called once per yielded item (or output_event fires once per item in event mode).
* **`’generator’`**: The entire source async iterator is handed to `output_transform` once. In direct callback mode, `output_transform` must be an async generator function that iterates the source and yields new items — enabling filtering, 1-to-N expansion, and stateful transforms. In event mode, the output callback receives the source and returns an async iterable. Sync generator functions are automatically promoted to async. Raises `ValueError` for non-generator functions.

Use for normalizing arguments, serializing results, or shared encrypt/decrypt. Example (event mode):

```python
@framework.on_transform("transform_input")
async def normalize_input(*args, **kwargs):
    return (args, {**kwargs, "limit": kwargs.get("limit", 10)})

@framework.on_transform("transform_output")
async def serialize_output(result):
    return json.dumps(result) if isinstance(result, dict) else result

@framework.transform_io(
    input_event="transform_input",
    output_event="transform_output",
)
async def fetch_data(limit: int):
    return {"count": limit}
```

Example (generator mode — direct callback):

```python
async def expand_chunks(source):
    """Split each chunk into individual tokens."""
    async for chunk in source:
        for token in chunk.split():
            yield token

@framework.transform_io(output_transform=expand_chunks, output_mode="generator")
async def stream_response():
    yield "hello world"
    yield "foo bar baz"

# yields: "hello", "world", "foo", "bar", "baz"
```

---

## Hooks and metrics

### Lifecycle hooks

- **HookType.BEFORE**: Before any callback for the event runs.
- **HookType.AFTER**: After all callbacks have run (receives result list, etc.).
- **HookType.ERROR**: When a callback raises (receives exception, etc.).
- **HookType.CLEANUP**: In a finally block for cleanup.

Register with **add_hook(event, hook_type, hook)**; hook can be sync or async.

### Metrics and slow callbacks

- **get_metrics(event=None, callback=None)**: Returns call_count, avg_time, min_time, max_time, error_count, error_rate, last_call_time, etc., per callback.
- **reset_metrics()**: Clear all metrics.
- **get_slow_callbacks(threshold=1.0)**: Returns callbacks whose average time exceeds the threshold for performance tuning.

Keep `enable_metrics=True` (default) for important events and review slow callbacks periodically.

---

## Tool / LLM / Workflow invoke/stream interception

`Tool`, `BaseModelClient`, and `Workflow` automatically wrap their `invoke` and `stream` methods into corresponding `transform_io` pipelines at instantiation. Registering transform callbacks for the relevant events intercepts all instances globally.

### Available events

| Class | Input event | Output event |
|-------|-------------|--------------|
| `Tool.invoke` | `ToolCallEvents.TOOL_INVOKE_INPUT` | `ToolCallEvents.TOOL_INVOKE_OUTPUT` |
| `Tool.stream` | `ToolCallEvents.TOOL_STREAM_INPUT` | `ToolCallEvents.TOOL_STREAM_OUTPUT` |
| `BaseModelClient.invoke` | `LLMCallEvents.LLM_INVOKE_INPUT` | `LLMCallEvents.LLM_INVOKE_OUTPUT` |
| `BaseModelClient.stream` | `LLMCallEvents.LLM_STREAM_INPUT` | `LLMCallEvents.LLM_STREAM_OUTPUT` |
| `Workflow.invoke` | `WorkflowEvents.WORKFLOW_INVOKE_INPUT` | `WorkflowEvents.WORKFLOW_INVOKE_OUTPUT` |
| `Workflow.stream` | `WorkflowEvents.WORKFLOW_STREAM_INPUT` | `WorkflowEvents.WORKFLOW_STREAM_OUTPUT` |

Input callbacks receive `(*args, **kwargs)` and return `(new_args, new_kwargs)`; output callbacks receive `result=<value>` and return the new value; for stream methods, the output event fires once per yielded item.

### Example

```python
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.events import ToolCallEvents

fw = Runner.callback_framework

# Intercept inputs for all Tool.invoke calls
@fw.on_transform(ToolCallEvents.TOOL_INVOKE_INPUT)
async def preprocess(inputs, **kwargs):
    # inputs is the first positional argument of Tool.invoke
    return ((inputs,), kwargs)

# Intercept the return value for all Tool.invoke calls
@fw.on_transform(ToolCallEvents.TOOL_INVOKE_OUTPUT)
async def postprocess(result):
    return result  # pass through, or return a modified value
```

Callbacks apply to **all** Tool/LLM/Workflow instances without modifying any instance code.

---

## Using with the Runner

After the Runner has started, **Runner.callback_framework** is the same framework instance. Register events around Agent/Workflow execution (e.g. `workflow_started`, `agent_finished`) for logging, metrics, auditing, or extending the flow. Runner configuration remains **Runner.set_config** / **Runner.get_config** and is separate from the callback framework.

---

## Best practices

1. **Event naming**: Use meaningful, dot-separated names (e.g. `user.login`, `order.created`); avoid generic names.
2. **Priority**: Use high priority for critical steps (auth, validation) and lower for auxiliary ones (notifications, stats).
3. **I/O vs CPU**: Use **trigger_parallel** for I/O-bound work; use **trigger** when CPU-bound or when there is shared state.
4. **Namespace and tags**: Use `namespace` and `tags` to group callbacks and **unregister_namespace** / **unregister_by_tags** for bulk management.
5. **Error handling**: Set **error_handler** for callbacks that may fail, or use **rollback_handler** in chains for consistency.
6. **Transactional flows**: Use **trigger_chain** and **rollback_handler** when multiple steps must all succeed or all roll back.
7. **Monitoring**: Use **get_slow_callbacks(threshold)** regularly and set **timeout** on callbacks when appropriate.

---

## FAQ

- **When to use trigger vs trigger_parallel?**  
  Use trigger when there are ordering dependencies or shared state; use trigger_parallel when callbacks are independent and I/O-bound.

- **Filter execution order?**  
  Global filters → event filters → callback filters; any layer can return STOP (abort trigger) or SKIP (skip current callback).

- **How is data passed along the chain?**  
  The next callback’s first positional argument is the previous callback’s return value; use `kwargs["_chain_context"]` with `set_metadata` / `get_metadata` and `get_last_result()` for more state.

- **How to run only when a condition holds?**  
  Use **ConditionalFilter** or **trigger_until** to stop at the first result that satisfies a condition.

- **What happens when one item fails in stream processing?**  
  That item does not stop the stream; the error is logged and metered, that item produces no output, and processing continues for the next items.

---

## Reference

- **API details**: [callback API docs](../API%20Docs/openjiuwen.core/runner/callback/callback.README.md).
- **Source**: [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository, `openjiuwen/core/runner/callback/` directory.