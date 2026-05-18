# openjiuwen.agent_evolving.checkpointing

`openjiuwen.agent_evolving.checkpointing` provides checkpoint data structures, local file storage, and default save/restore strategies for self-evolving training; centered on Operator.get_state/load_state without binding to specific optimizer implementations.

---

## class openjiuwen.agent_evolving.checkpointing.state.EvolveCheckpoint

Single training checkpoint data structure (for restore and audit).

* **version**(str): Checkpoint version identifier.
* **run_id**(str): This run ID.
* **step**(Dict[str, int]): Step information (e.g., epoch, batch, global_step).
* **best**(Dict[str, Any]): Best information (e.g., best_score).
* **seed**(int, optional): Random seed.
* **operators_state**(Dict[str, Dict[str, Any]]): Operator ID to state mapping, used to restore each Operator.
* **updater_state**(Dict[str, Any]): Updater internal state (can be empty).
* **searcher_state**(Dict[str, Any]): Parameter searcher state (can be empty).
* **last_metrics**(Dict[str, Any]): Recent epoch summary metrics (can be empty).

---

## class openjiuwen.agent_evolving.checkpointing.types.EvolutionRecord

Persisted skill evolution experience record.

```text
class EvolutionRecord(
    id: str,
    source: str,
    timestamp: str,
    context: str,
    change: EvolutionPatch,
    applied: bool = False,
    score: float = 0.6,
    usage_stats: Optional[UsageStats] = None,
    skill_version: Optional[str] = None,
    summary: Optional[str] = None,
)
```

**Fields**:

* **id**(str): Stable evolution record ID, usually generated with the `ev_` prefix.
* **source**(str): Source signal or flow that produced the record.
* **timestamp**(str): ISO timestamp when the record was created or last updated.
* **context**(str): Compact context used to explain why the record was generated.
* **change**(EvolutionPatch): The concrete evolution change to persist or render.
* **applied**(bool, optional): Whether the record has already been applied. Default: `False`.
* **score**(float, optional): Ranking score used when selecting or rendering experiences. Default: `0.6`.
* **usage_stats**(UsageStats, optional): Presentation and feedback counters.
* **skill_version**(str, optional): Skill version associated with the record.
* **summary**(str, optional): One-sentence index summary used when rendering `SKILL.md`; old records without this field remain valid and fall back to content/script metadata during projection.

### make(source, context, change, *, score=0.6, skill_version=None, summary=None) -> EvolutionRecord

Creates a record with a generated ID, current UTC timestamp, default `UsageStats`, and the provided change metadata.

### to_dict() -> dict

Serializes the record for storage in `evolutions.json`. Optional fields are emitted only when present.

### from_dict(data: dict) -> EvolutionRecord

Restores a record from persisted JSON. Missing legacy fields are filled with compatible defaults.

### is_pending -> bool

Returns True when the record has not been applied.

---

## class openjiuwen.agent_evolving.checkpointing.store_file.FileCheckpointStore

Local JSON file-based checkpoint storage, not dependent on core's checkpointer, convenient for debugging and audit.

```text
class FileCheckpointStore(base_dir: str)
```

**Parameters**:

* **base_dir**(str): Checkpoint root directory; created automatically if not exists.

### save_checkpoint(ckpt: EvolveCheckpoint, filename='latest.json') -> Optional[str]

Serializes checkpoint as JSON and writes to specified filename under base_dir.

**Returns**:

**str | None**, full path of written file; returns None when base_dir is None.

### load_checkpoint(path: str) -> Optional[EvolveCheckpoint]

Loads checkpoint from specified path.

**Returns**:

**EvolveCheckpoint | None**, returns None when file doesn't exist or base_dir is None.

### load_state_dict(path: str) -> Optional[Dict[str, Dict[str, Any]]]

Reads only `operators_state` from checkpoint JSON, used for inference-side loading of operator states (e.g., `op.load_state(state[operator_id])`).

**Returns**:

**Dict[str, Dict[str, Any]] | None**, operator_id to state mapping; returns None when no `operators_state` or file doesn't exist.

---

## class openjiuwen.agent_evolving.checkpointing.manager.CheckpointManager

Checkpoint management protocol: decides when to save, how to build checkpoint, and how to restore.

### should_save(*, epoch: int, improved: bool) -> bool

Decides whether to save based on current epoch and whether improved.

### build_checkpoint(*, agent, progress, updater_state=None) -> EvolveCheckpoint

Builds current checkpoint (including operator state, progress, updater state, etc.).

### restore(*, agent, checkpoint: EvolveCheckpoint) -> Dict[str, Any]

Restores agent's operator state from checkpoint, and returns dict for Trainer to restore progress (e.g., start_epoch, best_score).

---

## class openjiuwen.agent_evolving.checkpointing.manager.DefaultCheckpointManager

Default checkpoint manager: saves on "validation improvement" or "every N epochs"; restores operators_state and progress best/epoch on restore.

```text
class DefaultCheckpointManager(
    *,
    run_id: Optional[str] = None,
    checkpoint_version: str = "v1",
    save_every_n_epochs: int = 1,
    save_on_improve: bool = True,
)
```

**Parameters**:

* **run_id**(str, optional): Run ID; uses uuid if not provided.
* **checkpoint_version**(str, optional): Checkpoint version identifier. Default: `"v1"`.
* **save_every_n_epochs**(int, optional): Save every N epochs, at least 1. Default: `1`.
* **save_on_improve**(bool, optional): Also save on validation improvement. Default: `True`.

### run_id -> str

Current run_id.

### should_save(*, epoch, improved) -> bool

Returns True when improved or epoch is a multiple of save_every_n_epochs.

### build_checkpoint(*, agent, progress, updater_state=None) -> EvolveCheckpoint

Calls get_operators() on agent, snapshots each operator's get_state(), assembles into EvolveCheckpoint together with progress and updater_state.

### restore(*, agent, checkpoint: EvolveCheckpoint) -> Dict[str, Any]

Writes checkpoint.operators_state back to each operator, and returns `{"start_epoch", "best_score", "run_id"}` for Trainer to restore progress.
