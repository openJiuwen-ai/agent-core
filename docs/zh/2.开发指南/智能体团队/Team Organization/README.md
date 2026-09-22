# Team Organization

Team Organization 是建立在 AgentTeams 之上的跨团队协作层。每个 Team 仍由自己的 Leader 管理内部 Teammates；加入 Organization 后，由各 Team Leader 通过共享任务池、可靠消息和组织工作空间协作。

## 与 AgentTeams 的关系

| 概念 | 协作范围 | 主要职责 |
|------|----------|----------|
| AgentTeams | 单个 Team 内部 | Leader 拆解任务并协调 Teammates |
| Team Organization | 多个 Team 之间 | Team Leader 认领、委派、审核和汇总跨团队任务 |
| AgentGroup | 专家团队模板 | 描述角色、提示词、技能和能力标签，不是运行中的 Team |
| Summary Team | Organization 共享 Team | 在 `SUMMARY_TEAM` 模式下汇总已验收的来源产物 |

Organization 的 Leader 是协作主体。组织运行时负责持久化、通知和唤醒，不代替 Leader 判断是否认领任务、如何修复失败或如何回应消息。

## 核心能力

- 创建和解散 Organization，由 Owner Team 邀请成员 Team。
- 使用共享 Task Pool 创建、认领、委派、执行和审核任务。
- 通过可靠 Leader Inbox 进行协商，并在重启后重新投递未确认消息。
- 使用 Organization Workspace 共享跨 Team 产物，同时隔离各 Team 的写入目录。
- 选择逐层责任汇总（`HIERARCHICAL`）或独立 Summary Team 汇总（`SUMMARY_TEAM`）。
- 由宿主按需发现 AgentGroup、创建专家 Team 并加入 Organization。
- 从持久化状态恢复成员绑定、执行中任务、待审核任务、消息和汇总执行。

## 适用场景

Team Organization 适合需要多个相对独立团队协作、产物需要明确验收，或执行过程需要持久化恢复的复杂任务。单 Team 的角色分工仍优先使用 AgentTeams；简单的 Agent 调用链不需要创建 Organization。

## 当前约束

- 同一 Organization 同时只允许一个非终态普通根任务；Summary Task 不计为另一个根任务。
- 受邀 Team 必须与 Owner Team 使用同一个 `TeamDatabase` 实例；跨进程部署时，各 Team 必须访问同一份组织数据。
- 专家 Team 和 Summary Team 的实例化由宿主实现。agent-core 只定义适配协议和协作运行时。
- 任务状态以数据库为事实来源；事件总线只负责通知和唤醒。

## 继续阅读

- [快速开始](./快速开始.md)
- [组织与任务协作](./组织与任务协作.md)
- [消息与工作空间](./消息与工作空间.md)
- [汇总与专家团队](./汇总与专家团队.md)
- [运行时集成与可靠性](./运行时集成与可靠性.md)

