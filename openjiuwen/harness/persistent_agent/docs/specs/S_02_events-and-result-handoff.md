# S_02 统一事件、记账与结果交接规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | S_02 / events-and-result-handoff |
| 关联模块 | [events.py](../../schema/events.py)、[emitter.py](../../execution/emitter.py)、[CommitPort](../../ports.py) |
| 最近一次修订日期 | 2026-10-15 |
| 关联 feature | [F_02](../features/F_02_execution-protocol.md)、[F_04](../features/F_04_contract-convergence.md)、[F_05](../features/F_05_execution-entries.md)、[F_15](../features/F_15_tool-intent-before-side-effect.md)、[F_18](../features/F_18_output-handoff.md)、[F_19](../features/F_19_execution-log-record.md) |
| 设计依据 | [架构设计：事件与确认](../designs/persistent-agent-architecture.md#62-事件传递确认与流量边界)、[结果交接](../designs/persistent-agent-architecture.md#63-完成与结果交接)、[日志读取](../designs/persistent-agent-architecture.md#833-执行日志的读取整理与能力边界) |

本规约唯一维护事件 Schema、序号与可靠性、CommitPort、候选输出及其持久化 / 投递边界。

## 1. 范围、术语与适用条件

事件 DTO、EventEmitter、执行流映射、工具意图 / 完成组件及候选交接（最终回答分段与 `share_file` 登记文件，[handoff.py](../../execution/handoff.py)）已有组件实现；window.switched 已有 DTO，换窗生产者仍待接入。生产事件队列、日志存储、候选输出保存、发送检查和渠道投递由 AgentServer 实现，本模块不另写本地日志文件。

已接受、已入队、已持久化、业务完成、允许发送和渠道送达是不同确认点。原生流的业务映射及结局优先级由 [S_01](S_01_public-api-and-execution.md) 定义，工具事件何时产生由 [S_08](S_08_tool-effects-and-intent.md) 定义，窗口事件触发由 [S_03](S_03_context-and-windows.md) 定义。

## 2. 数据结构与事实归属

AgentEvent 继承 [S_07](S_07_construction-and-binding.md#2-数据结构与事实归属) 的 InvocationRef 六元归属；另含 schema_version、event_id、event_type、local_seq、occurred_at、visibility、correlation_refs、payload。事件及嵌套负载不可变，按 event_type 选择模型并拒绝错配 / 额外字段；DTO 不分配序号、不保存去重状态，也不证明持久化成功。

- schema_version 只允许严格整数 1；local_seq 是严格正整数，作用域为单个 Execution。
- occurred_at 必须带时区并归一化 UTC，只用于展示，日志按 local_seq 排序。
- event_id 为 evt_ 加完整归属（含 source_ref）和 local_seq 的规范 JSON SHA-256。重传须沿用原 ID、序号和内容，完整提案摘要还覆盖时间、类型、visibility、关联引用与载荷；同 ID 不同内容是冲突，不能覆盖。
- visibility 仅 private / progress / candidate，不存在 delivered。最终展示由宿主授权和脱敏决定，标签不授予公开权。
- 标识最多 512 字符、文本最多 65536 字符、原因最多 4096 字符、引用最多 128 项；完整规范 UTF-8 事件上限 256 KiB。字符和字节限额独立，完整事件计入身份、时间、目标及 JSON 转义开销。`proposal_size()` 返回实际规范字节数。

| 事件 | 负载语义 | 可靠性 |
| --- | --- | --- |
| participation.decided | 判断、原因、证据及模型调用关联 | 普通 |
| execution.started | 任务目标；仅群参与判断路径带决策引用 | 普通 |
| progress.updated / reply.delta | 脱敏进度或展示片段 | 普通 |
| model.completed | model_call_id、配置版本与 ModelUsage | 普通 |
| tool.intent | operation_id、tool_call_id、工具名、可选目标、参数哈希与脱敏摘要 | 关键 |
| tool.completed | 工具调用、成功 / 失败 / 未知、结果摘要及可选引用；写操作含 operation_id | 普通 |
| window.switched | 旧窗 archive_ref 与新窗正整数 window_seq | 普通；生产者待实现 |
| output.proposed | output_id、目标、共享方式，正文或文件的内容摘要、逻辑路径与大小，依赖引用 | 普通；被 paused / final 引用时另须确认候选已保存 |
| execution.paused | 暂停原因、必要的被拦截调用（脱敏参数摘要）及本次交出的文件候选引用 | 关键 |
| execution.final | completed / ignored / cancelled / failed、输出与产物引用 | 关键 |

reply.delta 保留非空的空格、换行与制表符片段，空字符串不生成事件；完整正文和进度必须非空白。paused 原因仅 awaiting_confirmation、stopped、budget_denied、context_unfit、error；只有 awaiting_confirmation 携带 1～64 项、不重复 tool_call_id 的拦截调用。拦截调用的 arguments 是脱敏参数摘要，入口按事件容量截断时设 `arguments_truncated=true`，批准仍关联中断状态中的完整调用。paused 的 output_refs 引用本次交出的候选，stopped 必须为空。budget_denied、context_unfit、error 必须有 detail。非 completed 的 final 以及失败 / 未知的工具结果须有原因；所有工具结果须有非空白 result_summary，不能以引用替代摘要。

## 3. 不变量

1. **I-1 唯一记账：** 一个 Execution 由 EventEmitter 顺序调用 record；入口流只展示，宿主消费流时不再次保存同一事件。
2. **I-2 分级确认：** 所有事件统一 await record；关键事件等待自身落盘，普通事件入队后可返回。普通缺口不能阻塞后续关键记账；关键成功也不证明前序日志完整。
3. **I-3 结束引用：** paused / final 成功前必须确认 output_refs 指向的候选内容、目标和共享信息已可靠保存；缺少候选不能删除引用后记账结束。
4. **I-4 缺口不推断：** 序号缺口或 tool.completed 缺失不表示工具未执行；重试业务副作用须按 S_05 / S_08 查证。
5. **I-5 投递独立：** 文件写成、模型完成、事件流结束或 final 成功均不能证明渠道送达；投递失败只重试交接，不重跑业务任务。

## 4. 接口契约

### 4.1 `AgentEvent.create(execution, **fields)`

从 ExecutionContext 复制归属，校验字段、载荷、版本、事件身份和完整字节数；非法 DTO 抛 Pydantic ValidationError。assert_same_proposal 比较 ID 与完整提案摘要，冲突为 DEEPAGENT_INPUT_PARAM_ERROR。协议只接受当前版本，不盲目反序列化未知版本。

### 4.2 `CommitPort.record(execution, event) -> None`

调用方为 emitter。宿主按 Execution 保存、按 event_id 去重；普通事件接收入队后返回，入队或异步保存失败记诊断，可重试或跳过。tool.intent、paused、final 等待自身持久化，paused / final 还检查所引用的候选。

普通 record 调用失败时 emitter 仍向展示队列交出事件，因此展示不证明已保存。tool.intent 调用失败时 emitter 标记 fatal，抛 DEEPAGENT_RUNTIME_ERROR，不交出该事件，不允许继续业务执行。所有 paused / final（包括没有候选引用的结束事件和群参与判断路径的结束事件）由 `EventEmitter.emit_ending` 记账：首次失败时先按原事件对象把它引用的候选重新记账一次（其中失败只记诊断），再按原事件重试结束事件一次，保留原 event_id、local_seq、occurred_at 与内容；仍失败才标记 fatal 并抛 DEEPAGENT_RUNTIME_ERROR。只补交一轮，不重新保存记录、不调用模型、不执行工具；重交的事件不再次交给展示队列。宿主队列不随 Agent 实例清理而取消。

### 4.3 `read_execution_log(execution, execution_id) -> tuple[AgentEvent, ...]`

宿主只允许读取同一 Session、同一 Request 下的执行，按 local_seq 返回已保存事件的 tuple；允许缺口，非空不保证完整，失败直接抛异常。

消费方（[F_19](../features/F_19_execution-log-record.md) 的 `summarize_execution`）先整份校验再使用：返回值须是非空 tuple，每项重新 model_validate；租户、Agent、Session、Request 与当前执行一致，execution_id 为读取目标；同一份日志只有一个 source_ref（可以与当前执行不同）；同一 local_seq 只对应一份规范化提案，内容相同的重复只保留一条。任一项不满足即整份弃用，不向模型交出任何事件内容，结果标为记录缺失（`missing=True`）；`missing` 只表示没有取得可用日志，不表示上一次没有执行。诊断只含失败类别与定位身份：读取失败与空日志记 warning，其余契约不符记 error。execution_id 不是合法标识时在读取前抛参数错误，不转为记录缺失。

读不到、空日志或契约不符按“上一次记录缺失”处理，不据此启动整段重放；序号缺口与缺少结束记录照常整理并写明。整理规则见 F_19，查证与续做由 S_05 定义。

### 4.4 候选输出交接

OutputPayload 的 share_mode 为 message / upload / attachment / link：

- **message：** 必须有 text，不能带 file_ref、file_path、file_size。
- **upload（上传到目标群空间）/ attachment（作为附件发送到回复目标）：** 必须有 file_ref（`sha256:<64 位小写十六进制>`，收尾时读取内容的摘要）、file_path（逻辑相对 POSIX 路径，1～1024 字符，不以斜杠开头，不含空段、`.`、`..`、反斜杠与控制字符）和 file_size（实际读取的字节数，非负整数）。text 为可选的公开说明，最多 4096 字符。Schema 不限制文件大小，上限是运行时配置（S_04 第 4.3 节）。
- **link：** 协议保留，须带受控 file_ref；本期执行入口不产生。

target_ref 指定目标；dependency_refs 保留来源依赖，本期不填写。执行入口在结局锁定后整批准备、交出候选（编排见 S_01）：

- **来源：** 只有最终回答（after_invoke Rail 之后的实际结果）和经 `share_file` 登记的文件；文件以收尾时最终窗口中当前确认链内的有效登记结果为准，被宿主 Rail 改写或阻断、被说明替换的不算（S_04 第 4.3 节）。中间结论、展示片段、工具结果、错误与暂停说明、推理内容和未登记的文件都不成为候选。
- **目标：** target_ref 一律为当前 Request 的回复目标，确认恢复时取等待现场的回复目标。
- **何时交接：** completed 交回答与文件；awaiting_confirmation、budget_denied、context_unfit、error 只交文件，引用列入 PausedPayload.output_refs；被接受的 stopped、cancelled 不交。
- **顺序：** 回答各段在前，文件按首次登记顺序在后；output_refs 的顺序即投递顺序。
- **身份：** output_id 为 `out_` 加 SHA-256（规范 JSON{namespace_id, agent_id, session_id, request_id, target_ref, share_mode, content}）的前 32 位十六进制。message 的 content 为 {answer_sha256, segment_index, segment_count}，文件为 {file_sha256（即 file_ref）, file_size, file_path, caption}。同一 Request 内相同成果身份相同；内容或影响发送的信息变化则身份不同，已交出的版本不撤回、不替换。身份不依赖 execution_id、序号或时间。
- **分段：** 回答超过单事件容量时拆成多个 message 候选。容量按本 Request 任一 Execution 的最宽事件计算（execution_id 与 source_ref 取最长标识、最大序号、最长时间格式），每段同时满足字符与完整事件字节限额，优先在最后一个换行之后断开，不留下只含空白的段。分段拼接等于原文，同一回答总是切出相同的段。
- **整批：** 每个文件重新取得读授权、解析路径，分块读取并计算摘要与大小。任一文件失败、回答无法分段或引用数超过 128 时，整批一项不交：原本 completed 的改为 paused(error)，detail 写 `handoff not prepared: <逻辑路径>: <类别>`（多项以分号分隔，列前 5 项并注明总数，整体不超过 4096 字符）；其他暂停保留原因并在 detail 中补充。类别为 forbidden、unknown、not_found、unavailable、not_a_file、too_large、answer_unsplittable、too_many_outputs。

每项输出以 output_id + target_ref + share_mode 去重。同一 Execution 内重交沿用原 event_id 与事件内容；跨 Execution 只沿用 output_id，不重新生成业务结果，是否再次交出由 Agent 按续做规则决定。只有文件结果的任务也可 completed；文件存在不能自动成为群公开记录。发送授权检查及投递回执由服务端负责，普通 IM 工具不能绕过当前协作群的发送检查。

**宿主保证（JiuwenSwarm AgentServer，待联调验证）：**

1. 保存 output.proposed 时保存完整候选；文件候选按逻辑路径读取，核对大小与摘要后留存不可变版本，作为“已可靠保存”的一部分，不符视为候选未保存。
2. paused 与 final 记账成功前确认 output_refs 引用的候选已可靠保存；缺失或不符时抛异常，且结束事件本身不落盘。
3. 重交按 event_id 去重；同一 output_id 只接受一份内容，内容不同视为冲突。
4. 只投递被已落盘结束记录引用的候选，按 output_refs 顺序；未被引用的候选不投递，重启后也不补发。
5. 投递按 output_id + target_ref + share_mode 去重；失败只用已留存的版本重试发送，不重开 Execution；无法投递时告知发起人。
6. 发布给 Persistent Agent 的工具中不包含绕过本出口直接发送或上传的工具。
7. 确认卡片在 `arguments_truncated=true` 时提示摘要已截断；批准按交互 ID 关联完整调用。

## 5. 持久化与交接生命周期

EventEmitter 在锁内分配序号、构造事件、等待 record，再排入展示队列，保持产生 / 记账 / 展示顺序。容量试探不 record、不占序号；展示文本可按字符边界拆分，片段拼接与原文相同，非法字段不能被误判为容量超限。最终回答按第 4.4 节拆成有序候选，不截断，也不因容量暂停。

候选输出交接、会话状态保存和结束事件分别确认；具体编排见 S_01。普通日志无需在关键事件前全部补齐。paused / final 记账成功后，服务端处理其引用的已保存候选；候选可以 pending_check / held，发送前复核通过才发送，平台回执才证明送达。旧候选不因 Agent 重启自动补发，撤权内容不能继续投递；投递等待或失败不重开业务 Execution。

## 6. 当前限制与兼容性

类型化 DTO 和组件测试不能证明生产 CommitPort 真正提供关键持久化确认。宿主关键事件落盘、候选保存与核对、第 4.4 节的宿主保证、跨进程崩溃及渠道回执须按关联 feature 验证（F_18 PA-08-V-15）。新增的 file_path、file_size、PausedPayload.output_refs 与 arguments_truncated 须由服务端先发布支持，Agent 才能上线。当前没有 checkpoint.proposed 或 window.activated 事件；旧 Checkpoint 协议不属于本期事件兼容范围。

原生流映射、模型预算和结局判定并入 S_01；本文只维护其使用的事件协议。工具副作用规则由 S_08 独立维护，不在本文复制执行策略。

## 7. 关联规约与验证索引

S_07 提供共同归属；S_01 / S_08 / S_03 分别作为执行、工具和窗口生产者；S_05 消费历史日志并判断继续执行的前提。

| 条款 / 接口 | 检查入口 |
| --- | --- |
| DTO、身份、载荷、版本及重传冲突 | [test_protocol.py](../../../../../tests/unit_tests/harness/persistent_agent/test_protocol.py) |
| 字符 / 字节限额、无损拆分与容量试探 | [test_emitter.py](../../../../../tests/unit_tests/harness/persistent_agent/test_emitter.py) |
| 关键失败、候选交接及流关闭 | [test_entries.py](../../../../../tests/unit_tests/harness/persistent_agent/test_entries.py) |
| 候选身份、分段、整批准备与文件读取 | [test_handoff.py](../../../../../tests/unit_tests/harness/persistent_agent/test_handoff.py) |
| 结束事件补交与重试、最宽事件容量 | [test_emitter.py](../../../../../tests/unit_tests/harness/persistent_agent/test_emitter.py) |
| 工具意图与完成等级 | [test_tool_intent_rail.py](../../../../../tests/unit_tests/harness/persistent_agent/test_tool_intent_rail.py) |

真实宿主持久化和投递证据在 feature / 唯一开发计划维护；测试文件存在不等于这些保证已经验证。
