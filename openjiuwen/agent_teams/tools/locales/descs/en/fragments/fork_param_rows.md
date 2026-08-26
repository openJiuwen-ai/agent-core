| **fork** | at spawn | true: inherit the caller's full current context. A string (e.g. `code-ready`): inherit from the named checkpoint snapshot. See *Context Inheritance (Fork)* below |
| **fork_source** | at spawn | Name of the member whose context to fork from; defaults to leader. Must be an already-spawned in-process teammate. See *Context Inheritance (Fork)* below |
| **fork_mode** | at spawn | Which side of the checkpoint to keep: `full` \| `before` \| `after` \| `keep_before_compact_after` \| `keep_after_compact_before`. See *Fork Semantics* below |
