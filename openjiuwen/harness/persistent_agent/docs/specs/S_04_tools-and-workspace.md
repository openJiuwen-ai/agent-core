# S_04 工具副作用与办公空间规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 状态 | 拟议契约；尚未实现 |
| 关联模块 | `openjiuwen/harness/persistent_agent/{workspace/,tools/,execution/loop_adapter.py}`（拟议路径） |
| 最近一次修订日期 | 2026-10-08 |
| 关联 feature | N/A；开发计划 PA-14～PA-16 |
| 设计依据 | 旧版详细架构 §12、§14.2（见 Git 历史）；当前设计为 [Persistent Agent 详细架构设计](../designs/persistent-agent-architecture.md) |

当前没有本模块的工具适配或办公空间实现；本规约不声称已有工具具备下述可靠恢复能力。

## 范围 / 边界

本规约定义 Persistent Agent 对 Harness 工具、办公空间文件、历史与知识读取工具的授权边界，以及产生外部效果的工具操作协议。工具通用注册和执行安全仍由 [Harness 工具规约](../../../docs/specs/S_05_tools-contract.md)、[安全规约](../../../docs/specs/S_08_security-engine.md) 和 [工作空间规约](../../../docs/specs/S_09_workspace.md) 负责。

## 现有实现的复用边界

| 现有实现 | 直接复用 | 尚未覆盖的要求 |
| --- | --- | --- |
| [`Workspace`](../../../workspace/workspace.py) 与 [`ReadFileTool/WriteFileTool/EditFileTool`](../../../tools/filesystem.py) | 工作目录结构及文件读写；`EditFileTool` 已有读后编辑检查和进程内路径锁 | 每调用授权交集、跨进程执行权围栏、稳定 `operation_id` 与回执核对。进程内锁不能当作跨实例事务 |
| [`PermissionEngine`](../../../security/permission_engine/core.py)、[`PermissionInterruptRail`](../../../rails/security/tool_security_rail.py) 和 FileGuard | 沿用工具审批、路径及命令安全检查 | 宿主当前主体/Session/资源授权和租约写入边界；不复制一套通用权限引擎 |
| DeepAgent 现有 [子代理运行时](../../../subagent_runtime/) | 子代理创建、运行和并行调度 | 父 Request 的业务事件汇聚、预算、外部副作用确认及恢复核对；不新建子代理运行时 |

已有文件工具的本地历史或读状态记录不是 SessionStore 权威操作日志，也不能证明跨进程写入恰好一次。优先以适配器围绕现有工具增加意图确认和结果核对；若某工具不能满足围栏或恢复协议，只禁用该工具的自动恢复/写能力，不另写一套文件工具替代所有已有实现。

## 不变量

1. 有效权限为配置范围、Request 启动上限、当前主体、Session 或群范围及资源当前授权的交集；文件路径、同 Agent 身份或同项目根均不授予其他 Session 的私有历史访问权。
2. Agent 可在获授权办公空间内直接读写文件。文件存在和写入成功不等于 Request 已完成或文件已公开到群。
3. 具有副作用的逻辑操作使用稳定 `operation_id`，与模型工具调用 ID 分离。意图先获宿主确认，外部调用后回执再获确认，才能推进依赖步骤。
4. 工具超时仅表示结果未知；不能以超时、进程退出或旧执行权失效推断“未执行”，也不能盲目生成新操作标识重做。
5. Shell、CLI、MCP 和子进程继续受本调用主体、Session 与资源范围约束；入口路径校验不能代替子进程内隔离。保存脚本或 SOP 不自动安装 Skill 或扩大执行权限。
6. DeepAgent 子代理仍是父 Request/Execution 内的工作单元。其事件、预算、权限和写入回执经父执行汇聚，不创建第二个业务 Session 或绕开宿主队列。

## 接口契约与错误语义

| 拟议工具类别 | 读写契约 |
| --- | --- |
| `search_session_history / read_evidence` | 返回本 Session 有界原文、来源、版本、完整性与分页游标 |
| `query_group_materials` | 按获授权群查询公开消息、成员、公告和文档导航 |
| Harness 文件工具 | 当前办公空间相对路径的读、创建、修改、列举；写入返回可核对状态 |
| `read_context_knowledge` | 同一发布版本的受控正文与来源，读取时重新鉴权 |
| `request_window_switch` | 只登记换窗请求，实际指针更新由 ContextManager 完成 |

读取结果至少区分 `status, content_or_ref, source_refs, version, completeness, next_cursor`；写入另含 `operation_id, receipt`。`forbidden`、`not_found`、`partial`、`version_conflict`、`unavailable` 语义不同，但面向无权主体不能用错误差异泄露资源存在性。工具应声明只读、外部幂等键、查询、取消及补偿能力，恢复依据声明能力与实际回执，不根据工具名猜测。

拟议副作用序列：

```text
prepare(operation_id, target, args_hash, controlled_args_ref)
  → tool.intent 提交确认
  → 检查执行权、当前授权、取消与审批
  → 使用原幂等键调用外部系统
  → tool.completed 携带真实回执和结果引用
  → 提交确认后推进依赖步骤
```

意图还须保存工具与协议版本、目标身份、可恢复参数受控引用、审批或授权关联以及查询/重试能力声明。参数摘要用于冲突检测，不能从摘要猜测参数；明文凭证不进入恢复正文，执行时按当前授权重新取得。目标或关键参数变化须创建新操作。无法安全保留重试参数或查询回执时，结果未知只能人工核对。

## 状态及数据生命周期

办公空间目录可按部署初始化为 `inbox/`、`work/`、`sessions/<session_id>/`、`knowledge/`、`templates/`、`practices/`，目录名称不是授权机制。跨 Session 可在当前权限下共享工作文件；会话草稿、未完成待办、Checkpoint 和私有轨迹仍在会话受限范围。读取工作文件应保留路径、当时内容状态和来源；后续使用前文件变化时重新读取与核对。并发写同一文件发生冲突时不盲目覆盖。

外部操作查询可得到已成功、明确未执行或仍未知。已成功复用原回执；明确未执行时重新检查权限和前置条件；支持外部幂等键时按原键查询或有限重试；未知且不可查询时暂停。补偿作为新的获授权操作，取消不自动授予补偿权。租约失效只能防止后续受控写入，不能撤回在途请求。旧执行者回执由宿主验证后进入核对流程，不直接改写新执行的终态或窗口。

文件工具在派发和实际写入前核验执行权；若底层无法阻止失权旧进程继续写入，不开放该空间的自动多实例接管或相应写能力。子代理写工具默认串行；只有资源独立、操作标识稳定且回执可逐项核对时才允许并行。换窗前须收拢完整模型工具消息组；Checkpoint 不序列化旧子代理线程。初期缺少子代理汇聚与恢复适配时，对需可靠恢复的任务禁用其写工具。

## 与其它 spec 的关系

- [S_02](S_02_events-and-result-handoff.md) 定义意图与回执的事件确认。
- [S_03](S_03_context-and-windows.md) 定义文件当前状态进入模型输入的条件。
- [S_05](S_05_checkpoint-and-recovery.md) 定义未决操作在恢复时的核对。
