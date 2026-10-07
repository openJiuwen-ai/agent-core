# Team Organization

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/organization/`、`runtime/manager.py` |
| 最近一次修订日期 | 2026-10-07 |
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
6. 组织工具只向具备对应角色的 Leader 暴露，不赋予普通成员组织管理权限。
7. 共享工作空间允许读取组织资料；写入限制在本团队发布目录或允许的共享目录。
   Git 初始化、配置和初始提交使用同一个互斥锁；文件锁在工具成功、失败和异常路径均释放。

## 接口契约

`OrganizationRuntimeManager` 管理建立、绑定、加入、释放和运行时查询；`OrgTaskManager`
管理任务池；`OrgMessageService` 管理消息正文与收件回执；`OrganizationWorkspaceManager`
管理文件布局、写入权限、锁与版本。具体参数和错误行为以导出对象及单元测试为准。

Runner 的组织入口复用现有 TeamRuntimeManager，不改变未绑定组织的 Team 的派发方式。
会话释放先释放组织关联，再停止团队；普通 stop/pause/finalize 仍遵循 S_06 的生命周期语义。
组织工具由现有工具工厂和角色权限链装配，组织命名空间与原有团队工具分开。

## 与其他 spec 的关系

- [S_06 Runtime Pool & Dispatch](S_06_runtime-pool-dispatch.md)：组织绑定补充其进程内对象池，不替代并发门禁。
- [S_08 Team Tools Contract](S_08_team-tools-contract.md)：组织工具复用 TeamTool 与权限装配。
- 用户指南位于 `docs/zh/2.开发指南/智能体团队/Team Organization/` 及对应英文目录。
