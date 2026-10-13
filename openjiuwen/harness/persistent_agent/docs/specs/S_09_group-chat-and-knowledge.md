# S_09 群聊与授权知识端口规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | S_09 / group-chat-and-knowledge |
| 关联模块 | [ports.py](../../ports.py)、[schema/context.py](../../schema/context.py)、[GroupMessageReader 接入](../../execution/seams.py) |
| 最近一次修订日期 | 2026-10-13 |
| 关联 feature | [F_02](../features/F_02_execution-protocol.md)、[F_04](../features/F_04_contract-convergence.md)、[F_07](../features/F_07_group-participation.md)；实际读取接入见[开发计划](../designs/persistent-agent-development-plan.md) PA-13、PA-23 |
| 设计依据 | [架构设计：群聊与检索](../designs/persistent-agent-architecture.md#46-群聊记录与历史检索)、[办公空间与知识](../designs/persistent-agent-architecture.md#47-办公空间与知识)、[权限](../designs/persistent-agent-architecture.md#91-身份与权限检查) |

本规约从 S_03 分离，唯一维护 GroupChatPort、KnowledgePort、授权资料 DTO 与群消息读取接入，不维护窗口、文件读取或知识采集发布。

## 1. 范围、术语与适用条件

两个端口协议与 DTO 已定义；群参与判断已直接消费 GroupChatPort.recent_messages。供窗口组装的 GroupMessageReader、完整群历史工具、KnowledgePort 的读取 / 搜索适配仍待接入；端口声明不等于真实服务可用或已经执行授权过滤。

宿主根据 ExecutionContext 的 Session 定位群、鉴权并过滤消息，调用方不另传 group。PersonalContext 负责来源采集、发布和来源游标，本模块只读取获授权版本，不修改其内部文件或维护第二套采集 / 发布状态。通用行为引用 [Harness PersonalContext 规约](../../../docs/specs/S_17_personal-context.md)。

窗口背景和检索结果的使用时机由 S_03 定义；参与判断如何读取和重试由 S_01 定义；文件授权端口另由 S_04 定义。本文的知识读取权限不等于跨 Session 分析授权、知识发布权或 Agent 级学习作业能力。

## 2. 数据结构与事实归属

群与知识结果均继承 S_07 的 InvocationRef，消费边界重新 model_validate 后核对调用归属；不能把类型注解或既有实例当作已验证的宿主响应。

### 2.1 群消息与查询

GroupMessage 包含 message_id、sender_id、sender_name、sender_kind(human / agent)、received_at、可选 text、mention_ids、reply_to、attachment_refs、status(normal / edited / recalled / unavailable)、严格布尔 queued_request。时间须带时区并归一化 UTC；recalled 与 unavailable 不得带正文，正文最多 65536 字符，提及与附件各最多 128 项。稳定身份和来源引用不由模型猜造。

GroupMessageQuery 至少有 query / since / until / sender_id 中一个条件；同时提供起止时间时不得倒置。cursor 可选，limit 默认 50、严格正整数且最多 500。GroupMessagesResult 的 messages 最多 500 项，另含 next_cursor、scanned_through 和可选 reason。scanned_through 是服务端确认已扫描到的位置，与分页及展示范围分开；empty 也可携带，进度不可信时为空。

### 2.2 知识引用与结果

MaterialQuery 的 query 必须非空，可按 source_ref、scope_ref 和 cursor 限定，limit 默认 50、最多 500。MaterialSnapshot 包含 material_ref、source_ref、scope_ref、version_ref、content_ref、content_digest、policy_revision、completeness(complete / partial) 和可选 source_cursor；这是版本化来源引用，不是授权凭证。

MaterialReadResult 包含 status、items（最多 500）、next_cursor、query_complete（默认 false）、watermark 和 reason。完整性与水位配对：query_complete 为 true 当且仅当有非负 watermark。字段细节和 Schema 唯一维护于 context.py，不将其用于 WindowStore 会话记录或群消息的替代模型。

## 3. 不变量

1. **I-1 明确缺失：** incomplete、partial、unknown、unavailable 和 forbidden 不能映射为确定没有；非空结果也不能自动推导查询完整。
2. **I-2 来源绑定：** 返回引用须保留来源、版本、范围及完整性；引用和摘要不授予访问权，正文读取仍检查权限。
3. **I-3 背景非指令：** 排队群消息仅为背景，不能作为当前 Request 新输入；触发消息不重复注入背景。
4. **I-4 发布边界：** KnowledgePort 为只读消费协议，不更新 PersonalContext 发布指针、采集游标或全局配置，不混合不同可见范围的证据。
5. **I-5 生效范围：** 本期知识授权和版本在 Execution 开始解析，下一 Execution 刷新导航；不能据逐次正文读取鉴权宣称运行窗口及所有派生内容已立即失效。

## 4. 端口契约与错误语义

### 4.1 GroupChatPort

```text
recent_messages(execution, cursor=None, limit=20) -> GroupMessagesResult
search_messages(execution, query: GroupMessageQuery) -> GroupMessagesResult
```

方法均为 async，宿主按群范围、Agent 入群时间、消息撤回和成员权限过滤，不返回撤回正文。recent 的 limit 默认值是端口默认，参与判断使用的 8 条限制由 S_01 定义，不能混同。

| status | 必须满足 / 消费行为 |
| --- | --- |
| found | 至少一条消息；仍结合 next_cursor 判断是否需继续读取 |
| empty | 无消息且无 next_cursor，表示完整查询后确定没有 |
| incomplete | 必须有 reason，允许部分消息；不能当作空历史或据此选择忽略 |

宿主读取失败可抛异常；具体消费分支按 S_01 判断或后续 PA-23 查询工具的契约处理，不统一伪装 empty。私有工作历史与群公开历史分别检索，不能从相同 Agent 身份推导跨 Session 权限。

### 4.2 `GroupMessageReader.recent_messages(context, *, after=None, trigger_message_id=None)`

内部 async 接入返回 GroupMessagesResult，待 PA-23 实现：读取游标之后的新消息，并回补触发消息之后至本次读取上界的消息，合并去重且排除触发消息，供 S_03 组装临时背景。当前 getter 明确拒绝。S_01 的参与判断直接读取 GroupChatPort，不调用此接入点；不能因此把 PA-23 标为参与判断的硬前置。

排队回补示例：R2 由 M120 触发，R1 执行期间 M130 补充了 R2 的要求，R1 结束游标为 M180；R2 的背景须包含 M130。游标使用增量读取的可信 scanned_through，无可信位置时保留旧值；正文暂缺标注缺失，不卡住可信扫描位置，也不建立跨 Request 自动缺口追补。PA-07 旧展示把空正文都标为已撤回，unavailable 的展示适配仍由 PA-07 负责人补齐。

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

已确认待实现：Execution 开始将获授权的知识导航与近期群背景放入临时块；正文、搜索群消息和历史引用由模型按需经受控工具读取，结果追加到窗口末尾。Execution 结束时临时块删除，下次读取当前版本重新注入，实际窗口规则只在 S_03 维护。

本期撤权在下一 Execution 生效；确需立即刷新时须主动换窗并接受缓存失效，调用级全面失效传播属于后续扩展，不能写成已开放。正文重新读取仍鉴权；来源版本变化或引用不可读时不得以旧摘要猜造原文。来源、摘要、索引、缓存和派生结论的更细失效处理须由 PA-22 等实现及验证后局部补齐。

## 6. 当前限制与替代关系

当前只有 DTO、端口以及参与判断消费路径，不声明已具有知识授权后端、群聊搜索工具、跨 Session 检索或主动学习。没有材料不等于缺乏知识，网络故障或无权不能自动归类为知识缺口。

本文接管原 S_03 中群聊与 Material 系列模型、完整性语义和知识消费边界；废止“每次模型调用刷新版本并立即撤权全部派生内容”的旧承诺。会话状态 / 归档仍由 S_03 管理，窗口及外部资料不共享第二个活动指针。

## 7. 关联规约与验证索引

S_07 校验端口形状与共同归属；S_01 消费参与判断背景；S_03 消费 Execution 级资料与游标；S_04 单独维护文件读取，避免把材料引用和文件访问权混为一谈。

- 群消息、查询、材料状态与归属：[test_protocol.py](../../../../../tests/unit_tests/harness/persistent_agent/test_protocol.py)。
- 群背景排除触发消息、incomplete 不当空、判断预算与事件：[test_entries.py](../../../../../tests/unit_tests/harness/persistent_agent/test_entries.py)。
- 宿主真实过滤、完整 PA-23 读取与搜索、PA-13 授权知识及失效传播尚待建立相应验证入口，证据在关联 feature / 唯一开发计划维护。
