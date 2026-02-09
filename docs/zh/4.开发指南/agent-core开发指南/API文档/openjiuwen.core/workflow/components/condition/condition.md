# openjiuwen.core.workflow

## class openjiuwen.core.workflow.components.condition.condition.Condition

```python
class openjiuwen.core.workflow.components.condition.condition.Condition(input_schema: Any = None)
```

所有条件判断类的抽象基类（父类）。它定义了条件判断组件的核心接口和基础执行逻辑，规定了子类实现条件判断的统一规范，所有自定义条件判断类都应继承此类并实现抽象方法。

**参数**：

* **input_schema**(Any = None)：条件判断所需的输入数据模式，用于从会话状态 [Session](../../../session/session.md) 中提取指定输入参数。

## class openjiuwen.core.workflow.components.condition.condition.FuncCondition

```python
class openjiuwen.core.workflow.components.condition.condition.FuncCondition(func: Callable[[], bool])
```

基于函数的条件判断实现，将一个无参函数作为条件判断逻辑。适用于需要封装简单逻辑判断的场景。特点是灵活，可以封装任意无参函数。

**参数**：

* **func**(Callable[[], bool])：一个无参数的函数，返回布尔值作为条件判断结果。函数内部可以包含任意复杂的判断逻辑。

**样例**：

```python
>>> # 1. 导入必要的类
>>> import os
>>> 
>>> from openjiuwen.core.workflow import FuncCondition
>>> 
>>> # 2. 定义一个条件函数
>>> def allow_zero():
...     # 这里可以包含任意复杂的判断逻辑
...     return str(os.environ.get("ALLOW_ZERO", "false")).lower() == "true"
>>> 
>>> # 3. 创建FuncCondition对象
>>> condition = FuncCondition(func=allow_zero)
```

## class openjiuwen.core.workflow.components.condition.condition.AlwaysTrue

```python
class openjiuwen.core.workflow.components.condition.condition.AlwaysTrue(inputs: Input, session: BaseSession)
```

恒成立的条件判断实现。适用于无条件循环，需要手动中断的场景。特点是最简单，始终成立，可以作为`default`分支的预设条件。

**参数**：

* **inputs**(Input)：传入的输入信息。

* **session**(BaseSession)：工作流执行的会话。
