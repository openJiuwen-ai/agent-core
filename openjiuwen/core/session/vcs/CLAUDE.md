# vcs — Session State/Context Version Control

Per-session **linear** version control for an agent. The first-class subject is
the **LLM context (conversation messages)**; agent kv-state rides along.

## Model

- **session_id ↔ one linear append-only history** (WAL). A `Session` object is
  just an access entry into that history.
- **rewind**: same session_id, **overwrite** — truncate history after the
  target point and continue from it.
- **fork**: the only operation that mints a **new session_id** — clones a new
  `Session` seeded from a history point for parallel use by another agent; the
  source session is untouched.
- Snapshot composition: `{context, state}`. `context` is message-level
  (append vs reset); `state` is kv-delta (nested set + removed paths).

## Layers

| File | Role |
|---|---|
| `protocol.py` | `VersionControl` — public capability protocol |
| `models.py` | pydantic records: `MessageDelta/StateDelta/LogEntry/Commit/Snapshot/Head`; `ForkResult` dataclass |
| `codec.py` | `BaseMessage` ↔ json dict, dispatched by `role` |
| `delta.py` | `diff_context/apply_context` (append/reset), `diff_state/apply_state` (None-safe) |
| `backend.py` | `VersioningBackend` protocol + shared crc/encode/decode |
| `jsonl_backend.py` / `kv_backend.py` | filesystem (per-session dir) / `BaseKVStore` (per-session key prefix) |
| `manager.py` | `VersioningManager` — append/commit/snapshot/restore/rewind/fork over a backend + injected callbacks |
| `adapter.py` | `for_session` — wires callbacks to a real `Session` + `ContextEngine`; fork uses `create_agent_session` |
| `config.py` | `VersioningConfig` + `build_backend` |

## Invariants

- **JSON only, never pickle.** vcs records are pydantic; messages reuse
  `BaseMessage.model_dump`/`model_validate` (by role).
- **No existing code is modified.** vcs reaches the session via the existing
  `state().get_state()/set_state()` contract, `ContextEngine` public methods,
  and `create_agent_session`.
- LogEntry carries a crc32; a torn/corrupt log tail stops reading without
  raising (crash recovery).
- A single writer per (session) is assumed.

## Tests

`tests/unit_tests/core/session/vcs/` — codec / delta / backend (both kinds) /
crash recovery / manager logic / rewind / fork / config / backend equivalence /
integration with a real context+state.
