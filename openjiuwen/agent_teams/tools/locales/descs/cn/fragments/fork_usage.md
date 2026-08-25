## 上下文继承（Fork）

**默认该用**：多个成员需要相同基础理解时，默认应**继承而非重读**——先让源成员调 `checkpoint(name="xxx")` 打快照，再在 `spawn_teammate` 里用 `fork="xxx" fork_source="<源成员>"` 继承。成员创建前先自问：它是否依赖某个成员已有的理解？若是，就用 fork，不要为每个成员各自从头重读。

### 编排顺序（关键时序）

快照**必须先于依赖它的成员存在**，且快照记录的是**源成员自己**的上下文——只能由源成员（不是 leader）调 `checkpoint`：

1. **spawn 分析成员时就埋好打快照的指令**：在它的 `desc` 或首轮任务里写明「读透模块后调 `checkpoint(name="xxx")`」——不要等之后补
2. 该成员读完并打完快照后，用 `list_checkpoints` 核对快照确实已建立
3. **在快照存在之后**再 spawn 依赖它的成员，传 `fork="xxx" fork_source="<分析成员>"`

若已 spawn 的分析成员还没打快照，先 `send_message` 让它读完调 `checkpoint`，`list_checkpoints` 确认后再 fork。若依赖成员与分析成员**同批 spawn**（快照尚不存在），fork 无可继承——先让分析成员跑完一轮、打了快照，再建依赖成员。

### 使用场景

| 场景 | 用法 |
|------|------|
| 基类已读，需要 N 个同质执行者实现派生类 | `fork="base-ready" fork_source="reader"` |
| 理解者已分析项目，多个执行者直接开工 | `fork="code-ready" fork_source="reader"` |
| 刚分析完就建执行者，立即同步 leader 当前推理 | `fork=true` |
| 从 leader 自身快照继承（省略 `fork_source` 即默认 leader） | `fork="code-ready"` |
| 不同专长成员各带同一项目理解，管各自领域 | `fork="code-ready" fork_source="arch"` |
| 同一分析快照给两个成员分别推方案 X / 方案 Y，结果可比 | `fork="analysis" fork_source="analyst"` |
| 阶段 A 的产出作为阶段 B 的输入（流水线接力） | `fork="stage-a-done" fork_source="stage-a"` |
| 成员停摆 / 换人，新成员继承其 checkpoint 接手 | `fork="mid-work" fork_source="<旧成员>"` |
| 复核：fork 执行者同一快照，以相同理解 cross-check | `fork="code-ready" fork_source="executor"` |
| 只继承 checkpoint 之后的近期工作态，不拖入旧历史 | `fork="code-ready" fork_source="reader" fork_mode="after"` |
| 保留定型分析、压缩近期细节后动手 | `fork="code-ready" fork_source="reader" fork_mode="keep_before_compact_after"` |
| 上下文很大：压缩旧分析、保留近期工作 | `fork="code-ready" fork_source="reader" fork_mode="keep_after_compact_before"` |
| 完整保留已定型的分析，丢弃尾部噪音 | `fork="code-ready" fork_source="reader" fork_mode="before"` |
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
