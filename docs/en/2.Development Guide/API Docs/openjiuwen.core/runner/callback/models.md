# openjiuwen.core.runner.callback.models

Data models and structures used by the callback framework.

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

Performance metrics for callback execution (call count, duration, errors, etc.).

**Parameters**:

* **call_count** (int, optional): Number of executions. Default: 0.
* **total_time** (float, optional): Cumulative execution time in seconds. Default: 0.0.
* **min_time** (float, optional): Minimum single execution time. Default: float('inf').
* **max_time** (float, optional): Maximum single execution time. Default: 0.0.
* **error_count** (int, optional): Number of failures. Default: 0.
* **last_call_time** (Optional[float], optional): Timestamp of last execution. Default: None.

### update

```python
def update(self, execution_time: float, is_error: bool = False) -> None
```

Update metrics with new execution data.

**Parameters**: **execution_time** (float): Duration of this execution in seconds. **is_error** (bool, optional): Whether the execution failed. Default: False.

### avg_time

```python
avg_time -> float
```

Average execution time in seconds; returns 0 when there are no calls.

### to_dict

```python
def to_dict(self) -> Dict[str, Any]
```

Convert metrics to a dictionary.

**Returns**: **Dict[str, Any]**, dictionary containing all metric fields.

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

Result returned by event filters.

**Parameters**: **action** (FilterAction): Action to take (CONTINUE, STOP, SKIP, MODIFY). **modified_args** (Optional[tuple], optional): New positional arguments when action is MODIFY. Default: None. **modified_kwargs** (Optional[dict], optional): New keyword arguments when action is MODIFY. Default: None. **reason** (Optional[str], optional): Reason for the action. Default: None.

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

Execution context for callback chains; used to share state and data along the chain.

**Parameters**: **event** (str): Name of the event being processed. **initial_args** (tuple): Original positional arguments. **initial_kwargs** (dict): Original keyword arguments. **results** (List[Any], optional): List of results from executed callbacks. Default: []. **metadata** (Dict[str, Any], optional): Custom metadata. Default: {}. **current_index** (int, optional): Index of the currently executing callback. Default: 0. **is_completed** (bool, optional): Whether the chain completed successfully. Default: False. **is_rolled_back** (bool, optional): Whether the chain was rolled back. Default: False. **start_time** (float, optional): Timestamp when chain execution started. Default: time.time().

### get_last_result

```python
def get_last_result(self) -> Any
```

Get the result from the previous callback.

**Returns**: **Any**, last result in the chain, or None if there are no results.

### get_all_results

```python
def get_all_results(self) -> List[Any]
```

Get all results from executed callbacks (a copy).

**Returns**: **List[Any]**, copy of the results list.

### set_metadata

```python
def set_metadata(self, key: str, value: Any) -> None
```

Store metadata in the context. **key** (str): Key. **value** (Any): Value.

### get_metadata

```python
def get_metadata(self, key: str, default: Any = None) -> Any
```

Retrieve metadata from the context. **key** (str): Key. **default** (Any, optional): Default if key is missing. Default: None.

**Returns**: **Any**, metadata value or default.

### elapsed_time

```python
elapsed_time -> float
```

Elapsed time in seconds since the chain started.

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

Result of chain execution.

**Parameters**: **action** (ChainAction): Final action of the chain. **result** (Any, optional): Final result value. Default: None. **context** (Optional[ChainContext], optional): Chain execution context. Default: None. **error** (Optional[Exception], optional): Exception if the chain failed. Default: None.

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

Metadata and configuration for a registered callback.

**Parameters**: **callback** (Callable[..., Awaitable[Any]]): Async callback function. **priority** (int): Execution priority (higher runs first). **once** (bool, optional): Execute only once. Default: False. **enabled** (bool, optional): Whether currently enabled. Default: True. **namespace** (str, optional): Namespace. Default: "default". **tags** (Set[str], optional): Set of tags. Default: set(). **max_retries** (int, optional): Maximum retries on failure. Default: 0. **retry_delay** (float, optional): Delay between retries in seconds. Default: 0.0. **timeout** (Optional[float], optional): Execution timeout in seconds. Default: None. **created_at** (float, optional): Registration timestamp. Default: time.time(). **wrapper** (Optional[Callable], optional): Wrapper returned by decorator (for unregistering). Default: None.
