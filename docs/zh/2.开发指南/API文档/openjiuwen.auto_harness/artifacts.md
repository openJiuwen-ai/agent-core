# openjiuwen.auto_harness.artifacts

制品存储原语模块，用于 Auto Harness 流水线各阶段之间的数据传递。通过会话级和任务级的双层命名空间实现制品隔离，支持跨阶段共享中间结果。

子模块：
- `store`：制品存储实现

---

## class openjiuwen.auto_harness.artifacts.store.ArtifactStore

```python
@dataclass
class ArtifactStore:
    """A scoped artifact store with session and task namespaces."""
```

具有会话和任务命名空间的制品存储。会话级制品对所有任务可见，任务级制品仅在指定 `task_id` 下可见。读取时优先查找任务级，未命中则回退到会话级。

**字段**：
* **_session**(`dict[str, Any]`)：会话级制品字典，默认空字典。
* **_task**(`dict[str, dict[str, Any]]`)：任务级制品字典，按 `task_id` 分桶，默认空字典。

### get(name: str, task_id: str = '', default: Any = None) -> Any

获取制品值。若指定 `task_id`，优先从任务级查找，未命中则回退到会话级；未指定 `task_id` 时仅查找会话级。

**参数**：
* **name**(`str`)：制品名称。
* **task_id**(`str`)：任务标识，默认为空字符串（仅查会话级）。
* **default**(`Any`)：未找到时的默认返回值，默认 `None`。

**返回**：制品值，或 `default`。

---

### require(name: str, task_id: str = '') -> Any

获取制品值，若不存在则抛出 `KeyError`。

**参数**：
* **name**(`str`)：制品名称。
* **task_id**(`str`)：任务标识，默认为空字符串。

**返回**：制品值。

**异常**：`KeyError` — 制品不存在时抛出。

---

### put(name: str, value: Any, task_id: str = '') -> None

存储单个制品。若指定 `task_id`，存入任务级命名空间；否则存入会话级命名空间。

**参数**：
* **name**(`str`)：制品名称。
* **value**(`Any`)：制品值。
* **task_id**(`str`)：任务标识，默认为空字符串（存入会话级）。

---

### put_many(artifacts: dict[str, Any], task_id: str = '') -> None

批量存储制品。等价于对字典中每个键值对调用 `put`。

**参数**：
* **artifacts**(`dict[str, Any]`)：制品名称到值的映射。
* **task_id**(`str`)：任务标识，默认为空字符串。

---

### has(name: str, task_id: str = '') -> bool

检查制品是否存在。查找逻辑与 `get` 一致。

**参数**：
* **name**(`str`)：制品名称。
* **task_id**(`str`)：任务标识，默认为空字符串。

**返回**：`True` 表示制品存在，`False` 表示不存在。

---

### reset_task(task_id: str) -> None

清除指定任务的全部制品。

**参数**：
* **task_id**(`str`)：要清除的任务标识。
