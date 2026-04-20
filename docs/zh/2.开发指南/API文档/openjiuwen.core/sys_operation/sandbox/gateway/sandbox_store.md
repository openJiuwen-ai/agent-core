# openjiuwen.core.sys_operation.sandbox.gateway.sandbox_store

## class SandboxStatus

```python
class SandboxStatus(Enum)
```

沙箱状态枚举。

**值**：

* **RUNNING**：运行中
* **PAUSED**：已暂停
* **KILLED**：已销毁

## class SandboxRecord

```python
@dataclass
class SandboxRecord
```

沙箱记录，用于在网关存储中跟踪沙箱实例。

**参数**：

* **sandbox_id**(str)：沙箱实例标识符。
* **base_url**(str)：沙箱服务基础 URL。
* **status**([SandboxStatus](#class-sandboxstatus))：沙箱状态。
* **launcher_type**(str)：启动器类型。
* **sandbox_type**(str)：沙箱类型。
* **container_config_hash**(str)：容器配置哈希。
* **created_ts**(float)：创建时间戳。默认值：`time.time`。
* **last_used_ts**(float)：最后使用时间戳。默认值：`time.time`。
* **metadata**(Dict[str, Any])：额外元数据。默认值：`{}`。

## class AbstractSandboxStore

```python
class AbstractSandboxStore(ABC)
```

沙箱存储抽象基类。

### async get

```python
async get(key: str) -> Optional[SandboxRecord]
```

获取沙箱记录。

**参数**：

* **key**(str)：隔离键。

**返回**：

**Optional[[SandboxRecord](#class-sandboxrecord)]**，沙箱记录。

### async set

```python
async set(key: str, record: SandboxRecord) -> None
```

设置沙箱记录。

**参数**：

* **key**(str)：隔离键。
* **record**([SandboxRecord](#class-sandboxrecord))：沙箱记录。

### async hdel

```python
async hdel(key: str) -> Optional[SandboxRecord]
```

删除沙箱记录。

**参数**：

* **key**(str)：隔离键。

**返回**：

**Optional[[SandboxRecord](#class-sandboxrecord)]**，被删除的沙箱记录。

### async flushdb

```python
async flushdb() -> List[SandboxRecord]
```

清空所有沙箱记录。

**返回**：

**List[[SandboxRecord](#class-sandboxrecord)]**，所有沙箱记录列表。

### async evict_expired

```python
async evict_expired(idle_ttl_seconds: int, now: float) -> List[SandboxRecord]
```

驱逐过期的沙箱记录。

**参数**：

* **idle_ttl_seconds**(int)：空闲超时时间（秒）。
* **now**(float)：当前时间戳。

**返回**：

**List[[SandboxRecord](#class-sandboxrecord)]**，被驱逐的沙箱记录列表。

---

## class InMemorySandboxStore

```python
class InMemorySandboxStore(AbstractSandboxStore)
```

内存中的沙箱存储实现。
