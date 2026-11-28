---
id: D-20261125-1414-team-stopped-approval-recovery
title: 恢复阻塞持久化团队完成的审批邮箱
type: D
slug: team-stopped-approval-recovery
status: in-development
stage: active
author: MaxMadMax
author_email: chengkunck@gmail.com
reviewers: [independent-reviewer]
created: 2026-11-25
updated: 2026-11-27
approved-date: 2026-11-27
domain: protocol
modules: [agent_teams]
tags: [approval, team-completion]
scope: 持久化团队的 Native 成员停止后遗留审批回执阻塞完成
related: []
files: []
---

# 恢复阻塞持久化团队完成的审批邮箱

PR !3893 的完成轮询可能被发给 `STOPPED` 成员的未读审批卡住。缺少 harness 或等待超时无法证明回执已经失效，因此保留未读门禁，由收件人从可核实的原 checkpoint 恢复后判断。

- 复用 leader 完成轮询与成员恢复。只有任务均为终态且成员均处于 settled 状态，目标是无活跃 runtime 且只有合法审批未读的同进程 Native 成员时，才尝试恢复。
- 限定当前 leader 会话持有的原成员身份、精确快照及内置 InMemory checkpointer 实例。确认原成员已退出，并在新实例校验装载成功后恢复邮箱处理。
- 复用原子状态迁移和现有任务清理。失败或取消时仅撤销本次尝试拥有的状态，避免覆盖并发变更。消费规则见 S_03。

验收覆盖过期回执清除后完成、匹配回执只执行一次，以及身份或快照不明、启动失败和停机竞争时保留回执。

2026-11-25 独立初审要求补足原 checkpoint 的身份凭据及启动成功判定。修订后独立复审通过。

## 2026-11-27：READY 结果确认的最小补充方案

### 目标与边界

闭合 PR !3893 评论 204312911 指出的审批恢复启动取消窗口：即使停止操作超时引发重复取消，也必须先确认本次 RESTARTING → READY 的写入结果，让原恢复 owner 按真实归属回滚。本节续写同一审批恢复设计；D-20261126-1704-team-lifecycle-boundaries 中初始接管的原子资格、重复取消保护和通知关闭方案仍有效。

只调整 CoordinationKernel._complete_approval_startup 的 READY 结果等待，扩展原审批恢复测试，并在实施时同步 S_03 和已有 B 记录。不调整 STOPPED → RESTARTING 事务、邮箱消费、checkpoint、每轮限次、事件发布、关流前审批复查或整个 stop 的取消契约；不新增公共 API、数据库字段、超时常量、通用任务管理器或独立文档文件。

### 依据与职责

该方法现有首次 shield 在取消后退化为裸 await claim；第二次取消可进入 DAO task，使 READY 已提交而 binding.ready_written 未赋值。RecoveryManager._recover_approval_member 的 finally 只按该 binding 的归属标志在 RESTARTING 或 READY 上做条件回滚；不应让它猜测是否写入成功，更不能无条件写 STOPPED。S_03 已要求只撤销本次尝试写入的状态。复现细节和实际验证单独归已有 B 记录。

### 最小改法

1. READY CAS 仍只创建一个独立 claim task，不重发、不另开事务。
2. 在本方法内复用 RecoveryManager 初始接管已经使用的循环 shield 模式：每次等待都 shield 同一个 claim；记录 CancelledError，继续等到 claim 终结，普通异常只结束等待并交由结果读取处理。不得在捕获取消后裸 await claim，不调用 uncancel，也不主动取消 claim。
3. 终结后同步取 claim.result()：返回布尔值时先写入 binding.ready_written；随后在 finally 中重新抛出已记录的取消。无取消时保持原 false 分支的 RuntimeError、普通异常传播和成功分支。若同时有 DAO 异常与外部取消，先读取并取走 DAO 异常，按现有初始接管模式优先传播已记录的取消；不能把失败伪造成已写 READY。
4. 只要取消已到达本次等待，就不能继续发布 READY 状态事件、启动成员邮箱 EventBus 或 confirm_approval_startup。既有 owner 等子任务退出后，以 ready_written 为依据，沿原 CAS 清理本次 READY 或 RESTARTING；不改 cleanup_teammate、force_kill 或 stop 的全局等待。

结果确认期间 wait_for 等停止预算可能被清理等待超过，沿用现有“先确认写入归属”的语义，不另设超时后放弃结果。保证范围是重复取消落在 READY 写入结果确认窗口；不承诺任意后续清理 await 或整个 stop 不可取消。若实现验证发现本窗口还需改外层 owner 清理，先回到本节评审，不顺手扩大生命周期重构。

### 验收与定向回归

- 合并扩展现有 READY 清理回归为 test_ready_claim_completion_and_cancellation_cleanup，用真实 memory/file SQLite 和事件屏障，将 READY 提交后、DAO 返回前的窗口保持住；覆盖既有单次取消、外层 wait_for 停止超时的第二次取消，以及结果尚未释放时再次取消。外层 wait_for 单独建 task，先观察取消传播再释放 DAO 结果，避免测试自己等不到 finally 而死锁。
- 核验结果：claim 不因这些外部取消被终止；确认结果前无邮箱处理/启动成功确认；释放结果并等关闭链退出后，本次成员为 STOPPED、无存活/登记 handle、原审批仍未读、工具执行次数为 0。必须等清理真正退出后读 DB，不能只看停止 task 的异常或当前取消计数。
- 保留正常启动、匹配审批只执行一次、单次取消和每轮限次的既有断言；补足/复用 READY CAS 返回 false 与抛异常的分支覆盖，确保不伪造写入成功、不开放邮箱，不覆盖其它状态。无取消时保留原错误语义，有取消时落实上述异常优先级。
- 先运行本次复现及 test_stopped_approval_recovery.py，再运行 test_coordination_lifecycle.py、test_database_concurrency.py、test_db_sessions_watchdog.py。不运行本地静态工具；本计划不授权提交、推送或远端评论。

预计生产修改集中一个方法，约 15–25 行；测试优先参数化原用例并补必要结果分支，约 40–80 行。仅修正等待与结果归属，不提取跨模块 helper；若改动明显超出此边界，先说明原因再复核。

### 评审状态与交接

2026-11-27，independent-reviewer（任务 ready_result_plan_review）独立评审结论为 Approve，无阻塞发现、无必须修订项。评审核对了 recovery → cleanup_teammate → force_kill → member 的取消等待链，确认结果写回 binding 后的取消仍由原 owner 继续条件回滚，本窗口无需调整整个 stop。实施须落实真实停止链上的二次/多次取消、结果/异常分支和清理后状态断言；等待可能超过外层超时预算是明确取舍。

用户已于 2026-11-27 明确授权按本补充方案实施。方案批准不等于修复验证通过，实际实施、验证与限制见已有 B_20261125_1733_team_stopped_approval_mail；提交及推送按用户后续明确授权执行。
