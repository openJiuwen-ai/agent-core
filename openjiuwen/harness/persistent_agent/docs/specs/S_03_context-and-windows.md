# S_03 上下文、历史与活动窗口规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 状态 | PA-04 已按 v2 定义窗口存储后端注入位、群聊与知识端口及其数据结构；窗口管理、会话状态记录、换窗和检索尚未实现 |
| 关联模块 | `openjiuwen/harness/persistent_agent/persistent_agent/{ports.py,schema/context.py}` |
| 最近一次修订日期 | 2026-10-08 |
| 关联 feature | [F_02 执行协议](../features/F_02_execution-protocol.md)、[F_04 按 v2 收敛契约](../features/F_04_v2-contract-convergence.md)；开发计划 PA-06、PA-10～PA-13、PA-04、PA-23 |
| 设计依据 | [Persistent Agent 设计（v2）](../designs/persistent-agent-architecture.md) §7；旧版详细架构 §9～11 仅供参考（见 Git 历史） |

当前已实现的只有契约，不提供存储、检索、授权判定或运行行为：

- **窗口存储后端**：`PersistentPorts.window_backend` 必须是服务端注入的 `BaseKVStore` 实例，构造时只检查类型、不读写。会话状态记录与窗口归档的格式、序列化和检索由内部组件 `WindowStore` 在 PA-10、PA-11、PA-12 定义，v2 不再有外部 ContextStore、活动指针条件更新或执行代次。
- **群聊记录**：`GroupChatPort.recent_messages / search_messages` 返回 `GroupMessagesResult`，结果分为 found / empty / incomplete，incomplete（没查完或查询失败）不能当作“没有”。`GroupMessage` 按 v2 §7.9 携带稳定发送者身份、服务端接收时间、@、回复、附件引用、状态和是否属于排队中的请求；已撤回的消息不返回正文。读取与检索的行为由 PA-23 实现。
- **知识**：`KnowledgePort.resolve / read` 继续使用 `MaterialQuery / MaterialSnapshot / MaterialReadResult`，`unknown`、`unavailable`、`forbidden`、`partial` 与具备完整查询水位的 `empty` 区分，由 PA-13 细化。

以下章节仍按旧版架构编写，描述每次模型调用前的装配、活动指针和 Checkpoint 等已被 v2 取代的目标行为；与 v2 冲突时以 v2 §7（按 Execution 生命周期管理上下文、会话状态记录、换窗与归档）为准，随 PA-06、PA-10～PA-13 修订。

## 范围 / 边界

本规约定义每次模型调用的输入装配、授权与版本核验、私有历史检索、窗口内容保存及活动窗口切换。群完整历史归宿主 GroupChatStore，已提交执行事实归 SessionStore，PersonalContext 来源采集与发布归其自身模块。本模块只消费获授权版本，不复制这些系统的权威记录。

## 现有实现的复用边界

| 现有实现 | 复用方式 | 仍需适配 |
| --- | --- | --- |
| Harness [Prompt 及 Rail 契约](../../../docs/specs/S_06_prompts-and-l10n.md) | 沿用已有 Prompt 构建和模型调用前 Rail 事件，不另建平行注入通道 | 在最终请求成形、附件注入后检查总容量；验证 Rail 事件路由和优先级 |
| [`PersonalContextRail`](../../../rails/personal_context.py) | 复用 `before_model_call` 的临时附件注入以及 `after_model_call/after_invoke` 清理 | 现有 Rail 只读取本地开关与 `description.md`；多主体授权、不可变发布版本和失效传播须经 `KnowledgePort` 适配验证，不能仅凭该 Rail 宣称满足 |
| [`PersonalContext`](../../../personal_context/personal_context.py) | 复用来源采集、查询和内容组织基础 | 本模块只读取获授权发布视图，不复制采集游标或直接改写来源文件 |
| [`DeepAgent.save_state/load_state`](../../../deep_agent.py) | 保存其当前 Session 的内部运行状态 | Window 原文、索引、唯一活动指针和换窗条件更新属于 ContextStore 新契约，不重命名内部状态为 Window |

已有 Harness 上下文压缩或截断能力若启用，须与本规约的完整容量检查和证据归档协调；不能在检查前后静默丢弃必要事实或工具消息组。初期无法接管最终模型请求时，不声明支持可靠换窗。

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
