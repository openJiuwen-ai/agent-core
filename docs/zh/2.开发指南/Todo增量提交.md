# Todo 增量提交

Flash宿主为Todo工具设置异步`snapshot_guard(session_id, snapshot, committing=False)`。
它在`committing=True`时、文件可能发布前可靠登记初始化已使用。Jiuwenswarm 的 Flash
使用现有会话元数据；为保持旧接入可用，缺少标记时允许首次使用，已经登记后丢失文件
则拒绝重建。未知旧会话的文件丢失无法与首次使用区分，这是已接受的兼容边界。不改变
模型工具参数；未装配 guard 的其他调用保持既有行为。

Todo 工具仍按任务 ID 增、删、改；同一清单的完整读改写在进程内协调，跨进程由文件端条件原子替换保护。模型传递修改意图，不传版本字段。

## 文件与创建规则

新文件为 `{ "plan_id": "...", "revision": 1, "todos": [...] }`。revision 是非负 JavaScript 安全整数，每次实际修改递增；新建/重建更换 plan_id，但不重置 revision。删除最后一项保留带版本的空表。旧数组只读不迁移，第一次实际修改转换；未修改条目的扩展字段保持。

创建策略由宿主实例决定：普通 Core/Code 工具保留原创建行为；Flash 显式设置 `require_explicit_plan_action=True`，活动计划默认拒绝覆盖，继续/追加使用 todo_modify，明确新建/重建传 `plan_action="new"` 或 `"rebuild"`。保留单次 invoke 重复创建限制，失败不消耗创建标记。

`TodoCreateTool` 和 `create_todos_tool` 返回的创建工具默认 `require_explicit_plan_action=False`，因此跨轮默认创建可以替换未完成计划。需要保护活动计划的宿主，应在注册前将返回的 `TodoCreateTool` 实例属性显式设为 True；工厂不自动识别运行模式。此默认值不放宽同 invoke 内重复创建限制。

| 配套源码中的宿主 | 创建工具配置 | 活动计划的默认 create |
|---|---|---|
| Core 工厂、普通调用及 Code | False | 按原创建规则允许替换 |
| Jiuwen Flash | Flash 装配时显式设为 True | 拒绝，须明确传 plan_action=new/rebuild |

OfficeAce 等接入须按实际运行模式和 Core/Jiuwen 配套版本确认，不能仅凭产品名称认定保护开启。旧 Core 不读取该属性时，宿主赋值并不代表策略生效；正式交付仍须先合入配套 Core，再更新 Jiuwen 官方依赖锁并验证。

发布前校验目标快照的 ID、状态与全计划单进行项约束。旧数组按代际过滤后的业务校验不能允许写出多进行项的新格式；迁移冲突在首次发布前拒绝，原文件不动，不自动完成/取消旧任务。读取已有不一致文件不等于允许继续提交不一致状态。

任一次旧数组实际修改都可能触发迁移，并非仅 create。`migration_conflict` 表示旧数组候选不满足新格式约束；`state_conflict` 表示已有新格式的最终候选冲突，业务入口的 `multiple_in_progress` 则表示业务状态校验拒绝。错误列出候选冲突 ID；被拒的新条目尚未写入。调用方可用 todo_get 检查已有冲突任务，依据实际进度显式 update 为 pending/completed/cancelled，保留最多一个进行项后重试。不能仅为消除校验错误自动完成、取消或删除旧任务。

## 宿主接入

- Todo rail 在已有 after_model_call/before_tool_call 中固定批次计划。未解析的对象由第一次实际 Todo 读取解析，同批调用共享结果；新计划只交给后续批次。
- 工作启动时用 `capture_plan` 保存归属，异步回调提交时传同一个 expected_plan；不能在冲突后换绑新计划。
- `TodoTool.update_todos(session_id, callback)` 让宿主回调在最新候选列表上操作。callback 必须只修改清单，不重复外部业务。明确冲突最多三次尝试，其他失败/未知结果不重放。
- 需要在已有事务内分步读取或暂存的宿主逻辑，可使用 `TodoTool.read_todos` 和 `TodoTool.write_todos`；事务外写入仍须使用带读取基线的列表。这两个入口不会经过旧版缓存扩展。
- 冲突后必须重新读取当前文件并重新应用修改意图，callback 因此可能执行多次，不能发送消息或执行外部业务。`snapshot_guard` 也可能在每次尝试中重复调用：`committing=True` 表示本次候选即将尝试发布，不代表提交成功。宿主登记必须幂等，已经登记时不重复写入；不能跳过重试时的生命周期检查。`check_active` 在读取前、发布前及异步 guard 返回后复核，防止等待期间失效的任务继续提交。
- 原 load_todos/save_todos 签名保留；事务内操作候选副本。事务外 save_todos 必须使用 load_todos 返回的带基线列表，不能用裸 list 无条件覆盖。
- 工具的模型可见返回字典保持原业务内容；附属 `todo_snapshot` 属性仅供框架发布事件，JSON 序列化不会向模型追加完整清单。
- `todo_snapshot_versioned` 与 `read_authoritative` 保留来源。结果 outcome、条目计数及 commit_state 仅为框架属性/日志，不新增工具 JSON 或 wire 字段；提交未知不计作零应用或安全重试。Core 部分成功与宿主严格批次规则分别保留。
- 纯输入形状先校验，已有领域错误原样透传，不重复包装 182504。工具卡只补已有字段的非空和对象约束，共享 update/append 数组保持部分字段更新兼容，不要求旧模型接口接受复杂条件 Schema。

宿主事件/历史使用同一次已提交快照。默认旧协议只输出原数组；只有显式启用版本消费的接入方才附加 `todo_meta: {plan_id, revision}`，并由消费者按 revision 接受状态。

事务内使用 `_load_todos` / `_save_todos` 读取候选内容和暂存修改；公开的 `load_todos` / `save_todos` 保留为兼容入口。原生工具及配套宿主事务不得经由旧 I/O 缓存装饰器读改候选内容，否则可能把尚未提交的状态当作磁盘读取结果。此分离不增加文件读取或提交次数。

## 配套要求与限制

本地/Box 均需支持快照读取和条件替换。未支持的实际 Todo 调用返回错误；普通问答不会因 Todo 预初始化被提前结束。升级需配套 SDK、宿主/Box 和消费端，旧 writer 不能继续覆盖新文件。

**后端兼容性：** 本地 FS 和配套 JiuwenBox 提供完整文件契约；AIO、Yuanrong 缺少快照能力时，未挂宿主生命周期 guard 的 list/get 和兼容读取可以只读内容，但不能写入、制造权威快照或清空缓存计划。Flash 及所有修改仍要求完整能力，不退回普通覆盖写。元信息非法、读取错误和文件丢失不当作合法空表。普通文件接口不因此禁用，不新增线上能力握手或必填字段。

快照元信息要求 `exists` 为布尔值、`content_sha256` 为非空且非纯空白的字符串。摘要的字符串表示由后端负责，Todo 层原样传入 `expected_content_sha256`，由同一后端执行条件比较，不限定十六进制长度或移除前缀；缺少元信息的只读兼容规则不变。

本实现不增加执行 epoch、提交日志、能力握手、自动批量合并或存储迁移，也不承诺跨服务 exactly-once 或断电零丢失。
