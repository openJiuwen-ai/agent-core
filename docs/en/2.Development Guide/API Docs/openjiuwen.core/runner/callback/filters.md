# openjiuwen.core.runner.callback.filters

Event filtering system used to intercept and control execution before callbacks run.

## class EventFilter

```python
class EventFilter(name: str = "")
```

Base class for event filters. Subclass and implement `filter` to intercept or modify execution before callbacks run.

**Parameters**:

* **name** (str, optional): Filter name; uses class name if not provided. Default: "".

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

Filter logic executed before the callback. Override in subclasses.

**Parameters**:

* **event** (str): Current event name.
* **callback** (Callable): Callback about to run.
* **\*args**: Arguments that will be passed to the callback.
* **\*\*kwargs**: Keyword arguments that will be passed to the callback.

**Returns**: **FilterResult**, indicating the next action (e.g. CONTINUE, SKIP, STOP, MODIFY).

---

## class RateLimitFilter

```python
class RateLimitFilter(max_calls: int, time_window: float, name: str = "RateLimit")
```

Limits how often callbacks can run within a time window.

**Parameters**:

* **max_calls** (int): Maximum calls allowed in the window.
* **time_window** (float): Time window in seconds.
* **name** (str, optional): Filter name. Default: "RateLimit".

---

## class CircuitBreakerFilter

```python
class CircuitBreakerFilter(
    failure_threshold: int = 5,
    timeout: float = 60.0,
    name: str = "CircuitBreaker"
)
```

Stops execution after a failure threshold is reached; can try again after a timeout.

**Parameters**:

* **failure_threshold** (int, optional): Failures before opening the circuit. Default: 5.
* **timeout** (float, optional): Wait time in seconds before retry. Default: 60.0.
* **name** (str, optional): Filter name. Default: "CircuitBreaker".

### failures

```python
failures -> Dict[str, int]
```

Failure count per key (read-only).

### record_success

```python
async def record_success(self, event: str, callback: Callable) -> None
```

Record a successful execution.

**Parameters**:

* **event** (str): Event name.
* **callback** (Callable): Callback.

### record_failure

```python
async def record_failure(self, event: str, callback: Callable) -> None
```

Record a failure and possibly open the circuit.

**Parameters**:

* **event** (str): Event name.
* **callback** (Callable): Callback.

---

## class ValidationFilter

```python
class ValidationFilter(validator: Callable[..., bool], name: str = "Validation")
```

Validates callback arguments using the provided validator function.

**Parameters**:

* **validator** (Callable[..., bool]): Function that takes (**\*args**, **\*\*kwargs**) and returns bool.
* **name** (str, optional): Filter name. Default: "Validation".

---

## class LoggingFilter

```python
class LoggingFilter(logger: Optional[logging.Logger] = None, name: str = "Logging")
```

Logs callback execution (event, callback name, arguments, etc.).

**Parameters**:

* **logger** (Optional[logging.Logger], optional): Logger to use; uses default if None. Default: None.
* **name** (str, optional): Filter name. Default: "Logging".

---

## class AuthFilter

```python
class AuthFilter(required_role: str, name: str = "Auth")
```

Role-based authorization: allows execution only when `user_role` in kwargs equals `required_role`.

**Parameters**:

* **required_role** (str): Required role.
* **name** (str, optional): Filter name. Default: "Auth".

---

## class ParamModifyFilter

```python
class ParamModifyFilter(modifier: Callable[..., tuple], name: str = "ParamModify")
```

Modifies callback arguments before execution; modifier signature is `(*args, **kwargs) -> (new_args, new_kwargs)`.

**Parameters**:

* **modifier** (Callable[..., tuple]): Function returning (new_args, new_kwargs).
* **name** (str, optional): Filter name. Default: "ParamModify".

---

## class ConditionalFilter

```python
class ConditionalFilter(
    condition: Callable[..., bool],
    action_on_false: FilterAction = FilterAction.SKIP,
    name: str = "Conditional"
)
```

Runs the callback only when condition(event, callback, *args, **kwargs) is True; otherwise returns action_on_false.

**Parameters**:

* **condition** (Callable[..., bool]): Condition function.
* **action_on_false** (FilterAction, optional): Action when condition is False. Default: FilterAction.SKIP.
* **name** (str, optional): Filter name. Default: "Conditional".
