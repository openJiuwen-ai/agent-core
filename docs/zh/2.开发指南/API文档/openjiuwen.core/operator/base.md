# openjiuwen.core.operator.base

`openjiuwen.core.operator.base` 提供自演进所需的**原子算子**抽象：算子既可执行（invoke/stream，配合 Session，轨迹通过 session.tracer() 记录），又可优化（get_tunables、set_parameter、get_state/load_state）。

---

## class openjiuwen.core.operator.base.TunableSpec

描述算子的单个可调参数。

* **name**(str)：参数名。
* **kind**(TunableKind)：可调类型（如 prompt、continuous、discrete、tool_selector、memory_selector）。
* **path**(str)：参数在算子内的路径。
* **constraint**(Any，可选)：约束（如范围、枚举）。

```text
class TunableSpec(name: str, kind: TunableKind, path: str, constraint: Optional[Any] = None)
```

---

## class openjiuwen.core.operator.base.Operator

原子执行与优化单元的抽象基类。执行通过 invoke/stream 与 Session 完成（轨迹由 session.tracer() 写入）；优化通过 get_tunables、set_parameter、get_state/load_state 完成。开发者实现自定义算子时需继承此类并实现全部抽象方法。

```text
class Operator()
```

### @property abstractmethod operator_id() -> str

在轨迹与归因中使用的算子唯一标识。

**返回**：

**str**，算子 ID。

### abstractmethod get_tunables() -> Dict[str, TunableSpec]

描述可调参数（如 prompt 路径、temperature、tool_filter）。

**返回**：

**Dict[str, TunableSpec]**，参数名到规格的映射。

### abstractmethod set_parameter(target: str, value: Any) -> None

将指定参数设为优化后的值。prompt 类为字符串或列表；连续参数为数值。

**参数**：

* **target**(str)：参数名。
* **value**(Any)：新值。

### abstractmethod get_state() -> Dict[str, Any]

获取当前状态，用于回滚、快照或版本对比。

**返回**：

**Dict[str, Any]**，可序列化的状态字典。

### abstractmethod load_state(state: Dict[str, Any]) -> None

从序列化状态恢复算子。

**参数**：

* **state**(Dict[str, Any])：状态字典。

### abstractmethod async invoke(inputs, session: Session, **kwargs) -> Any

执行一步。轨迹通过 session.tracer() 写入，而非回调。

**参数**：

* **inputs**(Dict[str, Any])：本步输入。
* **session**(Session)：会话与追踪。
* **kwargs**：其他执行参数。

**返回**：

**Any**，执行结果。

### async stream(inputs, session: Session, **kwargs) -> AsyncIterator[Any]

可选流式执行；默认抛出 NotImplementedError。

**参数**：

* **inputs**(Dict[str, Any])：本步输入。
* **session**(Session)：会话与追踪。
* **kwargs**：其他参数。

**返回**：

**AsyncIterator[Any]**，流式块。

**异常**：

* **NotImplementedError**：子类未实现流式时抛出。

### _set_operator_context(session: Session, context_id: Optional[str] = None) -> None

在 session 上设置当前算子 ID，用于追踪；context_id 为 None 时清除。
