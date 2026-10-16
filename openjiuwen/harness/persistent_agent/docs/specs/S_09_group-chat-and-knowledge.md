# S_09 群聊与授权知识端口规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | S_09 / group-chat-and-knowledge |
| 关联模块 | [ports.py](../../ports.py)、[schema/context.py](../../schema/context.py)、[GroupMessageReader 接入](../../execution/seams.py) |
| 最近一次修订日期 | 2026-10-16 |
| 关联 feature | [F_02](../features/F_02_execution-protocol.md)、[F_04](../features/F_04_contract-convergence.md)、[F_07](../features/F_07_group-participation.md)、[F_23](../features/F_23_group-chat-history-retrieval.md)；实际读取接入见[开发计划](../designs/persistent-agent-development-plan.md) PA-13、PA-23 |
| 设计依据 | PA-23 群聊历史检索以 [F_23](../features/F_23_group-chat-history-retrieval.md) 为依据；[办公空间与知识](../designs/persistent-agent-architecture.md#47-办公空间与知识)、[权限](../designs/persistent-agent-architecture.md#91-身份与权限检查) |

本规约从 S_03 分离，唯一维护 GroupChatPort、KnowledgePort、授权资料 DTO 与群消息读取接入，不维护窗口、文件读取或知识采集发布。

## 1. 范围、术语与适用条件

两个端口协议及 DTO 已定义；群参与判断独立消费 `GroupChatPort.recent_messages`。PA-23 的跨群检索 DTO、Persistent 专用工具和当前群背景读取器已实现，JiuwenSwarm 的真实授权与历史存储 Provider 仍待适配。端口声明和组件测试不等于真实服务可用或已经执行授权过滤。

`recent_messages` 按 `ExecutionContext` 的 Session 定位当前群；`search_messages` 按可信 `ExecutionContext.agent_id` 确定 Agent，并在该 Agent 当前获授权的群范围内检索。调用方不得传入 Agent 身份或授权列表。跨群搜索不要求当前提问者属于目标群；群授权及历史存储由 JiuwenSwarm 宿主提供。PersonalContext 负责来源采集、发布和来源游标，本模块只读取获授权版本，不修改其内部文件或维护第二套采集 / 发布状态。通用行为引用 [Harness PersonalContext 规约](../../../docs/specs/S_17_personal-context.md)。

当前群窗口背景和检索结果的使用时机由 S_03 定义；参与判断如何读取和重试由 S_01 定义；文件授权端口另由 S_04 定义。群历史搜索的跨群范围仅由本规约的 Agent—群授权规则确定；它不开放私有 Session 工作历史检索、跨 Agent 检索、知识发布权或 Agent 级学习作业能力。

## 2. 数据结构与事实归属

群与知识结果均继承 S_07 的 InvocationRef，消费边界重新 model_validate 后核对调用归属；不能把类型注解或既有实例当作已验证的宿主响应。

### 2.1 群消息与查询

GroupMessage 包含 message_id、sender_id、sender_name、sender_kind(human / agent)、received_at、可选 text、mention_ids、reply_to、attachment_refs、status(normal / edited / recalled / unavailable)、严格布尔 queued_request。时间须带时区并归一化 UTC；recalled 与 unavailable 不得带正文，正文最多 65536 字符，提及与附件各最多 128 项。稳定身份和来源引用不由模型猜造。

`GroupMessageQuery` 至少有 `query` / `since` / `until` / `sender_id` 中一个条件；同时提供起止时间时不得倒置。`cursor` 可选，`limit` 默认 50、严格正整数且最多 500。查询还可选提供 `group_ids` / `group_names` 以缩小跨群搜索范围；两者均按精确值解析，只能匹配 Agent 已获授权的群，不能扩大授权。多个同名群时不随机选择，须返回消歧结果。查询关键词按字面子串匹配，大小写不敏感，不进行语义扩展；结果按服务端接收时间从新到旧排序，同一时间按 `group_id`、`message_id` 稳定排序。

`GroupMessagesResult` 专用于近期消息读取，`messages` 最多 500 项，另含 `next_cursor`、`scanned_through` 和可选 `reason`。`scanned_through` 是服务端确认已扫描到的位置，与分页及展示范围分开；`empty` 也可携带，进度不可信时为空。

跨群搜索命中使用 `GroupMessageSearchHit`，包含稳定的 `group_id`、`group_name`、`message_ref` 及匹配的 `GroupMessage`。`GroupMessageSearchResult` 专用于历史搜索，包含 `status`、有界 `hits`、`next_cursor`、`query_complete` 和可选 `reason`；不得用 `scanned_through` 表示多群搜索的完整性。`found` / `empty` 要求 `query_complete=true` 且没有后续页；`incomplete` 要求 `query_complete=false` 并说明原因，可以包含部分命中及可继续的 `next_cursor`。结果状态及其合法组合见 4.1。

### 2.2 知识引用与结果

MaterialQuery 的 query 必须非空，可按 source_ref、scope_ref 和 cursor 限定，limit 默认 50、最多 500。MaterialSnapshot 包含 material_ref、source_ref、scope_ref、version_ref、content_ref、content_digest、policy_revision、completeness(complete / partial) 和可选 source_cursor；这是版本化来源引用，不是授权凭证。

MaterialReadResult 包含 status、items（最多 500）、next_cursor、query_complete（默认 false）、watermark 和 reason。完整性与水位配对：query_complete 为 true 当且仅当有非负 watermark。字段细节和 Schema 唯一维护于 context.py，不将其用于 WindowStore 会话记录或群消息的替代模型。

## 3. 不变量

1. **I-1 明确缺失：** incomplete、partial、unknown、unavailable 和 forbidden 不能映射为确定没有；非空结果也不能自动推导查询完整。
2. **I-2 来源绑定：** 返回引用须保留来源、版本、范围及完整性；引用和摘要不授予访问权，正文读取仍检查权限。
3. **I-3 背景非指令：** 排队群消息仅为背景，不能作为当前 Request 新输入；触发消息不重复注入背景。
4. **I-4 发布边界：** KnowledgePort 为只读消费协议，不更新 PersonalContext 发布指针、采集游标或全局配置，不混合不同可见范围的证据。
5. **I-5 生效范围：** 本期知识授权和版本在 Execution 开始解析，下一 Execution 刷新导航；不能据逐次正文读取鉴权宣称运行窗口及所有派生内容已立即失效。
6. **I-6 群授权不随请求者扩张：** 跨群搜索只校验当前 Agent 对目标群的授权，不额外要求当前提问者属于目标群；模型提供的群 ID / 群名只能缩小 Agent 已授权的范围。授权源不可验证时失败关闭。
7. **I-7 不自动扩散群历史：** 跨群历史只由 Persistent Agent 显式调用搜索工具读取；私聊和群聊 Execution 均不因注入 `GroupChatPort` 自动读取或注入其他群的历史。

## 4. 端口契约与错误语义

### 4.1 GroupChatPort

```text
recent_messages(execution, cursor=None, limit=20) -> GroupMessagesResult
search_messages(execution, query: GroupMessageQuery) -> GroupMessageSearchResult
```

方法均为 async。`recent_messages` 按 Session 定位当前群，宿主依据该群访问策略、Agent 入群时间和消息状态过滤，不返回撤回正文。recent 的 limit 默认值是端口默认，参与判断使用的 8 条限制由 S_01 定义，不能混同。

`search_messages` 是 Persistent Agent 显式调用的历史检索端口。宿主应为启用 PA-23 搜索的私聊及群聊 Persistent Execution 注入 `GroupChatPort`；这不会触发近期消息自动读取。JiuwenSwarm 从可信 Execution 解析 Agent，并在每次调用时读取当前 Agent—群授权关系，再查询宿主持久化的群消息记录 / 检索索引。无群过滤时搜索该 Agent 当前全部获授权群；指定群过滤只缩小搜索范围。当前提问者是否属于目标群不参与授权判断。授权源、宿主存储或检索完整性不可确认时不得返回确定的空结果。

| status | 必须满足 / 消费行为 |
| --- | --- |
| found | 至少一条消息；仍结合 next_cursor 判断是否需继续读取 |
| empty | 无消息且无 next_cursor，表示完整查询后确定没有 |
| incomplete | 必须有 reason，允许部分消息；不能当作空历史或据此选择忽略 |

上述状态表适用于 `recent_messages` 返回的 `GroupMessagesResult`。宿主读取失败可抛异常，不能伪装为 `empty`。

`search_messages` 返回的 `GroupMessageSearchResult` 状态如下：

| status | 必须满足 / 消费行为 |
| --- | --- |
| found | 至少一个命中，`query_complete=true` 且无 `next_cursor` |
| empty | 无命中、`query_complete=true` 且无 `next_cursor`，表示授权范围和查询条件均已完整扫描 |
| incomplete | `query_complete=false`；允许返回部分命中，必须有 `reason`，可继续时提供 `next_cursor`；不能作为确定无结果 |
| forbidden / unavailable | `query_complete=false`、无命中且无 `next_cursor`；对显式目标使用不泄露目标是否存在的通用语义，必须有 `reason` |
| failed | `query_complete=false`、无命中且无 `next_cursor`；授权解析或检索失败，必须有 `reason`，不能伪装为 `empty` |

搜索参数不含 `agent_id`、`session_id`、提问者身份或授权列表。缺省群范围由宿主按当前 Execution 的 Agent 解析。每个分页请求重新校验授权；撤权后旧游标不能继续返回该群内容。JiuwenSwarm 查询其持久化群消息记录 / 检索索引，必须返回稳定来源群及消息引用，并准确表达实际保存覆盖范围。底层存储产品和物理路径属于宿主实现，不由本规约限定。

### 4.2 `GroupMessageReader.recent_messages(context, *, after=None, trigger_message_id=None)`

内部 async 接入已由 `ExecutionGroupMessageReader` 实现，返回 GroupMessagesResult：读取游标之后的新消息，并回补触发消息之后至本次读取上界的消息，合并去重且排除触发消息，供 S_03 组装临时背景。只在 `request.kind == group_message` 时调用；私聊和定时任务注入端口也不会自动读取群背景。S_01 的参与判断仍直接读取 GroupChatPort，不调用此接入点。

每次近期读取最多 500 条，触发补读最多 32 页，合并最多保留最近 500 条。带 cursor 的宿主分页按正向扫描顺序返回。首轮增量读取的 `scanned_through` 固定本次补读上界；补读读到该消息或得到相同扫描位置才确认到达上界，不把后来扫描到的更新位置当作本次上界。没有可信上界、不支持定位、游标循环、补读页数超限或无法到达上界时标记 `incomplete`，不合并未经证明的补读区间；合并条数超限也标记不完整。重复消息以后一次观察为准（包括撤回状态）。初次读取有后续页时同样标记不完整，不把分页当作完整背景。读取异常向上传播，不能返回确定为空。

搜索参数的群 ID / 群名各最多 128 项，群名为非空白精确值、最多 512 字符；查询默认 limit=50、上限 500，无时间条件即搜索实际保留历史。工具重新校验结果与 Execution 归属，并拒绝超过请求 limit 的返回。模型展示每条正文最多 2048 字符，整个 JSON 最多 32768 字符；`text_truncated`、`display_truncated` 和 `omitted_hits` 明示摘录和省略。`query_complete` 只表示宿主查询完整性，不表示展示完整；需要完整展示时缩小查询范围或 limit，不伪造补页游标。宿主异常和不合法结果统一安全返回 `failed`，取消向上传播；每次分页均重新调用宿主，不缓存授权。

补读未到达固定上界时，只保留本次增量已选中消息的新观察，不加入其他未经证明的区间消息；因此补读已经观察到的撤回 / 正文不可用不会因后续分页中断退回旧正文。

模型展示的提及和附件引用各最多 8 项；单条序列化结果超过 24000 字符时省略这些可选列表，保留来源身份与正文摘录。`metadata_truncated` 和 `omitted_metadata` 标明引用省略，过长原因摘录最多 1024 字符并带 `reason_truncated`。展示限制按整个 JSON 计算，包括转义开销；单条合法命中不会因可选元数据过大而整条消失。

排队回补示例：R2 由 M120 触发，R1 执行期间 M130 补充了 R2 的要求，R1 结束游标为 M180；R2 的背景须包含 M130。游标使用增量读取的可信 scanned_through，无可信位置时保留旧值；正文暂缺标注缺失，不卡住可信扫描位置，也不建立跨 Request 自动缺口追补。参与判断读取的最近消息同样区分已撤回与正文暂缺，展示规则见 S_01。

### 4.3 KnowledgePort

```text
resolve(execution, query: MaterialQuery) -> MaterialReadResult
read(execution, material_ref, version_ref) -> MaterialReadResult
```

方法均为 async，解析授权视图或读取精确版本正文引用，不能隐式读取“最新”替代传入版本。真正授权与存储实现由对端提供，适配待 PA-13 接入；同一次装配固定知识发布版本，正文读取仍检查实际权限。

| status | DTO 与消费边界 |
| --- | --- |
| found | 至少一个来源项，不自动表示完整查询 |
| empty | 无 items、query_complete=true 且有 watermark；其他分页信息按查询语义核对，不仅靠 items 为空判断 |
| partial | 有 next_cursor 或至少一个 partial 来源，不能当作完整证据 |
| forbidden / unavailable / unknown | 无 items 且必须有 reason，分别表示无权、不可用和未确定；宿主失败不填假成功 |

同进程端口失败直接抛异常；status=unknown 是资料查询不确定，不引入预算 / 记账调用的跨进程“结果未知”协议。对外错误展示仍需脱敏，不能凭错误差异向无权主体暴露资源存在性。

## 5. 使用与版本生命周期

当前群消息 Execution 开始时将近期背景放入临时块；知识导航仍待 PA-13。群历史搜索由 Persistent Agent 显式调用 `search_group_messages`，按需读取该 Agent 获授权的一个或多个群记录，不自动注入其他群历史。工具结果按正常工具返回进入当前执行上下文，随既有工具消息保存规则保留，而不是临时背景。Execution 结束时临时块删除，下次读取当前版本重新注入，实际窗口规则只在 S_03 维护。

每次新的群消息读取或搜索（含分页）均由宿主重新鉴权；已进入窗口的材料及派生结论不因此自动擦除，调用级全面失效传播属于后续扩展，不能写成已开放。正文重新读取仍鉴权；来源版本变化或引用不可读时不得以旧摘要猜造原文。来源、摘要、索引、缓存和派生结论的更细失效处理须由 PA-22 等实现及验证后局部补齐。

## 6. 当前限制与替代关系

PA-23 的 agent-core 端口、工具及窗口读取已实现；不声明已具有知识授权后端或真实群聊搜索服务，JiuwenSwarm Provider 和跨仓验证仍待完成。本规约不开放私有 Session 工作历史检索、跨 Agent 搜索或主动学习。没有材料不等于缺乏知识，网络故障或无权不能自动归类为知识缺口。

本文接管原 S_03 中群聊与 Material 系列模型、完整性语义和知识消费边界；废止“每次模型调用刷新版本并立即撤权全部派生内容”的旧承诺。会话状态 / 归档仍由 S_03 管理，窗口及外部资料不共享第二个活动指针。

## 7. 关联规约与验证索引

S_07 校验端口形状与共同归属；S_01 消费参与判断背景；S_03 消费 Execution 级资料与游标；S_04 单独维护文件读取，避免把材料引用和文件访问权混为一谈。PA-23 的实现与验收要求见 [F_23](../features/F_23_group-chat-history-retrieval.md)；架构设计 §3.5、§4.6、§9.1 已按本规约同步，宿主 Provider 仍待适配。

- 群消息、查询、材料状态与归属：[test_protocol.py](../../../../../tests/unit_tests/harness/persistent_agent/test_protocol.py)。
- 群背景排除触发消息、incomplete 不当空、判断预算与事件：[test_entries.py](../../../../../tests/unit_tests/harness/persistent_agent/test_entries.py)。
- PA-23 组件验证：`tests/unit_tests/harness/persistent_agent/test_group_history.py`、`test_construction.py`、`test_window_assembly.py`。当前授权过滤与真实群历史检索须由 JiuwenSwarm 联调验证；PA-13 授权知识与失效传播仍待建立验证入口，证据在关联 feature / 唯一开发计划维护。
