# openjiuwen.core.sys_operation.sandbox.gateway.sandbox_store

## class SandboxStatus

```python
class SandboxStatus(Enum)
```

Sandbox status enumeration.

**Values**:

* **RUNNING**: Running
* **PAUSED**: Paused
* **KILLED**: Killed

## class SandboxRecord

```python
@dataclass
class SandboxRecord
```

Sandbox record used by the gateway store to track sandbox instances.

**Parameters**:

* **sandbox_id**(str): Sandbox instance identifier.
* **base_url**(str): Sandbox service base URL.
* **status**([SandboxStatus](#class-sandboxstatus)): Sandbox status.
* **launcher_type**(str): Launcher type.
* **sandbox_type**(str): Sandbox type.
* **container_config_hash**(str): Container configuration hash.
* **created_ts**(float): Creation timestamp. Default: `time.time`.
* **last_used_ts**(float): Last used timestamp. Default: `time.time`.
* **metadata**(Dict[str, Any]): Additional metadata. Default: `{}`.

## class AbstractSandboxStore

```python
class AbstractSandboxStore(ABC)
```

Abstract base class for sandbox store.

### async get

```python
async get(key: str) -> Optional[SandboxRecord]
```

Get sandbox record.

**Parameters**:

* **key**(str): Isolation key.

**Returns**:

**Optional[[SandboxRecord](#class-sandboxrecord)]**, sandbox record.

### async set

```python
async set(key: str, record: SandboxRecord) -> None
```

Set sandbox record.

**Parameters**:

* **key**(str): Isolation key.
* **record**([SandboxRecord](#class-sandboxrecord)): Sandbox record.

### async hdel

```python
async hdel(key: str) -> Optional[SandboxRecord]
```

Delete sandbox record.

**Parameters**:

* **key**(str): Isolation key.

**Returns**:

**Optional[[SandboxRecord](#class-sandboxrecord)]**, deleted sandbox record.

### async flushdb

```python
async flushdb() -> List[SandboxRecord]
```

Clear all sandbox records.

**Returns**:

**List[[SandboxRecord](#class-sandboxrecord)]**, list of all sandbox records.

### async evict_expired

```python
async evict_expired(idle_ttl_seconds: int, now: float) -> List[SandboxRecord]
```

Evict expired sandbox records.

**Parameters**:

* **idle_ttl_seconds**(int): Idle timeout in seconds.
* **now**(float): Current timestamp.

**Returns**:

**List[[SandboxRecord](#class-sandboxrecord)]**, list of evicted sandbox records.

---

## class InMemorySandboxStore

```python
class InMemorySandboxStore(AbstractSandboxStore)
```

In-memory sandbox store implementation.
