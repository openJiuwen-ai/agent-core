Save a named snapshot of the current conversation context so that later `spawn_teammate(fork="this_name")` will let new members inherit this context — no need to re-read files or search the codebase.

| Parameter | Description |
|---|---|
| **name** | Semantic snapshot name (e.g. `code-ready`, `base-class-understood`). Used by later fork calls |
| **description** | Optional; why this checkpoint was taken (e.g. "base class analysis complete"), for logging only|

## When to Call

Call after completing **reusable phase work** — for example:

- Read base-class / interface source code; multiple executors will implement different derived classes
- Analyzed the project architecture; subsequent executors should start from this understanding
- Completed research on a key code area that multiple members should inherit

Work continues unaffected after calling — the snapshot records the context position at call time. Multiple members can share the same snapshot.

## Example

```
After the understander finishes reading the base class:
checkpoint(name="base-ready", description="Base class analysis complete, ready to fork")
```

## Fork coordination

```python
# 1. Understander saves checkpoint
checkpoint(name="code-ready")

# 2. Leader forks multiple executors from the checkpoint
spawn_teammate(name="dev-1", fork="code-ready", fork_source="understander", ...)
spawn_teammate(name="dev-2", fork="code-ready", fork_source="understander", ...)
```

**The checkpoint stores `len(messages)` at call time.** Context growth after the call does not affect the snapshot's semantics — fork captures from that position; messages that arrive later are not inherited.

## Notify the Leader

Saving a checkpoint **automatically notifies the leader** — the runtime publishes a framework event and the leader's context receives an announcement-only note with the exact name (no reply is expected). You do **not** need to send a separate `send_message` to report the name; if you want the leader to understand the snapshot's purpose, describe it in the `description` parameter (it is carried with the announcement). The leader can call `list_checkpoints` at any time to see the authoritative list — never expect the leader to guess the name you chose.
