# openjiuwen.core.operator.base

`openjiuwen.core.operator.base` 提供自演进所需的**参数句柄**抽象：算子通过 `operator_id` 支持轨迹归因，通过 `get_tunables`、`set_parameter`、`get_state`/`load_state` 支持参数优化。算子本身不执行，仅管理参数，执行由使用者（Agent）完成。

---

## class openjiuwen.core.operator.base.TunableSpec

描述算子的单个可调参数。

* **name**(str)：参数名。
* **kind**(TunableKind)：可调类型（如 prompt、continuous、discrete、text）。
* **path**(str)：参数在算子内的路径。
* **constraint**(Any，可选)：约束（如范围、枚举）。

```text
class TunableSpec(name: str, kind: TunableKind, path: str, constraint: Optional[Any] = None)
```

---

## class openjiuwen.core.operator.base.Operator

自演进参数句柄的抽象基类。算子提供统一接口供进化框架使用：
- 通过 `operator_id` 标识算子（用于轨迹归因）
- 通过 `get_tunables` 描述可调参数
- 通过 `get_state` 读取当前值
- 通过 `set_parameter` 更新参数（检查冻结标记）
- 通过 `load_state` 从检查点恢复（不检查冻结标记）

参数变更通过 `on_parameter_updated` 回调推送给使用者（Agent/Rail），确保即时同步。

```text
class Operator()
```

### @property abstractmethod operator_id() -> str

在轨迹与归因中使用的算子唯一标识。

**返回**：

**str**，算子 ID。

### abstractmethod get_tunables() -> Dict[str, TunableSpec]

描述可调参数及其约束。已冻结的参数不应包含在返回结果中。

**返回**：

**Dict[str, TunableSpec]**，参数名到规格的映射。

### abstractmethod get_state() -> Dict[str, Any]

获取当前参数值，用于检查点/回滚。

**返回**：

**Dict[str, Any]**，可序列化的状态字典。

### abstractmethod set_parameter(target: str, value: Any) -> None

设置参数值（进化更新）。约束：1) 检查目标参数是否冻结（冻结则跳过）；2) 更新内部状态；3) 触发 `on_parameter_updated` 回调同步使用者。这是进化更新的唯一入口。

**参数**：

* **target**(str)：参数名。
* **value**(Any)：新值。

### abstractmethod load_state(state: Dict[str, Any]) -> None

从检查点恢复状态。约束：1) 不检查冻结标记（必须恢复完整状态）；2) 逐字段更新内部状态；3) 为每个字段触发 `on_parameter_updated` 回调。这是检查点恢复的唯一入口。

**参数**：

* **state**(Dict[str, Any])：状态字典。
