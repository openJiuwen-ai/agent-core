## 上下文继承（Fork）

通过 `fork` / `fork_source` / `compact` 从已有成员继承上下文：

| 参数 | 用法 |
|------|------|
| **fork** | true：继承调用者当前全部上下文。字符串（如 `code-ready`）：从命名的 checkpoint 快照继承 |
| **fork_source** | 上下文来源成员名。默认 leader。必须是已拉起的 in-process teammate |
| **compact** | 启用上下文压缩。checkpoint 之前的旧消息压缩为摘要，之后的分析全量保留。仅配合 checkpoint fork 使用 |

### 使用场景

| 场景 | 用法 |
|------|------|
| 基类已读，需要 N 个同质执行者实现派生类 | `fork="base-ready" fork_source="reader"` |
| 理解者已分析项目，多个执行者直接开工 | `fork="code-ready" fork_source="reader"` |
| 上下文很大，需要压缩以节省 token | `fork="code-ready" fork_source="reader" compact=true` |
| 不相关的新任务 | 不传 fork |

### Fork 语义

| fork | compact | 行为 |
|------|---------|------|
| `true` | — | 全量注入，不压缩 |
| `"ckpt"` | false | 截断到 ckpt 之前 |
| `"ckpt"` | **true** | ckpt 为分界：之前压缩为摘要，之后全量保留 |

其他组合由系统内置上下文压缩自动兜底，无需手动指定。

### 机制说明

- 继承的消息为源成员的完整对话历史（包含文件读取结果、搜索输出、分析结论）
- fork 前先让源成员调 `checkpoint(name="xxx")` 打快照，再用 `fork="xxx"` 指定
- **fork 前先调 `list_checkpoints` 拿到确切名字**——不要猜 checkpoint 名。填了不存在的名字会静默回退为全量继承
- **快照名在整个团队内唯一**，且归属于其创建者。`fork_source` 应指向创建者（`list_checkpoints` 可看到）——快照的消息索引只对创建者的上下文有意义
- `fork=true` 继承调用时刻的全部上下文，包含后续调度噪音时建议改用 checkpoint 模式
