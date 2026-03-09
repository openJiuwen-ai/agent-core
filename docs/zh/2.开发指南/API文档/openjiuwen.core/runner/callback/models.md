# openjiuwen.core.runner.callback.models

回调框架使用的数据模型与结构。

## class CallbackMetrics

```python
class CallbackMetrics(
    call_count: int = 0,
    total_time: float = 0.0,
    min_time: float = float('inf'),
    max_time: float = 0.0,
    error_count: int = 0,
    last_call_time: Optional[float] = None
)
```

用于记录回调执行情况的性能指标（调用次数、耗时、错误数等）。

**参数**：

* **call_count**(int, 可选)：执行次数。默认值：0。
* **total_time**(float, 可选)：累计执行时间（秒）。默认值：0.0。
* **min_time**(float, 可选)：最短单次执行时间。默认值：float('inf')。
* **max_time**(float, 可选)：最长单次执行时间。默认值：0.0。
* **error_count**(int, 可选)：失败次数。默认值：0。
* **last_call_time**(Optional[float], 可选)：最后一次执行时间戳。默认值：None。

### update

```python
def update(self, execution_time: float, is_error: bool = False) -> None
```

用新的执行数据更新指标。

**参数**：

* **execution_time**(float)：本次执行耗时（秒）。
* **is_error**(bool, 可选)：是否为执行失败。默认值：False。

### avg_time

```python
avg_time -> float
```

平均执行时间（秒）；无调用时返回 0。

### to_dict

```python
def to_dict(self) -> Dict[str, Any]
```

将指标转换为字典。

**返回**：**Dict[str, Any]**，包含所有指标字段。

---

## class FilterResult

```python
class FilterResult(
    action: FilterAction,
    modified_args: Optional[tuple] = None,
    modified_kwargs: Optional[dict] = None,
    reason: Optional[str] = None
)
```

事件过滤器返回的结果。

**参数**：

* **action**(FilterAction)：要执行的动作（CONTINUE、STOP、SKIP、MODIFY）。
* **modified_args**(Optional[tuple], 可选)：当 action 为 MODIFY 时的新的位置参数。默认值：None。
* **modified_kwargs**(Optional[dict], 可选)：当 action 为 MODIFY 时的新的关键字参数。默认值：None。
* **reason**(Optional[str], 可选)：动作说明。默认值：None。

---

## class ChainContext

```python
class ChainContext(
    event: str,
    initial_args: tuple,
    initial_kwargs: dict,
    results: List[Any] = field(default_factory=list),
    metadata: Dict[str, Any] = field(default_factory=dict),
    current_index: int = 0,
    is_completed: bool = False,
    is_rolled_back: bool = False,
    start_time: float = field(default_factory=time.time)
)
```

链式执行时的上下文，用于在链中共享状态与数据。

**参数**：

* **event**(str)：当前处理的事件名。
* **initial_args**(tuple)：原始位置参数。
* **initial_kwargs**(dict)：原始关键字参数。
* **results**(List[Any], 可选)：已执行回调的结果列表。默认值：[]。
* **metadata**(Dict[str, Any], 可选)：自定义元数据。默认值：{}。
* **current_index**(int, 可选)：当前执行的回调索引。默认值：0。
* **is_completed**(bool, 可选)：链是否成功完成。默认值：False。
* **is_rolled_back**(bool, 可选)：是否已回滚。默认值：False。
* **start_time**(float, 可选)：链开始执行的时间戳。默认值：time.time()。

### get_last_result

```python
def get_last_result(self) -> Any
```

获取上一个回调的返回值。

**返回**：**Any**，链中最后一个结果；无结果时返回 None。

### get_all_results

```python
def get_all_results(self) -> List[Any]
```

获取已执行回调的全部结果（副本）。

**返回**：**List[Any]**，结果列表的副本。

### set_metadata

```python
def set_metadata(self, key: str, value: Any) -> None
```

在上下文中保存元数据。

**参数**：

* **key**(str)：键。
* **value**(Any)：值。

### get_metadata

```python
def get_metadata(self, key: str, default: Any = None) -> Any
```

从上下文中读取元数据。

**参数**：

* **key**(str)：键。
* **default**(Any, 可选)：键不存在时的默认值。默认值：None。

**返回**：

* **Any**，元数据值或 default。

### elapsed_time

```python
elapsed_time -> float
```

自链开始以来经过的时间（秒）。

---

## class ChainResult

```python
class ChainResult(
    action: ChainAction,
    result: Any = None,
    context: Optional['ChainContext'] = None,
    error: Optional[Exception] = None
)
```

链式执行的结果。

**参数**：

* **action**(ChainAction)：链的最终动作。
* **result**(Any, 可选)：最终结果值。默认值：None。
* **context**(Optional[ChainContext], 可选)：链执行上下文。默认值：None。
* **error**(Optional[Exception], 可选)：若链失败，对应的异常。默认值：None。

---

## class CallbackInfo

```python
class CallbackInfo(
    callback: Callable[..., Awaitable[Any]],
    priority: int,
    once: bool = False,
    enabled: bool = True,
    namespace: str = "default",
    tags: Set[str] = field(default_factory=set),
    max_retries: int = 0,
    retry_delay: float = 0.0,
    timeout: Optional[float] = None,
    created_at: float = field(default_factory=time.time),
    wrapper: Optional[Callable[..., Awaitable[Any]]] = None
)
```

已注册回调的元数据与配置。

**参数**：

* **callback**(Callable[..., Awaitable[Any]])：异步回调函数。
* **priority**(int)：执行优先级（数值越大越先执行）。
* **once**(bool, 可选)：是否仅执行一次。默认值：False。
* **enabled**(bool, 可选)：当前是否启用。默认值：True。
* **namespace**(str, 可选)：命名空间。默认值："default"。
* **tags**(Set[str], 可选)：标签集合。默认值：set()。
* **max_retries**(int, 可选)：失败时最大重试次数。默认值：0。
* **retry_delay**(float, 可选)：重试间隔（秒）。默认值：0.0。
* **timeout**(Optional[float], 可选)：单次执行超时（秒）。默认值：None。
* **created_at**(float, 可选)：注册时间戳。默认值：time.time()。
* **wrapper**(Optional[Callable], 可选)：装饰器返回的包装函数（用于注销）。默认值：None。
* **callback_type**(str, 可选)：语义类型标记，如 "transform"。默认值：""。
