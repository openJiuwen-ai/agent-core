# openjiuwen.core.runner.callback.framework

生产级异步回调框架，专为 asyncio 设计，支持优先级执行、过滤、链式执行与回滚、指标与生命周期钩子。

## class AsyncCallbackFramework

```python
class AsyncCallbackFramework(enable_metrics: bool = True, enable_logging: bool = True)
```

异步事件驱动框架，提供回调注册与多种触发方式、过滤、链与回滚、指标与钩子。所有操作均为异步。

**参数**：

* **enable_metrics**(bool, 可选)：是否收集性能指标。默认值：True。
* **enable_logging**(bool, 可选)：是否输出日志。默认值：True。

### callbacks

```python
callbacks -> Dict[str, List[CallbackInfo]]
```

事件名到回调信息列表的映射（只读）。

### chains

```python
chains -> Dict[str, CallbackChain]
```

事件名到回调链的映射（只读）。

### circuit_breakers

```python
circuit_breakers -> Dict[str, CircuitBreakerFilter]
```

熔断器 key 到实例的映射（只读）。

### callback_filters

```python
callback_filters -> Dict[Callable, List[EventFilter]]
```

回调到其专属过滤器列表的映射（只读）。

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
    max_retries: int = 0,
    retry_delay: float = 0.0,
    timeout: Optional[float] = None,
    callback_type: str = ""
)
```

装饰器：将异步函数注册为某事件的回调。数值越大优先级越高。

**参数**：

* **event**(str)：事件名。
* **priority**(int, 可选)：优先级。默认值：0。
* **once**(bool, 可选)：是否只执行一次。默认值：False。
* **namespace**(str, 可选)：命名空间。默认值："default"。
* **tags**(Optional[Set[str]], 可选)：标签。默认值：None。
* **filters**(Optional[List[EventFilter]], 可选)：该回调专属过滤器。默认值：None。
* **max_retries**(int, 可选)：最大重试次数。默认值：0。
* **retry_delay**(float, 可选)：重试间隔（秒）。默认值：0.0。
* **timeout**(Optional[float], 可选)：单次执行超时（秒）。默认值：None。
* **callback_type**(str, 可选)：语义类型标记，如 "transform"、"chain"。默认值：""。

**返回**：

* 装饰器函数。

---

### on_transform

```python
def on_transform(self, event: str, *, priority: int = 0)
```

装饰器：注册 transform 类型回调的便捷方式，等价于 `on(event, callback_type="transform", priority=priority)`。仅被 `trigger_transform` 和 `transform_io` 的事件模式触发；普通 `trigger` 不会调用此类回调。

**参数**：

* **event**(str)：事件名。
* **priority**(int, 可选)：优先级。默认值：0。

**返回**：

* 装饰器函数。

---

### on_chain

```python
def on_chain(
    self,
    event: str,
    *,
    priority: int = 0,
    once: bool = False,
    namespace: str = "default",
    tags: Optional[Set[str]] = None,
    rollback_handler: Optional[Callable] = None,
    error_handler: Optional[Callable] = None,
    max_retries: int = 0,
    retry_delay: float = 0.0,
    timeout: Optional[float] = None,
    callback_type: str = ""
)
```

装饰器：专门用于回调链的异步函数注册。与`on()`方法类似，但专为回调链设计。

**参数**：

* **event**(str)：事件名。
* **priority**(int, 可选)：优先级。默认值：0。
* **once**(bool, 可选)：是否只执行一次。默认值：False。
* **namespace**(str, 可选)：命名空间。默认值："default"。
* **tags**(Optional[Set[str]], 可选)：标签。默认值：None。
* **rollback_handler**(Optional[Callable], 可选)：回滚时调用的函数。默认值：None。
* **error_handler**(Optional[Callable], 可选)：出错时调用的函数。默认值：None。
* **max_retries**(int, 可选)：最大重试次数。默认值：0。
* **retry_delay**(float, 可选)：重试间隔（秒）。默认值：0.0。
* **timeout**(Optional[float], 可选)：单次执行超时（秒）。默认值：None。
* **callback_type**(str, 可选)：语义类型标记。默认值：""。

**返回**：

* 装饰器函数。

---

### trigger_on_call

```python
def trigger_on_call(
    self,
    event: str,
    pass_result: bool = False,
    pass_args: bool = True
)
```

装饰器：在被装饰函数被调用时触发指定事件（在函数执行前触发；若 `pass_result` 为 True 则在执行后再触发一次并传入结果）。

**参数**：

* **event**(str)：要触发的事件名。
* **pass_result**(bool, 可选)：是否把函数返回值传给回调。默认值：False。
* **pass_args**(bool, 可选)：是否把函数参数传给回调。默认值：True。

**返回**：

* 装饰器函数。

---

### emits

```python
def emits(
    self,
    event: str,
    result_key: str = "result",
    include_args: bool = False
)
```

装饰器：在被装饰函数**返回后**触发事件，并以 `result_key` 为键将返回值传给回调。

**参数**：

* **event**(str)：要触发的事件名。
* **result_key**(str, 可选)：传参时的键名。默认值："result"。
* **include_args**(bool, 可选)：是否同时传入原始参数。默认值：False。

**返回**：

* 装饰器函数。

---

### emits_stream

```python
def emits_stream(self, event: str, item_key: str = "item") -> Callable
```

装饰器：用于装饰**异步生成器**；每次 `yield` 时自动触发事件，并以 `item_key` 为键将该项传给回调，原生成器仍向调用方正常 yield。

**参数**：

* **event**(str)：每次 yield 时触发的事件名。
* **item_key**(str, 可选)：传参键名。默认值："item"。

**返回**：

* 装饰后的异步生成器函数。

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

装饰器：在函数执行前触发 `before_event`，执行成功后触发 `after_event`；若发生异常且指定了 `on_error_event` 则触发该事件。

**参数**：

* **before_event**(str)：执行前触发的事件。
* **after_event**(str)：执行成功后触发的事件。
* **pass_args**(bool, 可选)：是否向事件传递函数参数。默认值：True。
* **pass_result**(bool, 可选)：是否向 after_event 传递返回值。默认值：True。
* **on_error_event**(Optional[str], 可选)：出错时触发的事件。默认值：None。

**返回**：

* 装饰器函数。

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
    output_mode: Literal["frame", "generator"] = "frame",
)
```

装饰器：在调用被装饰函数前后对入参和返回值做变换。支持两种方式：**事件模式**（设置 `input_event`/`output_event`，由框架触发事件并用最后一个回调返回值作为变换后的入参或结果）；**直接回调模式**（设置 `input_transform`/`output_transform`，`input_transform(*args, **kwargs)` 返回 `(new_args, new_kwargs)`，`output_transform(value)` 返回新值）。支持普通异步函数与异步生成器。

`output_mode` 控制对生成器函数的输出变换方式：

* **`'frame'`**（默认）：逐帧模式，对每个 yield 项单独调用 output 变换；事件模式下 output_event 对每项触发一次。
* **`'generator'`**：生成器模式，将整个 source 异步迭代器一次性传给 output 变换；直接回调模式下 `output_transform` 须为异步生成器函数 `(source: AsyncIterator) -> AsyncIterator`，可对每项 yield 0/1/多次，支持过滤、展开、有状态变换；事件模式下 output_event 触发一次并以 source 为参数，回调应返回可迭代对象；同步生成器自动提升为异步。对非生成器函数使用时抛出 `ValueError`。

**参数**：

* **input_event**(Optional[str], 可选)：输入变换事件名（与 input_transform 二选一）。默认值：None。
* **output_event**(Optional[str], 可选)：输出变换事件名（与 output_transform 二选一）。默认值：None。
* **result_key**(str, 可选)：输出事件触发时传参的键名。默认值："result"。
* **input_transform**(InputTransform, 可选)：直接输入变换回调。默认值：None。
* **output_transform**(OutputTransform, 可选)：直接输出变换回调。默认值：None。
* **output_mode**(Literal["frame", "generator"], 可选)：生成器函数的输出变换模式。默认值："frame"。

**返回**：

* 应用输入/输出变换的装饰器。

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

以编程方式为事件注册异步回调。参数含义与 `on` 装饰器一致。

---

### unregister

```python
async def unregister(self, event: str, callback: Callable) -> None
```

从事件上注销一个回调。支持传入原始回调或 `@on` 返回的包装函数。

**参数**：

* **event**(str)：事件名。
* **callback**(Callable)：要移除的回调（原函数或包装函数）。

---

### unregister_namespace

```python
async def unregister_namespace(self, namespace: str) -> None
```

注销指定命名空间下的所有回调。

**参数**：

* **namespace**(str)：命名空间。

---

### unregister_by_tags

```python
async def unregister_by_tags(self, tags: Set[str]) -> None
```

注销带有给定标签中任意一个的回调。

**参数**：

* **tags**(Set[str])：要匹配的标签集合。

---

### unregister_event

```python
async def unregister_event(self, event: str) -> None
```

注销某事件的全部回调，并移除该事件关联的过滤器、链、钩子和熔断器。

**参数**：

* **event**(str)：事件名。

---

### trigger

```python
async def trigger(self, event: str, *args, **kwargs) -> List[Any]
```

按优先级顺序触发事件，执行所有已注册回调（经过滤、指标与钩子），并收集结果。

**参数**：

* **event**(str)：事件名。
* **\*args**、**\*\*kwargs**：传给回调的参数。

**返回**：

* **List[Any]**，所有被执行回调的返回值列表。

---

### trigger_transform

```python
async def trigger_transform(self, event: str, *args, **kwargs) -> Any
```

仅触发 `callback_type="transform"` 的回调，返回最后一个回调的结果。无 transform 回调时返回内部哨兵值，调用方应据此透传原始值。通常不需要直接调用此方法；`transform_io` 事件模式在内部使用它。

**参数**：

* **event**(str)：事件名。
* **\*args**、**\*\*kwargs**：传给回调的参数。

**返回**：

* **Any**，最后一个 transform 回调的返回值；若无 transform 回调则返回内部哨兵值。

---

### trigger_delayed

```python
async def trigger_delayed(self, event: str, delay: float, *args, **kwargs) -> List[Any]
```

延迟指定秒数后触发事件。

**参数**：

* **event**(str)：事件名。
* **delay**(float)：延迟时间（秒）。
* **\*args**、**\*\*kwargs**：传给回调的参数。

**返回**：

* **List[Any]**，回调结果列表。

---

### trigger_chain

```python
async def trigger_chain(self, event: str, *args, **kwargs) -> ChainResult
```

按链方式触发：按优先级顺序执行回调，将上一回调的返回值作为下一回调的输入；支持回滚。

**参数**：

* **event**(str)：事件名。
* **\*args**、**\*\*kwargs**：链的初始参数。

**返回**：

* **ChainResult**，包含最终动作、结果、上下文及可能的异常。

---

### trigger_parallel

```python
async def trigger_parallel(self, event: str, *args, **kwargs) -> List[Any]
```

并行触发：使用 asyncio.gather 并发执行所有回调，适用于 I/O 密集型场景。

**参数**：

* **event**(str)：事件名。
* **\*args**、**\*\*kwargs**：传给回调的参数。

**返回**：

* **List[Any]**，成功执行的回调结果列表（失败的不包含）。

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

按优先级依次执行回调，直到某个返回值使 `condition(result)` 为 True 则停止并返回该结果。

**参数**：

* **event**(str)：事件名。
* **condition**(Callable[[Any], bool])：接收回调返回值并返回 bool 的函数。
* **\*args**、**\*\*kwargs**：传给回调的参数。

**返回**：

* **Optional[Any]**，第一个满足条件的结果，若无则返回 None。

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

在限定总时间内触发事件；超时则中止并返回已收集的结果。

**参数**：

* **event**(str)：事件名。
* **timeout**(float)：最大执行时间（秒）。
* **\*args**、**\*\*kwargs**：传给回调的参数。

**返回**：

* **List[Any]**，回调结果列表（超时可能不完整）。

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

对异步输入流中的每一项触发事件，逐项执行回调并流式产出结果；支持回调返回普通值或异步生成器。

**参数**：

* **event**(str)：事件名。
* **input_stream**(AsyncIterator[Any])：异步输入流。
* **\*args**、**\*\*kwargs**：额外传给回调的参数。

**Yields**：

* 每项经回调处理后的结果。

---

### trigger_generator

```python
async def trigger_generator(self, event: str, *args, **kwargs) -> AsyncIterator[Any]
```

触发事件并聚合所有回调的产出：若回调返回异步生成器则迭代其产出，否则将其返回值作为单项产出。

**参数**：

* **event**(str)：事件名。
* **\*args**、**\*\*kwargs**：传给回调的参数。

**Yields**：

* 所有回调的产出（含生成器产生的每一项）。

---

### add_filter

```python
def add_filter(self, event: str, filter_obj: EventFilter) -> None
```

为指定事件添加过滤器。

**参数**：

* **event**(str)：事件名。
* **filter_obj**(EventFilter)：过滤器实例。

---

### add_global_filter

```python
def add_global_filter(self, filter_obj: EventFilter) -> None
```

添加全局过滤器（对所有事件生效）。

**参数**：

* **filter_obj**(EventFilter)：过滤器实例。

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

为指定事件的某个回调添加熔断保护。

**参数**：

* **event**(str)：事件名。
* **callback**(Callable)：要保护的回调。
* **failure_threshold**(int, 可选)：打开熔断的失败次数。默认值：5。
* **timeout**(float, 可选)：恢复尝试前的等待时间（秒）。默认值：60.0。

---

### add_hook

```python
def add_hook(self, event: str, hook_type: HookType, hook: Callable) -> None
```

为事件添加生命周期钩子。

**参数**：

* **event**(str)：事件名。
* **hook_type**(HookType)：BEFORE / AFTER / ERROR / CLEANUP。
* **hook**(Callable)：要执行的函数（可为异步）。

---

### get_metrics

```python
def get_metrics(
    self,
    event: Optional[str] = None,
    callback: Optional[str] = None
) -> Dict[str, Dict[str, Any]]
```

获取回调性能指标。

**参数**：

* **event**(Optional[str], 可选)：按事件筛选。
* **callback**(Optional[str], 可选)：按回调名筛选。

**返回**：

* **Dict[str, Dict[str, Any]]**，key 到指标字典的映射（如 call_count、avg_time、error_rate 等）。

---

### reset_metrics

```python
def reset_metrics(self) -> None
```

清空所有性能指标。

---

### get_slow_callbacks

```python
def get_slow_callbacks(self, threshold: float = 1.0) -> List[Dict[str, Any]]
```

返回平均执行时间超过阈值的回调信息。

**参数**：

* **threshold**(float, 可选)：最小平均时间（秒）。默认值：1.0。

**返回**：

* **List[Dict[str, Any]]**，按 avg_time 降序排列的慢回调列表。

---

### enable_event_history

```python
def enable_event_history(self, enabled: bool = True) -> None
```

开启或关闭事件历史记录。

**参数**：

* **enabled**(bool, 可选)：是否记录。默认值：True。

---

### get_event_history

```python
def get_event_history(
    self,
    event: Optional[str] = None,
    since: Optional[datetime] = None
) -> List[Dict[str, Any]]
```

获取已记录的事件历史。

**参数**：

* **event**(Optional[str], 可选)：按事件筛选。
* **since**(Optional[datetime], 可选)：仅返回该时间之后记录。

**返回**：

* **List[Dict[str, Any]]**，事件记录列表。

---

### replay_events

```python
async def replay_events(self, since: Optional[datetime] = None) -> None
```

按记录顺序重新触发事件。

**参数**：

* **since**(Optional[datetime], 可选)：从该时间之后的记录开始重放。默认值：None。

---

### save_state

```python
def save_state(self, filepath: str) -> None
```

将框架状态写入文件（仅元数据，不包含回调函数本身）。

**参数**：

* **filepath**(str)：文件路径。

---

### list_events

```python
def list_events(self, namespace: Optional[str] = None) -> List[str]
```

列出已注册的事件名。

**参数**：

* **namespace**(Optional[str], 可选)：仅列出该命名空间下存在回调的事件。

**返回**：

* **List[str]**，事件名列表。

---

### list_callbacks

```python
def list_callbacks(self, event: str) -> List[Dict[str, Any]]
```

列出某事件下所有回调的元信息。

**参数**：

* **event**(str)：事件名。

**返回**：

* **List[Dict[str, Any]]**，回调信息列表（name、priority、enabled、namespace、tags、once、max_retries、timeout 等）。

---

### get_statistics

```python
def get_statistics(self) -> Dict[str, Any]
```

获取框架整体统计（事件数、回调总数、命名空间、过滤器数、链数、历史长度、指标数等）。

**返回**：**Dict[str, Any]**，统计信息字典。