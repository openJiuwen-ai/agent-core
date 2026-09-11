| **fork** | spawn 时 | true：继承调用者当前全部上下文。字符串（如 `code-ready`）：从命名 checkpoint 快照继承。见下文「上下文继承（Fork）」 |
| **fork_source** | spawn 时 | 上下文来源成员名，默认 leader。须为已拉起的 in-process teammate。见下文「上下文继承（Fork）」 |
| **fork_mode** | spawn 时 | 保留 checkpoint 的哪一侧：`full` \| `before` \| `after` \| `keep_before_compact_after` \| `keep_after_compact_before`。见下文「Fork 语义」 |
