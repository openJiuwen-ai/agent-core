## 上下文继承（Fork）

通过 `fork` / `fork_source` / `fork_mode` 从已有成员继承上下文：

| 参数 | 用法 |
|------|------|
| **fork** | true：继承调用者当前全部上下文。字符串（如 `code-ready`）：从命名的 checkpoint 快照继承 |
| **fork_source** | 上下文来源成员名。默认 leader。必须是已拉起的 in-process teammate |
| **fork_mode** | 保留 checkpoint 的哪一侧：`full` \| `before`（默认）\| `after` \| `keep_before_compact_after` \| `keep_after_compact_before`。见「Fork 语义」 |

### 使用场景

| 场景 | 用法 |
|------|------|
| 基类已读，需要 N 个同质执行者实现派生类 | `fork="base-ready" fork_source="reader"` |
| 理解者已分析项目，多个执行者直接开工 | `fork="code-ready" fork_source="reader"` |
| 上下文很大，压缩旧分析、保留近期工作 | `fork="code-ready" fork_source="reader" fork_mode="keep_after_compact_before"` |
| 完整保留已定型的分析，丢弃尾部噪音 | `fork="code-ready" fork_source="reader" fork_mode="before"` |
| 只要 checkpoint 之后的工作状态，丢弃之前全部 | `fork="code-ready" fork_source="reader" fork_mode="after"` |
| 不相关的新任务 | 不传 fork |

### Fork 语义

| fork | fork_mode | 行为 |
|------|-----------|------|
| `true` | `full` | 全量注入 |
| `"ckpt"` | `before` | checkpoint 之前的消息全量保留 |
| `"ckpt"` | `after` | 从 checkpoint 起的消息保留 |
| `"ckpt"` | `keep_before_compact_after` | 保留前，把之后压缩为摘要 |
| `"ckpt"` | `keep_after_compact_before` | 保留后，把之前压缩为摘要 |

未传 `fork_mode` 时：`fork=true` 默认 `full`，命名 checkpoint fork 默认 `before`。

### 机制说明

- 继承的消息为源成员的对话历史（包含文件读取结果、搜索输出、分析结论）
- fork 前先让源成员调 `checkpoint(name="xxx")` 打快照，再用 `fork="xxx"` 指定
- **fork 前先调 `list_checkpoints` 拿到确切名字**——不要猜 checkpoint 名。填了不存在的名字会静默回退为全量继承
- **快照名在整个团队内唯一**，且归属于其创建者。`fork_source` 应指向创建者（`list_checkpoints` 可看到）——快照的消息索引只对创建者的上下文有意义
- `fork=true` 继承调用时刻的全部上下文，包含后续调度噪音时建议改用 checkpoint 模式
