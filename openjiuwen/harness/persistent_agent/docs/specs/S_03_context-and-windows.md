# S_03 上下文、历史与活动窗口规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 状态 | PA-04 已按架构设计定义窗口存储后端注入位、群聊与知识端口及其数据结构；PA-06 已实现新 Request 的窗口组装，并在 Persistent 构造中拒绝挂载三个上下文 rail；会话状态记录、换窗和检索尚未实现 |
| 关联模块 | `openjiuwen/harness/persistent_agent/persistent_agent/{ports.py,schema/context.py,schema/integration.py,schema/spec.py,execution/seams.py,execution/window_assembly.py}` |
| 最近一次修订日期 | 2026-10-12 |
| 关联 feature | [F_02 执行协议](../features/F_02_execution-protocol.md)、[F_04 契约收敛](../features/F_04_contract-convergence.md)、[F_05 执行入口](../features/F_05_execution-entries.md)、[F_06 窗口组装](../features/F_06_window-assembly.md)；开发计划 PA-06、PA-10～PA-13、PA-04、PA-23 |
| 设计依据 | [Persistent Agent 详细架构设计](../designs/persistent-agent-architecture.md) §7；旧版详细架构 §9～11 仅供参考（见 Git 历史） |

PA-04 定义的端口契约如下，本身不提供存储、检索或授权判定：

- **窗口存储后端**：`PersistentPorts.window_backend` 必须是服务端注入的 `BaseKVStore` 实例，构造时只检查类型、不读写。会话状态记录与窗口归档的格式、序列化和检索由内部组件 `WindowStore` 在 PA-10、PA-11、PA-12 定义，架构设计不再有外部 ContextStore、活动指针条件更新或执行代次。
- **群聊记录**：`GroupChatPort.recent_messages / search_messages` 返回 `GroupMessagesResult`，结果分为 found / empty / incomplete，incomplete（没查完或查询失败）不能当作“没有”。`GroupMessage` 按架构设计 §7.9 携带稳定发送者身份、服务端接收时间、@、回复、附件引用、状态和是否属于排队中的请求；已撤回和正文暂缺（`unavailable`，消息存在但暂时读不到）的消息都不返回正文。`recent_messages` 按游标读取之后的消息，回补时以触发消息为游标读取；结果中的 `scanned_through` 是服务端确认已扫描到的位置，与分页用的 `next_cursor` 分开，进度不可信时为空。读取与检索的行为由 PA-23 实现。
- **知识**：`KnowledgePort.resolve / read` 继续使用 `MaterialQuery / MaterialSnapshot / MaterialReadResult`，`unknown`、`unavailable`、`forbidden`、`partial` 与具备完整查询水位的 `empty` 区分，由 PA-13 细化。

PA-05 定义的窗口共享契约经 PA-06 整理后集中在 `schema/integration.py`，`execution/seams.py` 提供接入点并兼容再导出原有契约名称，供 PA-06、PA-07、PA-10、PA-23 并行实现（详见 [F_05](../features/F_05_execution-entries.md) §7、[F_06](../features/F_06_window-assembly.md) 决策 8）：

- **加载结果**：会话状态记录加载后返回 `LoadedRecord`，包含等待现场和群消息游标。游标字段名为 `last_group_message_id`，含义是服务端确认已扫描到的消息位置，与正文是否读全、实际展示范围分开。
- **窗口组装**：新 Request 在加载之后由 `WindowAssembler.assemble(context, request, record)` 追加执行头、临时背景块和任务输入，返回原生输入和本次的群消息游标；确认恢复不组装。
- **窗口标记**：追加的执行头和临时背景块在消息元信息 `persistent_window_part` 中分别标为 `execution_header`、`temporary_background`，PA-10 结束整理时据此压缩执行头、删除临时背景块。PA-10 为停止时缺失结果的工具调用补位时，补位的工具结果标为 `stopped_tool_placeholder`，内容为“执行已停止，结果未取得，实际效果需核实”，不冒充真实结果，也不认定工具失败；正常等待确认的调用不补位。
- **结束信息**：`ExecutionEnding` 携带群消息游标和容量超限时未交接的最终答案，PA-10 写入执行结束标记和记录。
- **群消息读取**：`GroupMessageReader.recent_messages(context, after, trigger_message_id)` 由 PA-23 实现，供 PA-06 组装窗口背景：游标之后的增量与“触发消息之后至本次读取上界”的回补合并去重、排除触发消息；`scanned_through` 取增量读取确认的位置作为新游标，为空时保留旧游标。例：R2 由 M120 触发；R1 执行期间，群里在 M130 补充了 R2 的要求；R1 结束时游标为 M180。R2 执行时须读到 M130：它不在游标之后，也只出现在 R1 的临时背景里，已随结束整理删除。PA-07 不调用该接入点，判断时直接读取已注入的 `GroupChatPort.recent_messages`。
- **窗口安全**：PA-10 保存时保证窗口可以安全追加，加载已知停止的旧记录时幂等修补；PA-06 加载后仍发现未配对的工具调用时，以 `DEEPAGENT_CONTEXT_PARAM_ERROR` 校验拒绝并记 error 日志，不追加任何内容。
- **构造限制**：Persistent 构造拒绝三个上下文 rail，也拒绝显式启用 `max_context_message_num`、`default_window_message_num`、`default_window_round_num`（默认均为空，不截断）。

PA-06 已实现窗口组装（`execution/window_assembly.py`，接入点 `seams.window_assembler`），设计与取舍见 [F_06](../features/F_06_window-assembly.md)：

- **组成与位置**：加载会话状态记录之后，在 Session state `context` 中已保存窗口的末尾依次追加执行头、临时背景块（均为 `UserMessage`，带上述标记），任务输入作为原生输入 `{"query": RequestInput.text}` 由原生 ReAct 加在最后。系统提示词和工具定义不变；此后执行中只追加。
- **执行头**：一行，`【本次执行】类型：新任务；request_id：…；execution_id：…；来源：私聊|群消息|定时任务`，有触发消息时再加消息 ID、发送者（显示名、ID，群消息另注人或 Agent）、接收时间，有附件时加附件引用。续做（PA-18）沿用同一格式，类型为“续做”并加 `recovery_ref`；在此之前带 `recovery_ref` 的上下文不组装。
- **群 Session**：注入了 `GroupChatPort` 的 Session。只有群 Session 读取最近群消息：以加载的游标为 `after`、以触发消息为 `trigger_message_id` 读取（增量与触发位置回补合并去重、排除触发消息），结果须属于本次执行；组装器再排除触发消息并按接收时间排序。被忽略的消息照常列出，排队中的请求逐条标注且说明只作背景、不是指令，已撤回的不显示正文，正文暂缺的标注“正文暂缺，需要时可查询群聊记录”。`empty` 不加背景块；`incomplete` 仍加背景块并注明没有读取完整，不能当作“没有消息”。
- **游标**：取读取结果中服务端确认的扫描位置 `scanned_through`，与展示范围无关；结果不带该位置时以及私聊 Session 沿用加载时的游标。
- **未接入的来源**：群 Session 在群消息读取（PA-23）接入前、注入 `KnowledgePort` 的执行在知识导航（PA-13）接入前，于接入点明确拒绝（`DEEPAGENT_CONFIG_PARAM_ERROR`）。群消息 Request 例外：PA-07 先完成参与判断才取接入点，此时已有判断事件，会话状态记录（PA-10）未接入时同样如此；群里的定时任务和私聊仍在任何事件之前拒绝。
- **等待中的调用**：加载的记录带等待现场，或 Session state 带 `__react_agent_interruption__` 时，新 Request 不组装，校验拒绝（`DEEPAGENT_CONTEXT_PARAM_ERROR`），窗口不追加；窗口有未配对的工具调用时同样拒绝，并记 error 日志（见上文“窗口安全”）。
- **上下文 rail**：Persistent 构造（`PersistentAgentSpec.resolve_parts`）拒绝挂载 `ContextAssembleRail`、`PersonalContextRail`、`ContextProcessorRail`（含子类），`ContextProcessorRail` 的默认压缩链因此也不会挂载；`DeepAgentSpec` / `create_deep_agent` 不变。
- **增量通道**：保留原生 `PromptAttachmentManager.sync_to_context`（首次快照、之后只追加变化）；一期 Persistent 自身没有执行中追加的动态内容。
- **容量**：组装不做容量检查。超限时优先展示最新消息、标明省略范围、保留历史查询定位的选择逻辑，待共享容量计算的提供方和最终请求的检查位置对齐后实现（开发计划第 3.4 节）。

以下章节仍按旧版架构编写，描述每次模型调用前的装配、活动指针和 Checkpoint 等已被架构设计取代的目标行为；与架构设计冲突时以架构设计 §7（按 Execution 生命周期管理上下文、会话状态记录、换窗与归档）为准，随 PA-10～PA-13 修订。PA-06 已按上文改为每个 Execution 开始时组装一次，并据此修订了下面的复用边界表。

## 范围 / 边界

本规约定义每次模型调用的输入装配、授权与版本核验、私有历史检索、窗口内容保存及活动窗口切换。群完整历史归宿主 GroupChatStore，已提交执行事实归 SessionStore，PersonalContext 来源采集与发布归其自身模块。本模块只消费获授权版本，不复制这些系统的权威记录。

## 现有实现的复用边界

| 现有实现 | 复用方式 | 仍需适配 |
| --- | --- | --- |
| Harness [Prompt 及 Rail 契约](../../../docs/specs/S_06_prompts-and-l10n.md) | 系统提示词和工具定义沿用原生构建，一次 Execution 内不变；`PromptAttachmentManager` 的增量通道保留原生行为（PA-06） | 容量检查待补设计 |
| [`ContextAssembleRail`、`ContextProcessorRail`](../../../rails/context_engineer/)、[`PersonalContextRail`](../../../rails/personal_context.py) | 不复用：Persistent 构造拒绝挂载（PA-06），默认压缩链随之不挂载 | 知识导航改由 PA-13 经 `KnowledgePort` 在 Execution 开始时放入临时背景块 |
| [`PersonalContext`](../../../personal_context/personal_context.py) | 复用来源采集、查询和内容组织基础 | 本模块只读取获授权发布视图，不复制采集游标或直接改写来源文件 |
| [`DeepAgent.save_state/load_state`](../../../deep_agent.py) | 保存其当前 Session 的内部运行状态 | Window 原文、索引、唯一活动指针和换窗条件更新属于 ContextStore 新契约，不重命名内部状态为 Window |

Persistent 不挂载 `ContextProcessorRail` 的压缩链；`context_engine_config` 中显式设置 `max_context_message_num`、`default_window_message_num` 或 `default_window_round_num` 时构造拒绝（见 F_06）。不能在检查前后静默丢弃必要事实或工具消息组。初期无法接管最终模型请求时，不声明支持可靠换窗。

## 不变量

1. 当前活动窗口及其内容版本以 ContextStore 的 `SessionContextHead` 为准；Checkpoint 只保存精确引用，不充当第二个活动指针。
2. 同一窗口可以保存多个不可变内容版本；普通保存推进 `window_revision` 和 `head_revision`，不改变 `window_id`。换窗才创建新的 `window_id`。
3. Window 切换不推进 Request 的业务处理位置；`window.activated` 事件提交失败不撤销已生效的新窗口。
4. 摘要与索引只提供导航，事实判断和工具操作需回查当前获授权原文。索引未命中不能断言历史不存在。
5. 撤权覆盖原文、摘要、索引、缓存及派生结论。群背景中的排队新消息不是当前 Request 的新指令。
6. 完整模型请求在所有运行时附件、工具描述和结果注入后检查容量；不能静默裁剪必要事实、孤立工具结果或无限换窗。

## 接口契约与错误语义

| 模型阶段 | 必要输入 |
| --- | --- |
| 判断 | 固定职责、当前输入与来源、少量近期公开讨论、必要本 Session 状态；只读 |
| 执行 | 当前目标、约束和待办，加相关历史证据、工作空间与知识导航、完整工具消息组 |
| 恢复 | 有效执行内容，加已提交 Checkpoint、事件尾部核对结果、必要文件当前状态与归档指针 |

每次模型调用前依次检查执行权与控制、当前授权和资料版本，排除失效片段，按任务选择必要资料，固定层次装配，注入运行时附件一次，检查完整请求大小，再申请模型预算准入。`ContextSnapshot` 拟记录阶段、窗口精确版本、业务事件边界、资料游标、必要工作文件路径及当时内容状态、各知识发布根版本和授权修订。跨存储不要求全局事务快照，但每份引用须自洽、可解析且仍获授权；必要资料在装配中变化时重新装配，持续冲突则暂停。

上下文容量门禁使用对应模型计量器或保守上界，包含协议开销与输出预留。正式执行或恢复的完整输入达到上限时请求换窗；最小必要输入仍超限时返回逻辑错误 `CONTEXT_UNFIT` 并暂停。判断阶段不为过大输入新建正式窗口，应改用有界读取。模型服务再次报告超限时可在安全点有限次数修正容量估计，不能无限重试或无声截断。

历史检索经受控工具按当前 Session、授权范围、来源、版本和完整性分页返回。公开群资料经群与资源权限另行校验。分页未完与空结果不同，`partial / unavailable / forbidden` 不能映射为“无历史”。PersonalContext 导航在同一次装配固定发布版本；读取正文时验证当前授权，知识更新在下一次调用刷新，撤权立即阻止继续使用旧正文及其派生内容。

## 状态及数据生命周期

| 逻辑记录 | 关键字段与用途 |
| --- | --- |
| `SessionContextHead` | `active_window_id, active_window_revision, head_revision, authority_epoch`；唯一活动指针 |
| `WindowRecord` | `window_id, window_revision, predecessor_ref, content_ref, content_hash, source_refs, event_refs`；不可变内容版本 |
| `MaterialSnapshot` | 来源、修订、游标、完整性、权限范围和正文引用 |
| `ArchiveManifest` | 旧窗正文、索引、覆盖范围、校验和与稳定 `switch_id` |
| `EvidenceIndex` | Session、维度、键、来源指针和版本；命中后再鉴权 |

判断为 `ignore` 时只提交私有决策证据，不创建正式工作窗口。首次正式执行且 head 为空时，以“预期为空”条件创建首窗；冲突时重新读取，不覆盖已有窗口。已确认步骤、请求收尾、等待或暂停安全点应保存当前 Window 新内容版本：先保存可读的不可变正文及引用，再以预期修订和有效执行权 `advance_head`。响应丢失时用稳定写入标识查询。Checkpoint 引用精确 `(window_id, window_revision)`，但 head 更新与 Checkpoint 提交是两个独立确认点。

已有工作窗口只由 Agent 主动请求或完整输入容量不足触发换窗。ContextManager 在安全边界暂停新模型和工具派发，收拢成组消息与回执，保存旧窗原文及必要索引，准备可读的新窗内容，再以 `expected_head_revision + epoch + switch_id` 条件执行 `create_and_activate_window`。新窗保留剩余目标、约束、已确认结论、待办、未决操作与旧窗归档指针。写入边界须原子核验有效租约和代次；预先读一次执行权不构成围栏。

旧窗归档失败时不得激活新窗。激活结果未知时先查询当前 head：已生效则沿新窗继续，尚未生效才按原 `switch_id` 核对或重试。新窗一旦生效，后续事件、Checkpoint 或执行失败均不回滚活动指针；恢复时逐项核对其内容与已提交业务事实。有效 Checkpoint、活动窗口和未决操作所引用对象在清理前受保护；未知快照主版本应报 `SCHEMA_UNSUPPORTED`，不能盲目反序列化执行。

## 与其它 spec 的关系

- [S_01](S_01_public-api-and-execution.md) 定义判断、执行和恢复阶段。
- [S_05](S_05_checkpoint-and-recovery.md) 定义如何用 Window、Checkpoint 和事件尾部分别重建上下文与进度。
- PersonalContext 的已有公共行为见 [Harness PersonalContext 规约](../../../docs/specs/S_17_personal-context.md)。
