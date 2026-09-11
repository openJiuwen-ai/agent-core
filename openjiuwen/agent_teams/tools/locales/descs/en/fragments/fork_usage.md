## Context Inheritance (Fork)

**Default to it**: when multiple members need the same base understanding, default to **inheriting rather than re-reading** — have the source member call `checkpoint(name="xxx")` first, then spawn with `fork="xxx" fork_source="<source-member>"`. Before creating a member, ask: does it depend on understanding some member already has? If so, fork — don't have each member re-read the same material from scratch.

### Orchestration Order (critical timing)

The snapshot **must exist before the members that depend on it**, and it records the **source member's own context** — only the source member (not the leader) can call `checkpoint`:

1. **Bake the checkpoint instruction into the analyzer when you spawn it**: in its `desc` or first task, say "after reading the module, call `checkpoint(name="xxx")`" — don't plan to add it later
2. After it finishes reading and snapshots, call `list_checkpoints` to confirm the snapshot exists
3. **Spawn members that depend on it only after the snapshot exists**, passing `fork="xxx" fork_source="<analyzer>"`

If an already-spawned analyzer has not snapshotted yet, `send_message` it to read and call `checkpoint`, then confirm via `list_checkpoints` before forking. If the dependent members are spawned **in the same batch as the analyzer** (no snapshot yet), there is nothing to fork — let the analyzer run a round and snapshot first, then build the dependents.

### Use Cases

| Scenario | Usage |
|------|------|
| Base class read; N homogeneous executors implement derived classes | `fork="base-ready" fork_source="reader"` |
| Understander has analyzed the project; multiple executors start directly | `fork="code-ready" fork_source="reader"` |
| Spawn an executor right after analyzing; sync the leader's current reasoning immediately | `fork=true` |
| Fork from the leader's own snapshot (omitting `fork_source` defaults to leader) | `fork="code-ready"` |
| Members of different specialties each carry the same project understanding for their own domain | `fork="code-ready" fork_source="arch"` |
| Fork the same analysis snapshot to two members driving plan X / plan Y, making results comparable | `fork="analysis" fork_source="analyst"` |
| Stage A's output becomes stage B's input (pipeline handoff) | `fork="stage-a-done" fork_source="stage-a"` |
| A member goes stale / is replaced; the new member inherits its checkpoint | `fork="mid-work" fork_source="<old-member>"` |
| Review: fork the executor's same snapshot to cross-check with identical understanding | `fork="code-ready" fork_source="executor"` |
| Keep only the post-checkpoint working state; skip the old history | `fork="code-ready" fork_source="reader" fork_mode="after"` |
| Keep the settled analysis; compress the recent detail before starting work | `fork="code-ready" fork_source="reader" fork_mode="keep_before_compact_after"` |
| Large context; compress the old analysis, keep the recent work | `fork="code-ready" fork_source="reader" fork_mode="keep_after_compact_before"` |
| Keep the established analysis verbatim, drop the trailing noise | `fork="code-ready" fork_source="reader" fork_mode="before"` |
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
