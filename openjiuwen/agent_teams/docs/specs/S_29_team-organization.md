# Team Organization

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/organization/`、`runtime/manager.py` |
| 最近一次修订日期 | 2026-10-26 |
| 关联 feature | `F_115_team-organization-develop-integration.md` |

## 范围 / 边界

Organization 在已有 Team 之上提供跨团队任务池、消息、专家团队、汇总团队和共享工作空间。
它不是另一套成员调度器：Team 内的成员运行、权限、暂停与恢复继续由原有运行时管理。
当前组织中的参与团队 Leader 必须处于同一进程并共享同一个 TeamDB 实例；不提供跨进程、跨节点的组织路由。
这不改变单个 Team 原有的成员启动模式。

## 不变量

1. Organization 有明确的 owner team；同一 team 的组织绑定不能跨组织复用。
2. 任务创建、领取、委派、完成、审核与汇总以数据库状态为准；事件用于通知，不承担结果正文存储。
3. 领取和状态变更必须带任务当前状态条件，避免多个团队同时成功领取。
4. 子任务保留 parent/root 关系；汇总任务保存来源关系和执行状态，不以普通子任务代替来源依赖。
5. 未领取任务按策略进入描述修订或超时关闭，不能依靠 Leader 恰好再次被调用来推进。
6. 组织工具只向具备对应角色且 `enable_organization` 为 True 的 Leader 暴露，不赋予普通成员组织管理权限；
   该 spec 字段默认 True，是宿主关闭整条组织能力的唯一开关。
7. 共享工作空间允许读取组织资料；写入限制在本团队发布目录或允许的共享目录。
   Git 初始化、配置和初始提交使用同一个互斥锁；文件锁在工具成功、失败和异常路径均释放。
8. 组织订阅入口按 `OrgEventMessage` 解析组织事件，不依赖传输消息的普通 Team 事件映射。
9. Summary Execution 的正式完成以 `org_summary_complete` 持久化提交为准，不以文件生成或 Team idle 为准。
   同一运行时、同一执行在自然暂停后最多补偿一次提交轮；仍未提交时通知 Root Leader，
   不自动重启研究。取消、shutdown、移除或非暂停 Team 不触发补偿，补偿预算不跨进程持久化。
   提交补偿轮只允许 `read_file`、`org_summary_get_inputs` 和 `org_summary_complete`；普通汇总轮不受此限制。
   阻塞通知发送给根任务当前 assignment 对应的已注册 Leader，通知异常不能重新排队汇总轮。
10. 宿主 Summary 输出转发器将成员执行错误投递给 Summary Leader，不以内部任务板已收敛为前提。
    同一执行的同一错误帧去重；最多一次故障恢复通知，再失败则向根任务 Leader 报告阻塞。
    Summary Leader 的撰稿交接最多一次初稿、一次定向修订；正式提交成功立即结束当前 Agent Turn。
    宿主各 Summary 角色继承有限正整数迭代预算；缺失或非法预算使用有限默认值，不配置无限循环。

## 接口契约

`OrganizationRuntimeManager` 管理建立、绑定、加入、释放和运行时查询；`OrgTaskManager`
管理任务池；`OrgMessageService` 管理消息正文与收件回执；`OrganizationWorkspaceManager`
管理文件布局、写入权限、锁与版本。具体参数和错误行为以导出对象及单元测试为准。

Runner 的组织入口复用现有 TeamRuntimeManager，不改变未绑定组织的 Team 的派发方式。
会话释放先释放组织关联，再停止团队；普通 stop/pause/finalize 仍遵循 S_06 的生命周期语义。
解散组织清除各参与团队及其成员的组织绑定配置，并停止宿主标记为 `expert_team` 的临时专家团队和汇总团队。
Owner 及原本独立存在的受邀团队保持运行实例；专家团模板、团队历史和已生成的交付物保留。
组织工具由现有工具工厂和角色权限链装配，组织命名空间与原有团队工具分开。
挂载入口 `ensure_control_tools` 与 `ensure_team_binding` 受 `TeamAgentSpec.enable_organization` 门控，
关闭时 Leader 不挂载组织工具，既有绑定也不回灌。

本 Team 承担组织子任务时，开始执行或向内部成员派发工作前，Leader 应先调用 `org_update_task(action='start')`。内部成果完成后，Leader 检查成果并及时回写对应组织子任务，再按创建者权限显式验收。成员分工使用团内工具；本团承担的组织任务仍用组织工具管理状态。内部任务完成不自动完成或验收组织任务，不新增调度、轮询或状态联动。

## 聚合选择与交付输入

共享聚合规则保存在 `prompts/cn/org_root_aggregation.md` 与 `prompts/en/org_root_aggregation.md`，由现有 `load_template` 加载。组织系统提示词、认领后执行提示词和工具说明复用同一模板，不在任务数据模型或运行时模块中各自复制文案。

认领根任务后、拆分前显式选择模式。根据根任务要求和各团队实际承担的交付范围选择模式。不存在承担完整根交付的专业团队，且各团队分别提供并列、独立的领域结论，根任务需要汇集、编排或综合这些结论时，必须选择 SUMMARY_TEAM。无论内容多少、任务大小或是否存在冲突，均不得以节约时间、降低开销、只是收集或 Owner 能自行整理为由改选 HIERARCHICAL。只有某个专业团队承担完整根交付，其最终输出本身即可满足根任务，其他团队提供所需输入、组件或验证时，才选择 HIERARCHICAL。团队领域正交不改变这一条件。Owner 名义上的汇总职责不构成完整专业交付，也不得虚设最终集成团队。用户明确指定模式时，在运行时约束允许的范围内遵循。规则不改变数据库默认值、已有选择或状态转换。

`org_summary_get_inputs` 在既有执行与来源字段外返回 `root_task`（task_id、title、description、output_spec）；只有该执行绑定的 Summary Team 可读取，根任务也必须属于同一组织。根任务和汇总任务约束交付目标与格式，事实仍只来自绑定来源。Summary 自有最终文件仍须发布到组织 `summary/`，来源成品保留路径与类型，不默认替换为报告。

`org_create_summary_execution` 允许在来源未就绪时创建执行，但必须返回最新任务状态。等待时返回 `blocked_sources`（绑定的必需来源中未完成或未验收项的 task_id、task_status、review_status、reviewer_team_id），提示 Owner 处理来源；本团执行的子任务也需要显式验收。仅返回来源状态，不向 Owner 开放 Summary 专用输入快照。等待阶段发布 `summary_execution_waiting_sources`，不得报告已委派或提示只等待交付；只有运行中的执行发布 `summary_execution_delegated`。验收门槛和现有唤醒逻辑保持不变。

## 与其他 spec 的关系

- [S_06 Runtime Pool & Dispatch](S_06_runtime-pool-dispatch.md)：组织绑定补充其进程内对象池，不替代并发门禁。
- [S_08 Team Tools Contract](S_08_team-tools-contract.md)：组织工具复用 TeamTool 与权限装配。
- 用户指南位于 `docs/zh/2.开发指南/智能体团队/Team Organization/` 及对应英文目录。
