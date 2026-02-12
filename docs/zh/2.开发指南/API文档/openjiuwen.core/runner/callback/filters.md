# openjiuwen.core.runner.callback.filters

事件过滤系统，用于在回调执行前进行拦截与控制。

## class EventFilter

```python
class EventFilter(name: str = "")
```

事件过滤器基类。可在回调执行前拦截或修改执行。自定义过滤器需继承此类并实现 `filter`。

**参数**：

* **name**(str, 可选)：过滤器名称，未提供时使用类名。默认值：""。

### filter

```python
async def filter(
    self,
    event: str,
    callback: Callable,
    *args,
    **kwargs
) -> FilterResult
```

在回调执行前执行的过滤逻辑。子类应重写此方法。

**参数**：

* **event**(str)：当前事件名。
* **callback**(Callable)：即将执行的回调。
* **\*args**、**\*\*kwargs**：将传给回调的参数。

**返回**：

* **FilterResult**，表示下一步动作（如 CONTINUE、SKIP、STOP、MODIFY）。

---

## class RateLimitFilter

```python
class RateLimitFilter(max_calls: int, time_window: float, name: str = "RateLimit")
```

限流过滤器：在给定时间窗口内限制回调执行次数。

**参数**：

* **max_calls**(int)：时间窗口内允许的最大调用次数。
* **time_window**(float)：时间窗口（秒）。
* **name**(str, 可选)：过滤器名称。默认值："RateLimit"。

---

## class CircuitBreakerFilter

```python
class CircuitBreakerFilter(
    failure_threshold: int = 5,
    timeout: float = 60.0,
    name: str = "CircuitBreaker"
)
```

熔断器：失败次数达到阈值后停止执行，超时后可尝试恢复。

**参数**：

* **failure_threshold**(int, 可选)：打开熔断前的失败次数。默认值：5。
* **timeout**(float, 可选)：恢复尝试前的等待时间（秒）。默认值：60.0。
* **name**(str, 可选)：过滤器名称。默认值："CircuitBreaker"。

### failures

```python
failures -> Dict[str, int]
```

各 key 对应的失败次数（只读）。

### record_success

```python
async def record_success(self, event: str, callback: Callable) -> None
```

记录一次成功执行。

**参数**：

* **event**(str)：事件名。
* **callback**(Callable)：回调。

### record_failure

```python
async def record_failure(self, event: str, callback: Callable) -> None
```

记录一次失败，并可能打开熔断。

**参数**：

* **event**(str)：事件名。
* **callback**(Callable)：回调。

---

## class ValidationFilter

```python
class ValidationFilter(validator: Callable[..., bool], name: str = "Validation")
```

参数校验过滤器：使用传入的校验函数检查参数是否合法。

**参数**：

* **validator**(Callable[..., bool])：接收 (*args, **kwargs) 并返回 bool 的校验函数。
* **name**(str, 可选)：过滤器名称。默认值："Validation"。

---

## class LoggingFilter

```python
class LoggingFilter(logger: Optional[logging.Logger] = None, name: str = "Logging")
```

日志过滤器：记录回调执行信息（事件、回调名、参数等）。

**参数**：

* **logger**(Optional[logging.Logger], 可选)：使用的 Logger，为 None 时使用默认。默认值：None。
* **name**(str, 可选)：过滤器名称。默认值："Logging"。

---

## class AuthFilter

```python
class AuthFilter(required_role: str, name: str = "Auth")
```

基于角色的授权过滤器：仅当 kwargs 中的 `user_role` 等于 `required_role` 时放行。

**参数**：

* **required_role**(str)：要求的角色。
* **name**(str, 可选)：过滤器名称。默认值："Auth"。

---

## class ParamModifyFilter

```python
class ParamModifyFilter(modifier: Callable[..., tuple], name: str = "ParamModify")
```

参数修改过滤器：在回调执行前用 modifier 转换 (*args, **kwargs)。modifier 签名为 `(*args, **kwargs) -> (new_args, new_kwargs)`。

**参数**：

* **modifier**(Callable[..., tuple])：返回 (new_args, new_kwargs) 的函数。
* **name**(str, 可选)：过滤器名称。默认值："ParamModify"。

---

## class ConditionalFilter

```python
class ConditionalFilter(
    condition: Callable[..., bool],
    action_on_false: FilterAction = FilterAction.SKIP,
    name: str = "Conditional"
)
```

条件过滤器：仅当 condition(event, callback, *args, **kwargs) 为 True 时放行；否则返回 action_on_false。

**参数**：

* **condition**(Callable[..., bool])：条件函数。
* **action_on_false**(FilterAction, 可选)：条件为 False 时的动作。默认值：FilterAction.SKIP。
* **name**(str, 可选)：过滤器名称。默认值："Conditional"。
