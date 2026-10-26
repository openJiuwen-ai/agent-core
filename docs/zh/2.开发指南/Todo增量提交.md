# Todo 增量提交

Todo 工具仍按任务 ID 增、删、改；同一清单的完整读改写在进程内协调，跨进程由文件端条件原子替换保护。模型传递修改意图，不传版本字段。

## 文件与创建规则

新文件为 `{ "plan_id": "...", "revision": 1, "todos": [...] }`。revision 是非负 JavaScript 安全整数，每次实际修改递增；新建/重建更换 plan_id，但不重置 revision。删除最后一项保留带版本的空表。旧数组只读不迁移，第一次实际修改转换；未修改条目的扩展字段保持。

活动计划默认不能通过 todo_create 覆盖。继续或追加使用 todo_modify；明确新建/重建时传 `plan_action="new"` 或 `"rebuild"`。保留单次 invoke 的重复创建限制，失败不消耗创建标记。

## 宿主接入

- Todo rail 在已有 after_model_call/before_tool_call 中固定批次计划。未解析的对象由第一次实际 Todo 读取解析，同批调用共享结果；新计划只交给后续批次。
- 工作启动时用 `capture_plan` 保存归属，异步回调提交时传同一个 expected_plan；不能在冲突后换绑新计划。
- `TodoTool.update_todos(session_id, callback)` 让宿主回调在最新候选列表上操作。callback 必须只修改清单，不重复外部业务。明确冲突最多三次尝试，其他失败/未知结果不重放。
- 原 load_todos/save_todos 签名保留；事务内操作候选副本。事务外 save_todos 必须使用 load_todos 返回的带基线列表，不能用裸 list 无条件覆盖。
- 工具的模型可见返回字典保持原业务内容；附属 `todo_snapshot` 属性仅供框架发布事件，JSON 序列化不会向模型追加完整清单。

实时事件/历史使用同一次快照的内容和 `todo_meta: {plan_id, revision}`。消费者按 revision 接受完整状态；合法空表和重新打开任务都可传播。

## 配套要求与限制

本地/Box 均需支持快照读取和条件替换。未支持的实际 Todo 调用返回错误；普通问答不会因 Todo 预初始化被提前结束。升级需配套 SDK、宿主/Box 和消费端，旧 writer 不能继续覆盖新文件。

本实现不增加执行 epoch、提交日志、能力握手、自动批量合并或存储迁移，也不承诺跨服务 exactly-once 或断电零丢失。
