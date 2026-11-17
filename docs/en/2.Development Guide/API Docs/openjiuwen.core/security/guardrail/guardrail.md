# openjiuwen.core.security.guardrail

## class openjiuwen.core.security.guardrail.BaseGuardrail

```
class openjiuwen.core.security.guardrail.BaseGuardrail(
    backend: Optional[GuardrailBackend] = None,
    events: Optional[List[str]] = None,
    enable_logging: bool = True
)
```

Abstract base class for guardrail implementations. Guardrails are used to monitor specific events in the Agent execution flow and perform security detection when these events are triggered. It integrates with the callback framework and supports configuration with custom detection backends.

Subclasses should define the `DEFAULT_EVENTS` class attribute and can override the `listen_events` property to implement dynamic event configuration.

**Parameters**:

* **backend**(GuardrailBackend, optional): Optional detection backend. Can also be set later using `set_backend()` method. Default: `None`.
* **events**(List[str], optional): Optional list of event names to monitor. If not provided, uses the subclass's `DEFAULT_EVENTS`. Default: `None`.
* **enable_logging**(bool, optional): Whether to enable logging output. Default: `True`.

### listen_events

Property that returns the list of event names this guardrail should monitor.

**Returns**:

**List[str]**, list of event name strings (returns a copy to prevent external modification).

### with_events

```python
with_events(events: List[str]) -> BaseGuardrail
```

Sets the event names to monitor (supports chained calls). This allows configuring which events the guardrail should monitor at runtime. Must be called before the `register()` method.

**Parameters**:

* **events**(List[str]): List of event name strings.

**Returns**:

**BaseGuardrail**, returns self to support method chaining.

### set_backend

```python
set_backend(backend: GuardrailBackend) -> BaseGuardrail
```

Sets the detection backend.

**Parameters**:

* **backend**(GuardrailBackend): Detection backend to use.

**Returns**:

**BaseGuardrail**, returns self to support method chaining.

### get_backend

```python
get_backend() -> Optional[GuardrailBackend]
```

Gets the currently configured detection backend.

**Returns**:

**Optional[GuardrailBackend]**, configured detection backend, returns `None` if not set.

### async detect

```python
async detect(event_name: str, *args, **kwargs) -> GuardrailResult
```

Performs security detection on a triggered event. This method is called when a subscribed event is triggered. The default implementation delegates to the configured backend (if available). Subclasses can override this method to implement custom detection logic.

**Parameters**:

* **event_name**(str): Name of the triggered event.
* **args**: Positional arguments passed from the callback framework when the event is triggered.
* **kwargs**: Keyword arguments (event data) passed from the callback framework when the event is triggered.

**Returns**:

**GuardrailResult**, indicates whether content is safe.

**Exceptions**:

* **ValueError**: If no backend is configured and the `detect` method is not overridden.

### async register

```python
async register(framework: Any) -> None
```

Registers this guardrail to the callback framework. This registers the guardrail's `detect` method as a callback function for all events in `listen_events`.

**Parameters**:

* **framework**(Any): `AsyncCallbackFramework` instance.

### async unregister

```python
async unregister() -> None
```

Unregisters this guardrail from the callback framework. Removes all registered callback functions. Calling this method is safe even if not registered.

---

## class openjiuwen.core.security.guardrail.GuardrailResult

```
class openjiuwen.core.security.guardrail.GuardrailResult(
    is_safe: bool,
    risk_level: RiskLevel,
    risk_type: Optional[str] = None,
    confidence: float = 0.0,
    details: Optional[Dict[str, Any]] = None,
    modified_data: Optional[Dict[str, Any]] = None,
    event: Optional[str] = None
)
```

Guardrail detection result. Defined in the detection library and re-exported here as the same class.

**Parameters**:

* **is_safe**(bool): `False` means risk was detected. It does not mean the call was blocked.
* **risk_level**(RiskLevel): Detected risk level.
* **risk_type**(str, optional): Risk type identifier. Default: `None`.
* **confidence**(float): Same value as the backend `RiskAssessment.confidence`. Default: `0.0`.
* **details**(Dict[str, Any], optional): Detection details. Default: `None`.
* **modified_data**(Dict[str, Any], optional): Sanitized data. Default: `None`. Not written or consumed in this change.
* **event**(str, optional): Name of the event that triggered detection. The converter leaves it empty; the callback writes it. Default: `None`.

### pass_

```python
class method pass_(details: Optional[Dict[str, Any]] = None) -> GuardrailResult
```

Creates a pass result.

**Parameters**:

* **details**(Dict[str, Any], optional): Optional detection details. Default: `None`.

**Returns**:

**GuardrailResult** with `is_safe=True` and `risk_level=SAFE`.

### block

```python
class method block(
    risk_level: RiskLevel,
    risk_type: str,
    details: Optional[Dict[str, Any]] = None,
    modified_data: Optional[Dict[str, Any]] = None
) -> GuardrailResult
```

Creates an `is_safe=False` result. This is a result object and does not block execution.

**Parameters**:

* **risk_level**(RiskLevel): Risk level.
* **risk_type**(str): Risk type identifier.
* **details**(Dict[str, Any], optional): Detection details. Default: `None`.
* **modified_data**(Dict[str, Any], optional): Sanitized data. Default: `None`.

**Returns**:

**GuardrailResult** with `is_safe=False`.

---

## class openjiuwen.core.security.guardrail.RiskAssessment

```
class openjiuwen.core.security.guardrail.RiskAssessment(
    has_risk: bool,
    risk_level: RiskLevel,
    risk_type: Optional[str] = None,
    confidence: float = 0.0,
    details: Optional[Dict[str, Any]] = None
)
```

Risk assessment result returned by detection backends.

**Parameters**:

* **has_risk**(bool): Whether risk was detected.
* **risk_level**(RiskLevel): Risk level.
* **risk_type**(str, optional): Risk type identifier. Default: `None`.
* **confidence**(float): Confidence score between 0.0 and 1.0. Default: `0.0`.
* **details**(Dict[str, Any], optional): Detailed information. Default: `None`.

---

## class openjiuwen.core.security.guardrail.RiskLevel

```
class openjiuwen.core.security.guardrail.RiskLevel
```

Risk level enumeration.

* **SAFE**: Safe, no risk
* **LOW**: Low risk
* **MEDIUM**: Medium risk
* **HIGH**: High risk
* **CRITICAL**: Critical risk

**Handling Mechanism**:

| Risk Level | Action |
|---------|---------|
| SAFE | The callback returns `is_safe=True`; the call continues |
| LOW | The callback returns `is_safe=False`; the call continues |
| MEDIUM | The callback returns `is_safe=False`; the call continues |
| HIGH | The callback returns `is_safe=False`; the call continues |
| CRITICAL | The callback returns `is_safe=False`; the call continues. It does not raise `AbortError` |

> **Note**: The callback no longer raises `GuardrailError` or `AbortError`. A direct `detect()` call still raises `ValueError` when no backend is set, and a backend `RuntimeError` still propagates. Only the callback path turns those exceptions into `pass_(details={"error": ...})`.

---

## class openjiuwen.core.security.guardrail.GuardrailError

```
class openjiuwen.core.security.guardrail.GuardrailError
```

The exception class remains. The content-detection callback no longer raises it. A direct `detect()` call does not use it as the risk exit either; the risk is on the returned `GuardrailResult`.

**Example**:

```python
result = await guardrail.detect("user_input", text=malicious_input)
if not result.is_safe:
    print(result.risk_type, result.risk_level, result.confidence)
```
