# Persistent Agent 员工权限与确认恢复设计方案

## 1. 元信息

| 项 | 值 |
| --- | --- |
| 建档日期 | 2026-10-24 |
| 方案成熟度 | 已确认，代码与组件验证完成；真实宿主验收待完成 |
| 关联计划条目 | PA-09 |
| 设计依据 | [Persistent Agent 架构](../designs/persistent-agent-architecture.md)、[PA-09 开发计划](../designs/persistent-agent-development-plan.md)、[JiuwenSwarm JM-26](../../../../../../huenrui_jiuwenswarm/jiuwenswarm/server/jiuwenmate/docs/features/F_26_employee-permission-profiles.md) |
| 验证摘要 | Persistent Agent 定向套件 291 passed、2 条既有 Pydantic 弃用警告；JiuwenSwarm 服务端 270 项和前端 49 项通过。真实飞书身份、投递与重启验收待部署环境 |
| Refs | JiuwenMate 团队版员工权限配置需求 |

## 2. 背景、目标与范围

Persistent Agent 已提供 permission interruption、Session 中断状态记录以及 `resume_persistent_request`。本轮将员工权限策略纳入不可变 Spec，为每次执行装配对应策略，并实现原生确认回复到精确工具调用的恢复适配；JiuwenSwarm JM-26 负责员工配置、审批路由和渠道身份。

范围包括 `default`、`automatic`、`full_access` 的 Spec 契约，调用级审核、人工确认恢复及 host-wide workspace scope。JiuwenMate 的员工配置数据库、审批渠道和前端不由本仓实现；跨仓契约由双方 feature 文档链接维护。

## 3. 设计约束、假设与待决问题

- Spec 是每次 Execution 唯一有效的权限策略来源；权限字段需参与 fingerprint。
- `automatic` 的一次审核决定只能批准被评估的单次调用；不确定或评估器不可用时转人工。
- 人工确认恢复必须沿用原 Request、Session 和 Spec；tool_call_id 绑定原生等待现场。
- 文件 scope 由宿主根据可信已发布模式提供，默认模式仍按原 employee workspace 和 Session 私有目录约束。
- `full_access` 只能扩展到宿主 OS 用户已有权限，core 不提供权限提权机制。

## 4. 总体方案与设计取舍

### 4.1 方案概览

扩展 `PersistentAgentSpec` 的权限策略快照，并让每次构造使用该策略装配权限 Rail。权限 `ask` 时复用原生中断和 Session state store；宿主接收 `execution.paused` 后负责确认身份、持久化路由并调用恢复入口。文件端口可返回明确的 host-wide scope，但必须与当前 Execution 身份及策略版本一致。

### 4.2 关键决策

#### D-01 权限只在不可变 Spec 中发布

- 选择与依据：权限 profile 和有效策略参与 Spec fingerprint，避免运行时全局配置改变已经接受的 Request。
- 代价与成立条件：更新审核模型或规则需要发布新 Spec。

#### D-02 复用 PA-10 状态记录和原生 ConfirmInterrupt

- 选择与依据：PA-10 已持久化 DeepAgent 状态、中断现场与窗口；复用 `ConfirmReply` 和原生 `resume_persistent_request`，不新建工具重放层。
- 代价与成立条件：JiuwenSwarm 需等待 core Execution 结束后按同一 Request 建立新 Execution 并恢复同一 Session。

#### D-03 workspace scope 由宿主按 Spec 授权

- 选择与依据：core 校验 scope 与 Execution 的 Agent、Session、principal 和 policy revision 一致；宿主只有在 full profile 时返回 host-wide 根范围。
- 代价与成立条件：OS 用户权限是最终边界，full profile 不能代表独立 OS 隔离。

### 4.3 替代方案与排除理由

只扩展 JiuwenSwarm UI 或使用进程级全局权限配置会造成员工策略串用，无法作为 Persistent Agent 的可信执行契约；另建工具重放机制会绕开 core 已有中断状态和 exact-call 校验，因此均不采用。

## 5. 详细设计

### 5.1 Spec 与构造

Spec 使用 Literal 模式字段并携带有效权限策略的 JSON 快照；旧 Spec 经默认值兼容为 `default`，未知模式拒绝。构造时把快照放入本次 DeepAgent 权限配置，不能从共享可变配置覆盖。权限模式或策略变化应改变 fingerprint。

### 5.2 中断与恢复

现有 `execution.paused(awaiting_confirmation)` 事件携带 `tool_call_id`、工具名和脱敏摘要。core 保存含原生中断现场的 Session 状态；新执行实例读取该状态后，校验 Request 和 tool_call_id，转换可信回复为 `InteractiveInput`，仅恢复匹配的调用。超过宿主 15 分钟审批时限后由宿主拒绝调用恢复入口，不执行等待的工具。

### 5.3 workspace 授权

read/write scope 增加明确且默认关闭的 host-wide 授权语义。访问路径解析仍阻止相对路径穿越和符号链接逃逸；host-wide profile 不得遗留 employee-only Session exclusion，default 模式则保持现有私有 Session 隔离。宿主失败或身份不匹配时拒绝访问。

## 6. 影响面与实施设计

| 文件 | 责任 |
| --- | --- |
| `schema/spec.py` | Spec profile 和策略字段、fingerprint、构造注入 |
| `workspace/access.py`、`workspace/file_access.py` | host-wide scope 契约与路径授权 |
| `execution/entries.py`、`execution/session_state.py` | exact-call 暂停与恢复、持久现场 |
| JiuwenSwarm `spec_provider.py`、`execution_driver.py`、`ports/workspace_access.py` | 发布快照、宿主授权与审批路由接入 |

## 7. 验证设计、执行记录与结论

### 7.1 预期验证

- Spec 缺省模式、未知模式拒绝、JSON round-trip 与 fingerprint 隔离。
- default/automatic/full profile 执行差异以及 host-wide scope 失败关闭。
- 错误 tool_call_id、Request、Session 或 Spec fingerprint 的恢复请求拒绝。
- Session state 重启恢复与确认后继续原调用；拒绝、超时和重复回复不执行工具。

### 7.2 实际执行与结论

agent-core Persistent Agent 定向回归 291 passed（2 条既有 Pydantic 弃用警告）；覆盖 Spec 快照、host-wide 读写 scope、逐调用权限策略、原生回复映射和精确 tool_call_id 恢复。JiuwenSwarm 服务端审批与恢复套件 270 passed、前端相关套件 49 passed。真实飞书应用身份、消息投递和进程重启场景尚未执行；实现证据及宿主边界见 JiuwenSwarm [JM-26](../../../../../../huenrui_jiuwenswarm/jiuwenswarm/server/jiuwenmate/docs/features/F_26_employee-permission-profiles.md)。

## 8. 风险与已知遗留

core 组件测试不能证明 JiuwenSwarm 的 SQLite 审批记录、Web/飞书身份验证与真实重启恢复成立；这些必须由对端和联合验收覆盖。`full_access` 的宿主范围不超过进程 OS 权限，但可能包含其他员工数据。
