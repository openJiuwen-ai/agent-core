## Context Inheritance (Fork)

Inherit context from an existing member via `fork` / `fork_source` / `compact`:

| Parameter | Usage |
|------|------|
| **fork** | true: inherit the caller's full current context. A string (e.g. `code-ready`): inherit from the named checkpoint snapshot |
| **fork_source** | Name of the member whose context to fork from. Defaults to leader. Must be an already-spawned in-process teammate |
| **compact** | Enable context compaction: older messages before the checkpoint are compressed into a summary; messages after are kept verbatim. Only effective with a named checkpoint fork |

### Use Cases

| Scenario | Usage |
|------|------|
| Base class read; N homogeneous executors implement derived classes | `fork="base-ready" fork_source="reader"` |
| Understander has analyzed the project; multiple executors start directly | `fork="code-ready" fork_source="reader"` |
| Large context; needs compaction to save tokens | `fork="code-ready" fork_source="reader" compact=true` |
| Unrelated new task | Omit fork |

### Fork Semantics

| fork | compact | Behaviour |
|------|---------|-----------|
| `true` | — | Full injection, no compaction |
| `"ckpt"` | false | Truncate before checkpoint |
| `"ckpt"` | **true** | Split at ckpt: compress before, keep after |

Other combinations rely on built-in context compaction.

### Mechanism

- Inherited messages are the source member's full conversation history (file reads, search outputs, analysis conclusions)
- Have the source member call `checkpoint(name="xxx")` before forking, then use `fork="xxx"`
- `fork=true` inherits the full context at call time; prefer checkpoint mode when later scheduling noise would otherwise be included
