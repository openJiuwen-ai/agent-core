# S_03 上下文窗口、会话状态与归档规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | S_03 / context-and-windows |
| 关联模块 | [seams.py](../../execution/seams.py)、[BaseKVStore 注入集合](../../ports.py)、[integration.py](../../schema/integration.py)、[window_assembly.py](../../execution/window_assembly.py)；WindowStore 与换窗管理尚待接入 |
| 最近一次修订日期 | 2026-10-14 |
| 关联 feature | [F_04](../features/F_04_contract-convergence.md)、[F_05](../features/F_05_execution-entries.md)；待实现与验证范围见[开发计划](../designs/persistent-agent-development-plan.md) PA-06、PA-10～PA-12 |
| 设计依据 | [架构设计：窗口](../designs/persistent-agent-architecture.md#44-上下文窗口管理)、[换窗](../designs/persistent-agent-architecture.md#522-运行中换窗归档摘要与替换)、[保存规则](../designs/persistent-agent-architecture.md#7-数据管理与保存规则) |

本规约唯一维护 Session 当前窗口、会话状态记录、内部 WindowStore 接入、归档与本 Session 历史；合并旧 S_05 中的状态保存责任。

## 1. 范围、术语与适用条件

### 1.1 责任范围与当前边界

BaseKVStore 注入位及 LoadedRecord、WindowAssembly、ExecutionEnding、SessionStateStore / WindowAssembler 协议已有定义；PA-06 新 Request 窗口组装已实现；实际状态保存、换窗和归档检索尚未实现。共享值对象、标记和协议定义在 schema/integration.py，seams.py 兼容再导出。下文相应运行规则属于已确认但待实现的契约，不表示当前门禁已开放。

窗口归属 Session，只有 Execution 读写；始终加载 Session 的当前记录，不为每个 Request 保留可切回的独立活动窗口。宿主保证同 Session 单一执行者和后端隔离；本模块定义格式、序列化、整理、保存与检索逻辑，宿主不需要理解记录内容。

执行顺序由 [S_01](S_01_public-api-and-execution.md) 定义；正常确认与续做如何使用记录由 [S_05](S_05_confirmation-and-continuation.md) 定义；群聊及知识是 [S_09](S_09_group-chat-and-knowledge.md) 的外部事实；文件读取由 [S_04](S_04_tools-and-workspace.md) 定义。本文不复制这些系统的消息库、知识采集游标或外部操作结果。

### 1.2 复用边界

复用 DeepAgent 的 ContextEngine、完整模型 / 工具消息组以及 Session state；动态内容可以经 PromptAttachmentManager 的末尾增量通道进入窗口。按已确认设计，不挂载 ContextAssembleRail、PersonalContextRail 或 ContextProcessorRail 的默认刷新 / 压缩链，不能让其改写前缀或把私有状态卸载到办公空间。知识导航在 Execution 开始解析，执行中需要新增内容只能追加。

## 2. 数据结构与事实归属

| 对象 | 作用与事实权威 | 修改入口 |
| --- | --- | --- |
| 内存当前窗口 | 本次 Execution 的 ContextEngine 内容；运行中只追加，换窗时可替换 | WindowAssembler、原生循环与窗口管理 |
| 会话状态记录 | Session 最新已保存现场；跨 Execution 的唯一状态来源 | WindowStore 的单条记录保存 |
| 旧窗归档及索引 | 换出窗口的完整原文、关联执行与覆盖请求；摘要和索引只供导航 | WindowStore 归档与检索 |
| LoadedRecord | load 写回新 Session 后返回的接入结果 | SessionStateStore.load |
| WindowAssembly | native_input 与服务端确认扫描位置 last_group_message_id；无可信进度时保留加载时游标 | WindowAssembler.assemble |
| ExecutionEnding | outcome、request_id、reply_target、output_refs、pending_answer、last_group_message_id | 执行入口交给 save |

LoadedRecord 的 waiting 引用 [S_05](S_05_confirmation-and-continuation.md#2-确认输入与等待现场) 的 WaitingScene，另含服务端确认已扫描到的位置 last_group_message_id；与实际展示范围和正文是否完整分开，不表示群消息成为任务输入。

待实现的会话记录白名单包括 context、`__react_agent_interruption__`、INTERRUPT_AUTO_CONFIRM_KEY，及需要保存的 DeepAgent 运行状态。白名单以外的 Session 字段不保存，线程、锁、连接、隐藏推理和凭证不进入记录。具体序列化格式由实现特性确认，不能虚构 schema 版本或复制旧 CheckpointBody。

架构已定义的键为 `pa:{agent_id}:{session_id}:state`、`pa:{agent_id}:{session_id}:archive:{序号}`、`pa:{agent_id}:{session_id}:archive_index`。索引记录序号、execution_id、覆盖的 request_id 和时间；命名空间及跨主体隔离由宿主保障，不从键中包含 ID 推导已授权。

## 3. 不变量

1. **I-1 唯一状态：** 跨 Execution 只由 WindowStore 保存 / 加载白名单会话记录，不使用另一份 checkpointer 状态、ContextStore 活动指针或 Checkpoint 恢复点作为并行权威。
2. **I-2 追加边界：** Execution 内系统提示词和工具定义固定；动态内容只追加。运行中换窗是计划内的替换点，结束整理则在保存边界进行。
3. **I-3 最新窗口：** 新任务、崩溃续做和停止后继续加载 Session 最新记录，不回退到原 Request 的旧窗口。
4. **I-4 保存先行：** 整理 → 保存整条会话记录 → 记 paused / final，不能先确认结束再保存上下文。
5. **I-5 私有存储：** 状态与归档后端不能位于 Agent 工具可访问的办公空间或 SysOperation 内；内存测试后端不能证明跨重启持久化。
6. **I-6 消息完整：** 换窗前工具调用与结果须完整成组；旧窗先可靠归档，不能静默丢弃必要事实或用摘要替代可回查原文。

## 4. 接入协议与错误语义

### 4.1 `SessionStateStore.load(context) -> LoadedRecord`

调用方为执行 / 恢复入口，使用 S_01 要求的新空 Session 和 NoopCheckpointer。待实现组件把最新记录写回 Session state；确实没有记录时返回空 LoadedRecord。存储失败不能降级为“没有记录”，入口将调用异常或非法返回作为 DEEPAGENT_RUNTIME_ERROR，不开始原生执行。当前 getter 仍抛 DEEPAGENT_CONFIG_PARAM_ERROR，表示接入不可用。

### 4.2 `WindowAssembler.assemble(context, request, record) -> WindowAssembly`

PA-06 已实现：加载之后，按稳定到临时顺序保留窗口历史，再追加本次执行头、临时背景和任务输入。临时背景包括最近群消息、知识导航和必要续做日志；触发消息不重复进入背景，排队的新消息不能变成当前 Request 的指令。原任务输入置于最后。

追加消息的元信息键为 `persistent_window_part`：执行头值 execution_header，临时背景值 temporary_background。返回 native_input 及本次群消息游标。正常确认恢复不调用 assemble、不读取新背景，也不追加执行头或任务输入。来源和读取权限见 S_09；组装失败不允许原生流在半装配窗口上运行。

PA-06 已实现窗口组装（`execution/window_assembly.py`，接入点 `seams.window_assembler`），设计与取舍见 [F_06](../features/F_06_window-assembly.md)：

- **组成与位置**：加载会话状态记录之后，在 Session state `context` 中已保存窗口的末尾依次追加执行头、临时背景块（均为 `UserMessage`，带上述标记），任务输入作为原生输入 `{"query": RequestInput.text}` 由原生 ReAct 加在最后。系统提示词和工具定义不变；此后执行中只追加。
- **执行头**：一行，`【本次执行】类型：新任务；request_id：…；execution_id：…；来源：私聊|群消息|定时任务`，有触发消息时再加消息 ID、发送者（显示名、ID，群消息另注人或 Agent）、接收时间，有附件时加附件引用。续做（PA-18）沿用同一格式，类型为“续做”并加 `recovery_ref`；在此之前带 `recovery_ref` 的上下文不组装。
- **群 Session**：注入了 `GroupChatPort` 的 Session。只有群 Session 读取最近群消息：以加载的游标为 `after`、以触发消息为 `trigger_message_id` 读取（增量与触发位置回补合并去重、排除触发消息），结果须属于本次执行；组装器再排除触发消息并按接收时间排序。被忽略的消息照常列出，排队中的请求逐条标注且说明只作背景、不是指令，已撤回的不显示正文，正文暂缺的标注“正文暂缺，需要时可查询群聊记录”。`empty` 不加背景块；`incomplete` 仍加背景块并注明没有读取完整，不能当作“没有消息”。
- **游标**：取读取结果中服务端确认的扫描位置 `scanned_through`，与展示范围无关；结果不带该位置时以及私聊 Session 沿用加载时的游标。
- **未接入的来源**：群 Session 在群消息读取（PA-23）接入前、注入 `KnowledgePort` 的执行在知识导航（PA-13）接入前，于接入点明确拒绝（`DEEPAGENT_CONFIG_PARAM_ERROR`）。群消息 Request 例外：PA-07 先完成参与判断才取接入点，此时已有判断事件，会话状态记录（PA-10）未接入时同样如此；群里的定时任务和私聊仍在任何事件之前拒绝。
- **等待中的调用**：加载的记录带等待现场，或 Session state 带 `__react_agent_interruption__` 时，新 Request 不组装，校验拒绝（`DEEPAGENT_CONTEXT_PARAM_ERROR`），窗口不追加；窗口有未配对的工具调用时同样拒绝，并记 error 日志（见上文“窗口安全”）。
- **上下文 rail**：Persistent 构造（`PersistentAgentSpec.resolve_parts`）拒绝挂载 `ContextAssembleRail`、`PersonalContextRail`、`ContextProcessorRail`（含子类），`ContextProcessorRail` 的默认压缩链因此也不会挂载；`DeepAgentSpec` / `create_deep_agent` 不变。
- **增量通道**：保留原生 `PromptAttachmentManager.sync_to_context`（首次快照、之后只追加变化）；一期 Persistent 自身没有执行中追加的动态内容。
- **容量**：组装不做容量检查。超限时优先展示最新消息、标明省略范围、保留历史查询定位的选择逻辑，待共享容量计算的提供方和最终请求的检查位置对齐后实现（开发计划第 1.2 节）。

### 4.3 `SessionStateStore.save(context, ending) -> None`

待实现：保存整理后的白名单字段与结束信息为同一条记录，一次 set 写入；context 与中断状态不能分两次独立保存。调用成功只证明会话记录保存，不证明结束事件或候选输出完成；失败由执行入口抛运行异常且不记结束事件。

### 4.4 WindowStore 与历史检索（已确认待实现）

架构规定 load_state / save_state、archive / list_archives、search 的逻辑操作；它们尚不是已实现的公开函数签名。归档按 Session、execution_id 查询，本 Session 私有原文只能在授权范围内读取。检索先用关键词及索引扫描，结果须区分确定没有与没查完 / 失败，不以索引未命中断言历史不存在。摘要、索引和缓存只是派生视图，事实判断需回查可用原文。

## 5. 窗口与保存生命周期（已确认待实现）

### 5.1 执行中与安全换窗

普通模型回复、工具调用和结果沿原生路径追加。两次模型调用之间，窗口容量达到模型上下文容量的 70% 且工具消息成对完整时触发换窗，预留 30% 用于摘要。Agent 主动换窗请求属于后续扩展，本期不承诺 request_window_switch 工具。

顺序为：同步归档旧窗原文及关联 → 申请预算生成摘要 → 组装并替换内存新窗 → 产生 S_02 的 window.switched。归档失败不换窗；摘要失败保留已归档原文并按实际策略重试，不能承诺无限重试。新窗含旧窗摘要、目标 / 待办、未决写意图、归档引用、当前执行头和任务输入，以及完整成组的近期原文。最小必要内容仍放不下则 paused(context_unfit)，不静默截断。

新窗在执行中只存在内存，执行结束才保存当前记录；崩溃不会自动保存新窗口。归档、内存替换、普通窗口事件和会话记录分别确认，不提供全局事务、条件指针更新、租约围栏或“事件失败回滚窗口”的保证。

### 5.2 执行结束整理

| 内容 | 保存前处理 |
| --- | --- |
| 已保存历史 | 原样保留 |
| 本次执行头 | 保留并压缩为一行 |
| 临时背景 | 删除，下次 Execution 按当前版本重新注入 |
| 用户输入、确认结果及工作过程 | 保留；可重新取得的大结果可改为来源引用 |
| 执行结束标记 | 记录结局、产物及服务端确认已扫描到的群消息位置；最终答案超限时保存 pending_answer |

等待确认时，末尾待结果的工具调用一律不动；结束标记写入记录元信息，不追加到窗口尾部。临时背景仍可按标记删除。完成、取消、失败与可恢复暂停需保存；忽略群消息未写入窗口，无需保存；进程崩溃无法保存。停止能否取得完整现场须按实现验证，不据控制入口返回宣称已保存。

**中途结束后的安全窗口：** 停止、取消或执行中报错时，原生执行路径保留已取得的真实工具结果（Persistent 显式启用）；PA-10 保存时为缺失工具结果的调用幂等补充标为 `stopped_tool_placeholder` 的结果，说明“结果未取得，实际效果需核实”，具体结束原因由结束记录说明；已有真实结果及原生“已中断”结果不重复补位，正常等待确认不补位。加载结局为停止、取消或失败的旧记录时同样修补。“停止、取消或报错 → 保存 → 重新加载 → 下一任务”仍需完整联调（已确认待实现，见 [F_17](../features/F_17_session-state-record.md)）。

### 5.3 后端、清理与部分成功

服务端注入持久 BaseKVStore，保证命名空间隔离、工具不可访问和单条状态一次写入；沙箱、容器生命周期或 InMemoryCheckpointer 不能提供这些保证。状态保存成功而结束事件缺失时，后续结合记录结束标记、执行日志和归档查证，不能简单重跑。宿主按保留期清理归档，删除 Agent / Session 时清理对应前缀；不能把计算实例关闭视为删除持久记录。

## 6. 当前限制与替代关系

本规约不声明当前已具有换窗、存储、归档检索或恢复行为。未知存储格式的迁移、损坏校验和保留期细节由相应实现特性补齐后再写入有效契约，不沿用旧 epoch、head_revision 或 schema 错误名。

原 S_03 的 ContextStore、SessionContextHead、独立内容 revision、window.activated、每次模型调用重装配及即时知识刷新规则已被本规约替代；原 S_05 的 Checkpoint 保存责任合并到本规约的会话记录。旧方案在 Git / feature 历史追溯，不作为现行保证。群聊与知识模型转由 S_09 独立维护。

## 7. 关联规约与验证索引

- S_01 调用 load / assemble / save 并负责业务结局；S_05 规定正常确认保留现场、续做处理过期现场；S_02 定义窗口事件及结束事件的确认。
- S_09 提供获授权资料，S_04 提供当前文件读取；摘要、原文和工具实际结果分别承担不同事实职责。
- 当前接入协议、NoopCheckpointer 隔离与保存先后可定位 [test_entries.py](../../../../../tests/unit_tests/harness/persistent_agent/test_entries.py)。这些模拟检查不能证明真实 WindowStore 已实现。
- 实际存储、70% 换窗、归档检索、跨重启与安全后端的验证入口尚待 PA-10～PA-12 建立；PA-06 组装已有组件验证，容量验收未关闭，证据维护于唯一开发计划，不能把待执行验证写成已通过。
