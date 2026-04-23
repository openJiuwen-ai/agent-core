# openjiuwen.agent_evolving.agent_rl.rollout_store

## class openjiuwen.agent_evolving.agent_rl.offline.store.base.RolloutPersistence

```python
class openjiuwen.agent_evolving.agent_rl.offline.store.base.RolloutPersistence(ABC)
```

Abstract interface for Rollout persistence.

### save_rollout(self, step: int, task_id: str, rollout: RolloutMessage, *, phase: str = "train") -> None

Persist a single rollout and its full trajectory.

**Parameters**:

* **step**(int): Current training step.
* **task_id**(str): Task identifier.
* **rollout**(RolloutMessage): Rollout message to persist.
* **phase**(str, optional): `"train"` or `"val"` — determines output subdirectory. Default: `"train"`.

### save_step_summary(self, step: int, metrics: Dict[str, Any]) -> None

Persist per-step training summary metrics.

**Parameters**:

* **step**(int): Step number.
* **metrics**(Dict[str, Any]): Metrics dictionary.

### query_rollouts(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]

Query historical rollouts by filters (for analysis/debugging).

**Parameters**:

* **filters**(Dict[str, Any]): Filter dictionary.
* **limit**(int, optional): Result limit. Default: `100`.

**Returns**:

**List[Dict[str, Any]]**, List of matching rollout documents.

### close(self) -> None

Release connections and clean up resources.

## class openjiuwen.agent_evolving.agent_rl.offline.store.file_store.FileRolloutStore

```python
class openjiuwen.agent_evolving.agent_rl.offline.store.file_store.FileRolloutStore(save_path: str, flush_interval: int = 100)
```

Persist rollout data to local JSONL files, grouped by step range.

**Directory structure**:

```
save_path/
├── train/
│   └── rollouts/
│       ├── steps_000000_000099.jsonl
│       └── ...
├── val/
│   └── rollouts/
│       ├── steps_000000_000099.jsonl
│       └── ...
└── step_summaries/
    └── steps_000000_000099.jsonl
```

Each `.jsonl` file has one JSON object per line.

### __init__(self, save_path: str, flush_interval: int = 100) -> None

Initialize file rollout store.

**Parameters**:

* **save_path**(str): Root directory for all rollout output files.
* **flush_interval**(int, optional): Step range per file (e.g. 100 means steps 0-99 go to one file, 100-199 to the next). Default: `100`.

## class openjiuwen.agent_evolving.agent_rl.offline.store.null_store.NullRolloutStore

```python
class openjiuwen.agent_evolving.agent_rl.offline.store.null_store.NullRolloutStore()
```

No-op implementation when persistence is disabled. All methods succeed silently with no I/O.

**Example**:

```python
>>> from openjiuwen.agent_evolving.agent_rl.offline.store.base import RolloutPersistence
>>> from openjiuwen.agent_evolving.agent_rl.offline.store.null_store import NullRolloutStore
>>> from openjiuwen.agent_evolving.agent_rl.offline.store.file_store import FileRolloutStore
>>> 
>>> # Use NullRolloutStore (persistence disabled)
>>> store = NullRolloutStore()
>>> 
>>> # Use FileRolloutStore (persistence enabled)
>>> store = FileRolloutStore(save_path="/path/to/save", flush_interval=100)
```
