## Context Inheritance (Fork)

Inherit context from an existing member via `fork` / `fork_source` / `fork_mode`:

| Parameter | Usage |
|------|------|
| **fork** | true: inherit the caller's full current context. A string (e.g. `code-ready`): inherit from the named checkpoint snapshot |
| **fork_source** | Name of the member whose context to fork from. Defaults to leader. Must be an already-spawned in-process teammate |
| **fork_mode** | Which side of the checkpoint to keep: `full` \| `before` (default) \| `after` \| `keep_before_compact_after` \| `keep_after_compact_before`. See *Fork Semantics* |

### Use Cases

| Scenario | Usage |
|------|------|
| Base class read; N homogeneous executors implement derived classes | `fork="base-ready" fork_source="reader"` |
| Understander has analyzed the project; multiple executors start directly | `fork="code-ready" fork_source="reader"` |
| Large context; compress the old analysis, keep the recent work | `fork="code-ready" fork_source="reader" fork_mode="keep_after_compact_before"` |
| Keep the established analysis verbatim, drop the trailing noise | `fork="code-ready" fork_source="reader" fork_mode="before"` |
| Only the post-checkpoint working state, skip everything before | `fork="code-ready" fork_source="reader" fork_mode="after"` |
| Unrelated new task | Omit fork |

### Fork Semantics

| fork | fork_mode | Behaviour |
|------|-----------|-----------|
| `true` | `full` | Full injection |
| `"ckpt"` | `before` | Keep messages before the checkpoint verbatim |
| `"ckpt"` | `after` | Keep messages from the checkpoint onward |
| `"ckpt"` | `keep_before_compact_after` | Keep before verbatim; compress after into a summary |
| `"ckpt"` | `keep_after_compact_before` | Keep after verbatim; compress before into a summary |

Omitted `fork_mode` defaults to `full` for `fork=true` and `before` for a named checkpoint fork.

### Mechanism

- Inherited messages are the source member's conversation history (file reads, search outputs, analysis conclusions)
- Have the source member call `checkpoint(name="xxx")` before forking, then use `fork="xxx"`
- **Before any named fork, call `list_checkpoints` and use the exact snapshot name it returns.** Never guess a checkpoint name from memory or a member's report — a wrong name silently falls back to a full-context fork
- **Checkpoint names are team-globally unique** and belong to their creator. Set `fork_source` to that creator (visible in `list_checkpoints`): the snapshot's message index is only meaningful against the creator's context
- `fork=true` inherits the full context at call time; prefer checkpoint mode when later scheduling noise would otherwise be included
