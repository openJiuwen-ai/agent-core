# openjiuwen.core.workflow

## class openjiuwen.core.workflow.components.condition.condition.Condition

```python
class openjiuwen.core.workflow.components.condition.condition.Condition(input_schema: Any = None)
```

Abstract base class (parent class) for all condition judgment classes. It defines the core interface and basic execution logic for condition judgment components, specifies unified specifications for subclasses to implement condition judgment, and all custom condition judgment classes should inherit from this class and implement abstract methods.

**Parameters**:

* **input_schema** (Any = None): Input data schema required for condition judgment, used to extract specified input parameters from session state [Session](../../../session/session.md).

## class openjiuwen.core.workflow.components.condition.condition.FuncCondition

```python
class openjiuwen.core.workflow.components.condition.condition.FuncCondition(func: Callable[[], bool])
```

Function-based condition judgment implementation, using a parameterless function as condition judgment logic. Suitable for scenarios requiring encapsulation of simple logic judgment. Characterized by flexibility, can encapsulate any parameterless function.

**Parameters**:

* **func** (Callable[[], bool]): A parameterless function that returns a boolean value as the condition judgment result. The function can contain arbitrarily complex judgment logic internally.

**Example**:

```python
>>> # 1. Import necessary classes
>>> import os
>>> 
>>> from openjiuwen.core.workflow import FuncCondition
>>> 
>>> # 2. Define a condition function
>>> def allow_zero():
...     # Can contain arbitrarily complex judgment logic here
...     return str(os.environ.get("ALLOW_ZERO", "false")).lower() == "true"
>>> 
>>> # 3. Create FuncCondition object
>>> condition = FuncCondition(func=allow_zero)
```

## class openjiuwen.core.workflow.components.condition.condition.AlwaysTrue

```python
class openjiuwen.core.workflow.components.condition.condition.AlwaysTrue(inputs: Input, session: BaseSession)
```

Always-true condition judgment implementation. Suitable for unconditional loops, scenarios requiring manual interruption. Characterized by being the simplest, always true, can be used as a preset condition for `default` branches.

**Parameters**:

* **inputs** (Input): Passed input information.

* **session** (BaseSession): Session for workflow execution.
