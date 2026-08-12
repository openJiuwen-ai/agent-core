列出当前所有可供 fork 继承的命名 checkpoint，包含每个快照的名字、消息数、创建者与描述。

| 字段 | 含义 |
|---|---|
| **name** | 传给 `spawn_teammate(fork="<name>")` 的**确切名字** |
| **message_count** | 打快照时的上下文长度 |
| **created_by** | 创建该快照的成员 |
| **description** | 打快照时记录的可选说明 |

## 何时调用

**fork 之前必须调用**——不能凭猜测填 checkpoint 名字。成员创建快照时名字是任意的（见 `checkpoint` 工具），权威清单只在这里。填了不存在的名字，fork 会静默回退为全量继承，你将得不到任何继承的理解。

把返回的**确切名字**用于 `spawn_teammate(fork="<name>", fork_source="<created_by>", ...)`。
