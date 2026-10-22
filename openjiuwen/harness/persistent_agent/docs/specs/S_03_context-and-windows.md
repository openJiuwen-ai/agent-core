# S_03 上下文窗口、会话状态与归档规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | S_03 / context-and-windows |
| 关联模块 | [seams.py](../../execution/seams.py)、[BaseKVStore 注入集合](../../ports.py)、[integration.py](../../schema/integration.py)、[window_assembly.py](../../execution/window_assembly.py)、[session_state.py](../../execution/session_state.py)、[session_record.py](../../schema/session_record.py)；PA-11 agent-core 组件已接入，真实服务端后端待联调 |
| 最近一次修订日期 | 2026-10-22 |
| 关联 feature | [F_04](../features/F_04_contract-convergence.md)、[F_05](../features/F_05_execution-entries.md)、[F_11](../features/F_11_window-management.md)、[F_12](../features/F_12_session-history-search.md)（PA-12 设计与实现）、[F_17](../features/F_17_session-state-record.md)、[F_18](../features/F_18_output-handoff.md)；待实现与验证范围见[开发计划](../designs/persistent-agent-development-plan.md) PA-06、PA-10～PA-12；[F_20](../features/F_20_continuation.md)（PA-18 联动修订） |
| 设计依据 | [架构设计：窗口](../designs/persistent-agent-architecture.md#44-上下文窗口管理)、[换窗](../designs/persistent-agent-architecture.md#522-运行中换窗主动交接容量兜底与状态提交)、[保存规则](../designs/persistent-agent-architecture.md#7-数据管理与保存规则) |

本规约唯一维护 Session 当前窗口、会话状态记录、内部 WindowStore 接入、归档与本 Session 历史；合并旧 S_05 中的状态保存责任。

## 1. 范围、术语与适用条件

### 1.1 责任范围与当前边界

BaseKVStore 注入位及 LoadedRecord、WindowAssembly、ExecutionEnding、SessionStateStore / WindowAssembler 协议已有定义；PA-06 新 Request 窗口组装、PA-10 会话状态记录及 PA-11 的逐轮 checkpoint、主动换窗、容量兜底、归档和精确恢复已接入 agent-core 组件执行链并通过组件测试；PA-12 已增加当前 Session 归档关键词搜索与 `execution_id` 归档过滤，并通过定向组件测试。工具执行中途结束时由原生保留真实事实的 core 改动已有组件实现、待 core 维护者评审。真实 AgentServer BaseKVStore、单 Session 串行和真实 provider 容量边界，以及 PA-12 的宿主持久性、隔离和跨重启检索仍待联调；组件实现不代表真实宿主门禁已开放。共享值对象、标记和协议定义在 schema/integration.py，seams.py 兼容再导出。

窗口归属 Session，只有 Execution 读写；始终加载 Session 的当前记录，不为每个 Request 保留可切回的独立活动窗口。宿主保证同 Session 单一执行者和后端隔离；本模块定义格式、序列化、整理、保存与检索逻辑，宿主不需要理解记录内容。

执行顺序由 [S_01](S_01_public-api-and-execution.md) 定义；正常确认与续做如何使用记录由 [S_05](S_05_confirmation-and-continuation.md) 定义；群聊及知识是 [S_09](S_09_group-chat-and-knowledge.md) 的外部事实；文件读取由 [S_04](S_04_tools-and-workspace.md) 定义。本文不复制这些系统的消息库、知识采集游标或外部操作结果。

### 1.2 复用边界

复用 DeepAgent 的 ContextEngine、完整模型 / 工具消息组以及 Session state；动态内容可以经 PromptAttachmentManager 的末尾增量通道进入窗口。按已确认设计，不挂载 ContextAssembleRail、PersonalContextRail 或 ContextProcessorRail 的默认刷新 / 压缩链，不能让其改写前缀或把私有状态卸载到办公空间。知识导航在 Execution 开始解析，执行中需要新增内容只能追加。

## 2. 数据结构与事实归属

| 对象 | 作用与事实权威 | 修改入口 |
| --- | --- | --- |
| 内存当前窗口 | 本次 Execution 的 ContextEngine 内容；通常只追加，PA-11 仅在安全边界提交后整体替换 | WindowAssembler、原生循环与窗口管理 |
| 会话状态记录 | Session 最近一次完整 ReAct 轮次的已保存现场及当前窗口指针；跨 Execution 的唯一状态来源 | PA-10 检查点、结束保存及 PA-11 换窗提交均覆盖同一条 WindowStore 记录 |
| 旧窗归档及索引 | 换出窗口的完整原文、来源引用及切换元数据；索引只供导航，归档原文是历史资料而非当前指令 | WindowStore 归档；PA-12 的 WindowStore.search 与 Persistent 专用工具检索 |
| LoadedRecord | load 写回新 Session 后返回的接入结果 | SessionStateStore.load |
| WindowAssembly | native_input 与服务端确认扫描位置 last_group_message_id；无可信进度时保留加载时游标 | WindowAssembler.assemble |
| SessionCheckpoint | request_id、execution_id、完整 ReAct 轮次序号、检查点类型及当前群消息扫描游标；不保存模型调用栈或半轮状态 | Persistent 执行循环交给 checkpoint |
| ExecutionEnding | outcome、request_id、reply_target、output_refs、终态扫描游标 last_group_message_id，以及交接摘要 answer_text、answer_segments、shared_files、handoff_failure | 执行入口交给终态 save；游标由记录编码器提升至顶层，不放在 ending 内 |

LoadedRecord 的 waiting 引用 [S_05](S_05_confirmation-and-continuation.md#2-确认输入与等待现场) 的 WaitingScene，另含服务端确认已扫描到的位置 last_group_message_id；与实际展示范围和正文是否完整分开，不表示群消息成为任务输入。

PA-10 会话状态记录采用 schema_version=1 的唯一最终结构：白名单为 context 中的默认窗口、`__react_agent_interruption__`（只在等待确认时）、INTERRUPT_AUTO_CONFIRM_KEY、DeepAgent 状态快照 `deepagent`、当前检查点元数据、顶层群消息扫描游标及可选终态 `ending`，作为一条 JSON 记录写入（结构见 [F_17](../features/F_17_session-state-record.md)）。`ending` 只表示已结束 Execution；执行中检查点不设置本次 Execution 的 ending。由于尚未启用真实持久化，直接调整未发布的 V1 结构，不兼容此前组件级 V1 记录；启用前检查后端没有旧结构记录，若发现则显式迁移或清理。未知版本、损坏或身份不符仍失败关闭。结束信息不含 pending_answer：超长回答分段交出，完整正文随窗口保存；带该键的记录（含值为 null）按记录无效处理。中断请求带类型标记保存，只接受 InterruptRequest 与 AskUserRequest，其他类型明确失败。白名单以外的 Session 字段不保存，线程、锁、连接、隐藏推理和凭证不进入记录；不复制旧 CheckpointBody。

PA-11 在同一条 Session 状态记录中增加 `current_window_id`、`window_seq` 和 `last_rollover`：前两者标识唯一当前窗口及其 Session 内序号，后者记录最近一次已提交切换的旧 / 新窗口标识、`archive_ref`、触发类型和 token 用量。PA-10 在每个完整 ReAct 轮次后提交当前窗口；若该轮触发换窗，PA-11 的新窗口状态提交同时满足本轮检查点要求，不另写重复快照。执行中检查点、换窗提交与 Execution 结束保存共用 WindowStore 和状态键；不建立 `replacement_history` 或第二个活动窗口指针。schema_version=1 的唯一最终结构中，活动记录必须恰有非空 `checkpoint`、空 `ending`；终态记录恰有空 `checkpoint`、非空 `ending`。游标保存在记录顶层；不兼容此前组件级旧 V1 结构，也不提供读取映射。未知版本或无效记录不得降级为空状态；真实后端启用前须确认不存在旧结构记录。

键由导出函数 `session_key_prefix(agent_id, session_id)` 派生：两个 ID 分别百分号编码（冒号、百分号均转义）后拼成 `pa:{编码后的 agent_id}:{编码后的 session_id}:`，状态键为前缀加 `state`，归档与索引键为前缀加 `archive:{序号}`、`archive_index`；记录中保留原始 ID，身份按原值精确比较。索引记录序号、execution_id、覆盖的 request_id 和时间；命名空间及跨主体隔离由宿主保障，不从键中包含 ID 推导已授权。

## 3. 不变量

1. **I-1 唯一状态：** 跨 Execution 只由 WindowStore 保存 / 加载白名单会话记录；`current_window_id` 是当前窗口唯一持久指针，不使用另一份 checkpointer 状态、ContextStore 活动指针、普通事件或 Checkpoint 恢复点作为并行权威。
2. **I-2 追加边界：** Execution 内系统提示词和工具定义固定；动态内容只追加。每轮完整 ReAct 结束后、下一次模型调用前保存检查点；PA-11 只允许在安全边界完成归档与状态提交后整体替换窗口，结束整理仍在保存边界进行。
3. **I-3 最新窗口：** 新任务、崩溃续做和停止后继续加载 Session 最新记录，不回退到原 Request 的旧窗口。
4. **I-4 保存先行：** 普通继续路径先保存完整轮次检查点再调用下一轮模型；终态路径整理 → 保存整条会话记录 → 记 paused / final，不能先确认结束再保存上下文。
5. **I-5 私有存储：** 状态与归档后端不能位于 Agent 工具可访问的办公空间或 SysOperation 内；内存测试后端不能证明跨重启持久化。
6. **I-6 消息完整：** 换窗前工具调用与结果须完整成组；旧窗先可靠归档，不能静默丢弃必要事实或用交接要点替代可回查原文。未交出的 `share_file` 登记依据及尚未确认的副作用线索须在新窗交接内容中保留或以稳定引用指向。
7. **I-7 提交顺序：** 先幂等归档旧窗，再将新窗口及 `last_rollover` 写入同一条 Session 状态记录，状态写入成功后才替换运行时上下文；普通 `window.switched` 仅为通知，失败不回滚已提交状态。
8. **I-8 对账与幂等：** 同一旧窗口最多提交一个后继窗口；相同切换重试复用稳定 `archive_ref`，内容冲突不得覆盖。恢复先读取 Session 状态，再仅凭与 Session、旧 / 新窗口、原 Request / Spec 精确匹配的归档元数据对账，不以普通事件推断提交结果。

## 4. 接入协议与错误语义

### 4.1 `SessionStateStore.load(context) -> LoadedRecord`

调用方为执行 / 恢复入口，使用 S_01 要求的新空 Session 和 NoopCheckpointer。组件读出记录后先校验，再写回 Session state；确实没有记录时返回空 LoadedRecord。后端读写失败为 DEEPAGENT_RUNTIME_ERROR，不能降级为“没有记录”，不开始原生执行。记录无效（JSON 损坏、结构不合法、版本不支持、身份与本次执行不精确相等、消息或中断请求无法恢复、工具结果不紧跟本组调用、窗口与中断状态不一致）为 DEEPAGENT_CONTEXT_PARAM_ERROR，诊断日志不含记录正文；入口在加载路径上只透传这一类框架校验错误。加载失败不调用模型、不退回空窗口、不覆盖原记录；加载时的补位只作用于内存。工具结果按调用组配对；正常等待确认时，只有最后一组中被中断的调用可以缺结果，且窗口、中断状态的 ai_message 与中断条目中的调用须按 ID、类型、名称和参数逐个一致（原生恢复执行中断条目中的调用），这是合法现场；三者不一致时保存失败、加载判为无效，不互相覆盖。

### 4.2 `WindowAssembler.assemble(context, request, record) -> WindowAssembly`

PA-06 已实现：加载之后，按稳定到临时顺序保留窗口历史，再追加本次执行头、临时背景和任务输入。临时背景包括最近群消息、知识导航和必要续做日志；触发消息不重复进入背景，排队的新消息不能变成当前 Request 的指令。原任务输入置于最后。

追加消息的元信息键为 `persistent_window_part`：执行头值 execution_header，临时背景值 temporary_background。返回 native_input 及本次群消息游标。正常确认恢复不调用 assemble、不读取新背景，也不追加执行头或任务输入。来源和读取权限见 S_09；组装失败不允许原生流在半装配窗口上运行。

PA-06 已实现窗口组装（`execution/window_assembly.py`，接入点 `seams.window_assembler`），设计与取舍见 [F_06](../features/F_06_window-assembly.md)：

- **组成与位置**：加载会话状态记录之后，在 Session state `context` 中已保存窗口的末尾依次追加执行头、临时背景块（均为 `UserMessage`，带上述标记），任务输入由原生 ReAct 加在最后。新任务的 query 为 RequestInput.text 原文；续做的 query 在原文之后追加可选继续指示。系统提示词和工具定义不变；此后执行中只追加。
- **执行头**：一行，`【本次执行】类型：新任务；request_id：…；execution_id：…；来源：私聊|群消息|定时任务`，有触发消息时再加消息 ID、发送者（显示名、ID，群消息另注人或 Agent）、接收时间，有附件时加附件引用。PA-18 已在组件中接入续做执行头：类型为“续做”并加 `recovery_ref`；是否可进入该分支由 S_05 第 4.4 节的正式入口门禁控制。
- **续做材料**：先处理本 Request 的旧等待现场，再追加续做执行头、最近群消息、执行链中各次执行的日志预览与续做规则、原任务及继续指示。预览由程序把日志事件原样转为 JSON 逐行列出，模型用量与回复片段只计数，超长字段截断并列出路径；完整记录由受控 read_execution_record 按事件分页查询同一快照。预览与规则属于临时背景，结束时删除，执行头、任务和工作过程保留。具体契约见 [F_20](../features/F_20_continuation.md) D-06、D-08 与 [F_19](../features/F_19_execution-log-record.md)。
- **群消息 Execution**：只有 `request.kind == group_message` 自动读取当前群最近消息；私聊和定时任务即使注入 `GroupChatPort` 也只获得显式搜索能力，不自动注入群背景。以加载的游标为 `after`、以触发消息为 `trigger_message_id` 读取（增量与触发位置回补合并去重、排除触发消息），结果须属于本次执行；组装器再排除触发消息并按接收时间排序。被忽略的消息照常列出，排队中的请求逐条标注且说明只作背景、不是指令，已撤回的不显示正文，正文暂缺的标注“正文暂缺，需要时可查询群聊记录”。`empty` 不加背景块；`incomplete` 仍加背景块并注明没有读取完整，不能当作“没有消息”。
- **游标**：取读取结果中服务端确认的扫描位置 `scanned_through`，与展示范围无关；结果不带该位置时以及私聊 Session 沿用加载时的游标。
- **来源可用性**：PA-23 当前群读取器已接入；群消息缺少 `GroupChatPort` 仍由入口在事件前拒绝，读取失败不降级为空历史。注入 `KnowledgePort` 的执行在知识导航（PA-13）接入前仍明确拒绝（`DEEPAGENT_CONFIG_PARAM_ERROR`）。群消息 Request 的知识接入拒绝发生在 PA-07 判断后，此时可能已有判断事件。
- **等待中的调用**：加载的记录带等待现场，或 Session state 带 `__react_agent_interruption__` 时，新 Request 不组装，校验拒绝（`DEEPAGENT_CONTEXT_PARAM_ERROR`），窗口不追加；窗口有未配对的工具调用时同样拒绝，并记 error 日志（见上文“窗口安全”）。
- **上下文 rail**：Persistent 构造（`PersistentAgentSpec.resolve_parts`）拒绝挂载 `ContextAssembleRail`、`PersonalContextRail`、`ContextProcessorRail`（含子类），`ContextProcessorRail` 的默认压缩链因此也不会挂载；`DeepAgentSpec` / `create_deep_agent` 不变。
- **增量通道**：保留原生 `PromptAttachmentManager.sync_to_context`（首次快照、之后只追加变化）；一期 Persistent 自身没有执行中追加的动态内容。
- **续做材料**：续做只有必需材料，详细日志通过受控工具分页读取。PA-18 不检查模型容量（2026-10-19 按开发者决定移除，见 F_20 D-09），不因请求超限或计数不可靠而暂停；启用 window_management 时由 PA-11 按下文规则换窗。不截断原任务或静默删除未决写操作。
- **换窗容量**：PA-11 使用 `rollover_threshold = floor(context_window × 80%)`。未主动换窗时，下一次完整请求按上下文引擎的用量感知计数策略计算，超过该阈值就触发换窗；新窗口候选完整请求复用同一计数策略，但独立限制为 `new_window_limit = floor(context_window × 40%)`，不是 80%。计数优先取最近有效模型 `total_tokens` 并估算后续新增消息；无有效基线时用当前 ModelContext 计数器，缺失或失败则估算清理后的消息和工具内容。估算值照常参与阈值比较，不因估算本身暂停。`context_window` 每次模型调用前从当前 `ModelContext.context_window_tokens()` 获取，即上下文引擎解析出的有效窗口；窗口无法解析时暂停为 `error`。80% 是 PA-11 的换窗阈值，不是 provider hard-limit 保证。

### 4.3 `SessionStateStore.checkpoint(context, checkpoint) -> None`

在完整 ReAct 轮次结束、且该 Execution 将继续运行时调用；模型回复已收敛，批内工具全部完成并经过结果处理，所有调用与结果完整配对。实现先同步原生窗口和 DeepAgent 状态，校验消息完整性，再将白名单字段、完整当前窗口、检查点身份 / 序号 / 类型及最新群消息游标组成一条记录，一次 set 写入同一 Session state 键。检查点不删除临时背景、不压缩执行头、不追加执行结束标记，也不把当前 Execution 写成已结束；只有持久化成功后才允许下一次模型调用。该轮若执行 PA-11 换窗，则以归档后的旧窗和提交的新活动窗口完成同一检查点，不再额外写普通轮次快照。同步、校验、编码或存储失败时停止继续推理，不宣称检查点成功；后端 set 抛错可能已落盘，恢复时结合重载记录与执行日志判定。

### 4.4 `SessionStateStore.save(context, ending) -> None`

保存前先让原生把内存中的最新窗口（本 Session 已有原生上下文时；没有时保留 Session 中已加载或已装配的窗口）与 DeepAgent 状态写回 Session state，再整理窗口，把白名单字段与结束信息组成同一条记录，一次 set 写入；context 与中断状态不能分两次独立保存。同步失败、存在默认窗口以外的上下文、卸载缓冲不为空、等待确认却没有中断状态、非中途结束的结局仍有缺结果的调用、工具结果不紧跟本组调用、编码失败或后端写入失败，均为运行异常，执行入口不记结束事件。写入前的失败不调用后端写入，原记录不变；后端写入本身抛错时不能保证记录未改变（可能在写入落地后才报错），下一次加载可能读到本次记录，续做以执行日志为准。调用成功只证明会话记录保存，不证明结束事件或候选输出完成。

### 4.5 WindowStore 与历史检索（PA-11、PA-12 agent-core 组件已实现；真实宿主待联调）

PA-11 的 Session 状态读写、窗口归档、归档索引和精确恢复已有 agent-core 组件实现。归档保存被换出窗口的完整规范化消息，以及用于重建候选新窗口的切换元数据：旧 / 新 `window_id` 与 `window_seq`、稳定 `archive_ref`、`trigger_kind`、`WindowSeed`、保留消息组引用、候选指纹、token 用量、`request_id` / `execution_id`、原 Spec 指纹、轮次序号和群消息游标；不重复保存完整的新窗口候选。恢复时从旧窗口快照、seed 和保留项引用重建候选，校验指纹，并先持久化修复后的活动状态再加载到 Session。`archive_ref` 由 WindowStore 根据 Session 和被换出窗口序号派生，调用方不得指定物理键；重复写入相同内容可复用，冲突内容不得覆盖。归档按 Session 私有。PA-12 的 `WindowStore.search` 枚举当前 Agent / Session 实际归档键值，校验后搜索用户消息、模型可见回复、工具调用名称和参数、工具结果消息中的工具名及结果文本；不搜索活动窗口、其他 Session、隐藏推理、系统提示或工具定义，也不搜索二进制附件内容。匹配为不区分大小写的字面子串，工具 `search_session_history` 只在 Persistent Agent 构造链注册；命中返回有界原文摘录和旧 `window_id` 来源。归档原文是历史资料而非当前指令。结果状态 `found` / `empty` 仅在扫描完整时使用；损坏或缺失内容可能返回 `incomplete`，无法可靠开始时返回 `failed`；`results_truncated` 只表示展示省略，不表示扫描未完成。`archive_index` 只用于导航与完整性核对；索引缺失、损坏或漏列现存归档时可从实际归档重建，索引引用缺失归档时不得宣称结果完整。搜索只读归档正文和 Session 状态；索引缺失或失效时可能重建派生索引。现有 `BaseKVStore.get_by_prefix` 会一次返回完整键值映射，模型结果虽有界，扫描瞬时内存仍会随该 Session 保留的归档总量增长。真实 BaseKVStore 和 JiuwenSwarm 持久性、隔离及跨重启检索仍待联调。

## 5. 窗口与保存生命周期

### 5.1 执行中与安全换窗（PA-11：agent-core 组件已实现，真实宿主待联调）

启用 `window_management` 时，Persistent 构造入口增加中英文 `persistent_context_management` 系统提示词分节，指导 Agent 在自然断点自主判断换窗、保留必要交接并继续未完成工作；不把阶段变化当作必须换窗的条件。分节沿用既有语言选择和提示词模式过滤，完整模式下进入模型系统提示词，不修改用户人设和原 Spec 指纹。提示词不代替以下运行时校验，也不保证某个具体任务一定触发主动换窗。个人版和未启用该能力的实例不注入此策略。

普通模型回复、工具调用和结果沿原生路径追加。每轮完整 ReAct 结束且 Execution 将继续时，PA-10 在下一次模型调用前同步并持久化当前窗口检查点；执行中的消息流或未完成工具批次不单独落盘。PA-11 有两个触发入口但共用一条换窗流水线：Agent 在合适的阶段边界通过内部 `new_context` 控制动作提交受约束的 `WindowSeed`，这是主路径；若本回合没有主动请求，窗口管理组件按上下文引擎策略计算下一次完整模型请求的 token 数，超过 `floor(context_window × 80%)` 时触发兜底，正好等于阈值时不触发。计数优先复用有效模型用量并估算其后的新增消息；没有用量基线时调用当前 ModelContext 计数器，并在计数器缺失或失败时估算清理后的消息和工具内容。估算值照常参与比较，不因估算本身暂停。每次模型调用前都从当前 `ModelContext.context_window_tokens()` 刷新有效窗口、80% 触发线和 40% 新窗上限，模型切换后采用新值。上下文引擎无法提供正整数窗口或计数过程发生不可恢复异常时暂停为 `error`，不继续调用模型。

`new_context` 是 Persistent Agent 内部控制动作，不是用户调用的业务工具，也不直接改写存储或作为公共 Python API。WindowSeed 是 Agent 提交的有界交接建议，包含当前目标、已完成进度、下一步、约束 / 决策、未决问题，以及经执行日志核验的未决副作用引用和稳定证据引用；它不构成事实回执、权限或授权。容量兜底没有 Agent seed 时，由窗口管理组件根据已保存的任务目标、执行事实和最近完整交互整理最小交接要点，不额外调用模型生成摘要。

当前确认链中仍有效的 `share_file` 调用及成功结果须位于新窗最后一个执行头或结束标记之后，不能只保留登记摘要。

2026-10-22 已实现的保留调整：普通写操作只有在 Persistent 回调记录的结构化终态成功依据与调用、参数及结果正文匹配时，才移出候选正文，不再作为可选消息填回，仅以有界交接线索承接；完整调用及结果仍在旧窗归档。失败、超时、中断、后台仅启动或标记缺失 / 不匹配不能视为无副作用，仍完整成组保留待核实；混合调用组存在任一未决写操作则整组保留。成功标记是内部消息 metadata，不接受模型自报或正文中的成功文本；随消息保存，不改变公共记录 schema，也不追认历史无标记记录。自动完成提示不得挤占原交接与必要证据的空间，必要时退回原 seed 重建。有效 share_file 登记和安全换窗边界不变。常见后台启动语法的保守识别不构成任意程序终态保证；具体实现、验证和限制见 F_11 第 5.3.1、8.3 节。

候选预检查针对重组后的新消息前缀，不得沿用保留消息上的旧累计 `total_tokens`。PA-11 在计数副本中使旧 usage 失效，复用上下文引擎计数器及回退策略计算系统指令、候选消息与工具定义，不修改原窗口及其归档证据。当前活动窗口的正常追加仍可使用有效用量基线；候选装入后还须重建并复核最终完整请求。40% 是新窗口提交时的上限，不限制之后正常追加；等于上限允许，超过时恢复旧窗口且不提交切换。归档 token_usage.input_limit 记录本次 40% 上限，new_window_limit_ratio=0.4，rollover_threshold_ratio=0.8 仍表示触发比例。

只在安全边界切换：当前模型回复已结束，并行工具已结束或收敛，所有工具调用与结果完整成组。新窗口由固定系统指令和工具定义、本次原始 Request、经过校验的 WindowSeed，以及按 token 预算挑选的近期完整消息组构成；不把旧窗全文复制进新窗。必要任务信息、未交出的 `share_file` 登记依据和未决写操作线索必须保留或以稳定引用衔接。换窗后重建的完整请求也按上下文引擎策略计数，超过 `floor(context_window × 40%)` 时产出 `paused(context_unfit)`，不静默截断。

普通路径顺序固定为：同步原生状态与 DeepAgent 状态 → 校验本轮已完整收敛 → 一次写入当前窗口及检查点元数据 → 成功后才发起下一次模型调用。若该轮触发换窗，则以以下提交代替普通检查点，避免重复快照：准备并验证候选新窗 → 以稳定 `archive_ref` 幂等保存本轮结束后的旧窗完整原文和切换元数据 → 将新窗口、`current_window_id`、`window_seq`、`last_rollover` 与本轮检查点一次写入 PA-10 的同一条 Session 状态记录 → 持久化成功后替换运行时上下文 → 尽力记录 S_02 的普通 `window.switched` 通知。归档失败不提交新窗口；状态写入失败或结果未知时停止当前执行，不在候选新窗或旧内存窗继续调用模型。事件失败不回滚已提交的状态或内存切换。

恢复以 Session 状态记录的当前窗口指针为准。若进程在旧窗归档后、新状态提交前中断，加载时只有在归档元数据与 Session、旧窗口快照、原 Request、recovery_ref、Spec 指纹及轮次序号精确匹配时，才从旧窗口快照、已保存的 seed 和保留项引用确定性重建候选；重建结果须匹配归档候选指纹，随后先补写活动状态，成功后才加载到 Session。不匹配时失败关闭，不调用模型。若状态已写入但运行时替换前中断，恢复加载状态记录中的新窗口。PA-11 不提供跨键全局事务、分布式租约或跨进程接管保证；AgentServer 须保证同一 Session 单执行者串行及 BaseKVStore 的持久、隔离单键写入。

### 5.2 执行结束整理

| 内容 | 保存前处理 |
| --- | --- |
| 已保存历史 | 原样保留 |
| 本次执行头 | 保留；PA-06 生成的执行头已是一行，原样保留 |
| 临时背景 | 删除，下次 Execution 按当前版本重新注入 |
| 用户输入、确认结果及工作过程 | 保留；可重新取得的大结果可改为来源引用 |
| 交接正文 | 结局不是等待确认、本次有最终回答，且 after_invoke Rail 改写后与本 Execution 最后一条原生回复不同时，在结束标记之前追加一条带 `persistent_window_part=handoff_text` 标记的用户消息，内容为 `【实际交出的最终回答】` 加实际交接的正文；整批准备失败时同样保留，作为后续交接的依据；相同则不追加 |
| 执行结束标记 | 结局不是等待确认时，在窗口末尾追加一条带 `persistent_window_part=execution_end` 标记的单行用户消息，写明 request_id、execution_id、结局、交出的候选（`最终回答 N 段`、`文件 <逻辑路径>（<共享方式>，<output_id>）`，以结束记录为准，没有候选写“无”）、整批准备失败时的未交接原因（逻辑路径与类别），以及服务端确认已扫描到的群消息位置 |

等待确认时，末尾待结果的工具调用一律不动；结束标记写入记录元信息，不追加到窗口尾部。窗口中的 `share_file` 调用及其结构化成功结果是文件登记依据：收尾只扫描最终窗口最后一个结束标记或执行头之后的有效结果（S_04 第 4.3 节）。新任务、崩溃续做及非确认暂停后继续开启新的扫描范围，旧登记只供查证，不自动交出；正常确认恢复不追加执行头，沿用本确认链有效登记并计入上限。换窗（PA-11）须把有效调用及成功结果完整保留在该扫描范围内。临时背景仍可按标记删除。完成、取消、失败与可恢复暂停需保存；忽略群消息未写入窗口，无需保存；进程崩溃无法保存。停止能否取得完整现场须按实现验证，不据控制入口返回宣称已保存。

**中途结束后的安全窗口：** 停止、取消或执行中报错时，原生执行路径保留已发出的调用组（Persistent 显式启用）：后续处理（含安全 Rail 的替换）全部完成的结果按最终结果保留；已返回或已抛错、但后续处理未完成的调用只保留说明，不保存未经处理的内容。这里的“处理完成”指事件链走完，不等于安全检查成功：回调框架对 Rail 的普通异常只记日志、继续执行其余 Rail，内容照常保留；宿主安全 Rail 要拦截内容，须先替换成安全说明，再请求强制结束。PA-10 保存时按调用组为缺失工具结果的调用幂等补充标为 `stopped_tool_placeholder` 的结果，说明“结果未取得，实际效果需核实”，具体结束原因由结束记录说明；已有结果、说明及原生“已中断”结果不重复补位，正常等待确认不补位。加载结局为停止、取消或失败的旧记录时同样修补。保存补位、加载修补与原生事实保留均已有组件实现，core 改动待 core 维护者评审；“停止、取消或报错 → 保存 → 重新加载 → 下一任务”已有组件验证，仍需真实服务端联调（见 [F_17](../features/F_17_session-state-record.md)）。

### 5.3 后端、清理与部分成功

服务端注入持久 BaseKVStore，保证命名空间隔离、工具不可访问和每次检查点均以单条状态一次写入；沙箱、容器生命周期或 InMemoryCheckpointer 不能提供这些保证。状态检查点与执行事件、最终结局记账分别确认，不构成跨存储事务；保存成功而后续记账缺失时，续做结合检查点元数据、记录结束标记和执行日志查证，不能简单重跑。宿主按保留期清理归档，删除 Agent / Session 时清理 `session_key_prefix` 返回的前缀（由 agent-core 导出，宿主不自行拼接）；不能把计算实例关闭视为删除持久记录。

## 6. 当前限制与替代关系

PA-10 的终态保存语义保持不变；逐轮检查点和最终 V1 记录结构已在 agent-core 实现并通过组件测试（见 [F_17](../features/F_17_session-state-record.md)）。PA-11 的换窗、归档和精确对账也已接入组件执行链；真实 BaseKVStore 持久化、隔离、单 Session 串行和真实 provider 容量边界仍待联调。PA-12 的搜索和只读工具已在 agent-core 实现；PA 包与个人版定向回归通过，完整 harness 回归因缺少 `observability` 可选依赖未能收集。真实 JiuwenSwarm 后端验证仍待完成。最终 V1 将执行中检查点与可选终态 `ending` 分开表达；活动记录只含 checkpoint，终态记录只含 ending。此前组件级旧 V1 格式不在兼容范围内，启用真实后端前须确认并处理旧结构记录。归档保留期由 AgentServer 策略决定。不得沿用旧 epoch、head_revision 或旧 schema 错误名。

原 S_03 的 ContextStore、SessionContextHead、独立内容 revision、window.activated、每次模型调用重装配及即时知识刷新规则已被本规约替代；原 S_05 的 Checkpoint 保存责任合并到本规约的会话记录。旧方案在 Git / feature 历史追溯，不作为现行保证。群聊与知识模型转由 S_09 独立维护。

## 7. 关联规约与验证索引

- S_01 调用 load / assemble / save 并负责业务结局；S_05 规定正常确认保留现场、续做处理过期现场；S_02 定义窗口事件及结束事件的确认。
- S_09 提供获授权资料，S_04 提供当前文件读取；摘要、原文和工具实际结果分别承担不同事实职责。
- 当前接入协议、NoopCheckpointer 隔离与保存先后可定位 [test_entries.py](../../../../../tests/unit_tests/harness/persistent_agent/test_entries.py)；其中替身部分不证明真实后端行为。
- 会话状态记录：[test_session_state.py](../../../../../tests/unit_tests/harness/persistent_agent/test_session_state.py) 覆盖记录格式、键编码、中断请求类型、补位、整理与校验；test_entries.py 用正式存储与组装器覆盖跨 Request 窗口延续、确认现场、停止、后端故障与无效记录，以及工具执行中途停止、取消、报错和宿主安全 Rail 处理未完成时的保存；core 的事实保留由 test_ability_manager_tool_results.py 与 test_react_agent_tool_fact_retention.py 覆盖。均为内存后端的组件验证，不证明真实服务端后端的持久化与隔离。
- 主动换窗、模型容量兜底、归档 / Session 状态部分提交恢复以及 PA-12 历史检索已有 agent-core 组件测试；本轮 PA 包 856 passed / 10 skipped，个人版定向回归 243 passed。真实后端跨重启、隔离、单 Session 单执行者及 provider hard limit 仍待服务端联调。完整 harness 回归因环境缺少 `observability` 可选依赖未能收集。PA-12 组件验证见 [F_12](../features/F_12_session-history-search.md)；PA-06 组装已有组件验证，容量验收未关闭。真实后端和真实模型保证不能由替身测试证明。验证证据维护于唯一开发计划，不能把待执行联调写成已通过。
