List all named checkpoints currently available for fork inheritance, with each snapshot's name, message count, creator, and description.

| Field | Meaning |
|---|---|
| **name** | The exact string to pass to `spawn_teammate(fork="<name>")` |
| **message_count** | Context length at snapshot time |
| **created_by** | The member who created the snapshot |
| **description** | Optional note recorded at snapshot time |

## When to Call

**Call before forking** — you must not guess a checkpoint name. Members create snapshots with arbitrary names (see the `checkpoint` tool), so the authoritative list lives here. Forking with a name that does not exist silently falls back to a full-context inheritance and you get no inherited understanding.

Use the returned **exact name** in `spawn_teammate(fork="<name>", fork_source="<created_by>", ...)`.
