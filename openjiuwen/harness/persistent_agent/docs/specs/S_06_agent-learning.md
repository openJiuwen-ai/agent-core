# S_06 Agent 级主动学习规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 状态 | 拟议契约；尚未实现 |
| 关联模块 | `openjiuwen/harness/persistent_agent/{learning/,schema/learning.py}`（拟议路径） |
| 最近一次修订日期 | 2026-10-08 |
| 关联 feature | N/A；开发计划 PA-19～PA-21 |
| 设计依据 | 旧版详细架构 §13（见 Git 历史）；当前设计为 [Persistent Agent 详细架构设计](../designs/persistent-agent-architecture.md) |

当前没有本模块的 LearningCoordinator 或 LearningJobPort 实现；本规约描述独立学习作业的拟议契约。

## 范围 / 边界

本规约定义从专门授权的已确认工作证据识别 Agent 学习目标、跟踪来源处理与评价反馈。AgentServer 按 `agent_id` 调度并分配独立预算；PersonalContext 负责来源采集、内容处理、版本发布和游标。本模块不占业务 Session 队列，不管理平台凭证，也不直接修改 PersonalContext 内部文件。

## 现有实现的复用边界

学习目标识别与反馈关联是本模块新增逻辑；来源发现、采集运行、查询及知识组织复用已有 [`PersonalContext`](../../../personal_context/personal_context.py) 能力，不在 `persistent_agent/` 复制 Provider、采集游标或知识索引。发布版本与授权绑定仍须由适配器补齐。业务模型读取知识时复用 [`PersonalContextRail`](../../../rails/personal_context.py) 的附件生命周期，并按 [S_03](S_03_context-and-windows.md) 补齐授权与版本绑定。宿主负责学习作业调度和独立预算，本模块不增加常驻调度器。

## 不变量

1. 学习作业有自己的稳定作业标识与审计记录，不伪造业务 Session、Request 或 Execution；普通业务调用不取得 `LearningJobPort`。
2. 跨 Session 证据读取需专门分析授权；分析权不等于知识发布权，派生知识继承来源可见范围，不把不同可见范围的材料合并。
3. 目标状态以宿主提供的持久化仓库为准，推进时检查预期修订。同一目标重复调度不能产生并行更新者。
4. 来源关联未确认或 PersonalContext 发布失败时，不宣称知识已更新，不开放新导航；来源游标不能先于对应内容发布成功而推进。
5. 新知识与来源变更在业务模型的下一次装配使用获授权的不可变发布版本；当前 Request 的人设、模型、工具与 Skill 配置快照不因学习更新而改变。

## 接口契约与错误语义

拟议内部入口 `LearningCoordinator.run_learning(job_context)` 接收稳定作业标识、Agent、证据授权范围、预算、取消信号和 `LearningJobPort`，返回目标及处理结果引用；它不是常驻定时器。`LearningJobPort` 提供专门授权的证据读取、目标条件更新、来源关联及 PersonalContext 作业交接。业务调用的只读 `KnowledgePort` 不具备这些修改权限。

`LearningGoal` 拟包含 `agent_id, goal_id, kind, evidence_refs, scope, priority, budget, completion_criteria, state, revision`。目标可为知识缺口或高优领域建设，识别依据包括岗位相关性、价值、时效、频率、成本和可验证完成条件。网络错误、工具故障或无权限读取不能直接归类成“知识缺口”。同 Agent、同主题及同授权范围内使用稳定去重键；不同可见范围不合并。

| 阶段 | 必要确认或失败处理 |
| --- | --- |
| 获取证据 | 只读取授权窗口及 SessionStore 已提交事实，保留来源引用 |
| 识别与合并目标 | 保存目标、范围、价值与完成条件，使用预期修订防并发覆盖 |
| 验证来源 | 优先已有来源；新凭证或扩权需宿主配置确认 |
| 关联来源 | 稳定 `source_id` 经配置预期修订提交，未确认关联不开放导航 |
| 采集与发布 | PersonalContext 保证页面、来源元数据、版本指针与游标可核对 |
| 收集反馈 | 后续已提交任务的覆盖、引用有效性、纠错和重复检索作为下一轮证据 |

学习目标的逻辑状态可为待验证、采集中、已更新、暂停或失败。作业预算不足、撤权、来源关联失败和发布失败分别保留可追溯状态，不伪造已更新。每轮有停止条件；持续领域目标由后续独立调度增量推进。单次模型输出不能自证知识正确。

## 状态及数据生命周期

同一 PersonalContext 发布根由宿主或发布方协调写入，检查基础版本后发布不可变页面与来源元数据，并原子切换可读指针。`collection_enabled` 与 `agent_use_enabled` 分别控制采集与使用，均不代替身份授权。

发布和来源游标提交可能跨介质：用稳定采集批次记录发布版本及待推进游标。版本已发布但游标未推进时，重试核对原批次并补齐游标；未发布不得推进成功游标。下一次模型装配发现新版本时替换旧导航、正文与派生摘要；当前撤权优先于缓存或已固定版本。失败恢复只推进尚未完成且仍获授权的阶段。

## 与其它 spec 的关系

- [S_03](S_03_context-and-windows.md) 定义业务模型如何读取获授权的 PersonalContext 版本。
- [Harness PersonalContext 规约](../../../docs/specs/S_17_personal-context.md) 归属采集和使用基础；本规约只定义 Persistent Agent 的学习目标与交接边界。
