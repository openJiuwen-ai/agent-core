---
id: B_20261125_1733_team_stopped_approval_mail
title: 停止成员的审批回执阻塞持久化 Team 完成
type: B
author: "MaxMadMax"
author_email: "chengkunck@gmail.com"
modules: [agent_teams]
files:
  - openjiuwen/agent_teams/agent/recovery_manager.py
  - openjiuwen/agent_teams/agent/spawn_manager.py
  - openjiuwen/agent_teams/harness/team_harness.py
  - tests/unit_tests/agent_teams/test_stopped_approval_recovery.py
  - openjiuwen/agent_teams/group_chat/message_handler.py
  - openjiuwen/agent_teams/agent/coordination/kernel.py
  - tests/unit_tests/agent_teams/test_group_chat_integration.py
  - tests/unit_tests/agent_teams/test_coordination_lifecycle.py
  - openjiuwen/agent_teams/tools/database/__init__.py
  - openjiuwen/agent_teams/tools/database/member_dao.py
  - openjiuwen/agent_teams/tools/database/message_dao.py
designs: [D-20261125-1414-team-stopped-approval-recovery, D-20261126-1704-team-lifecycle-boundaries]
---

# 停止成员的审批回执阻塞持久化 Team 完成

PR !3893 基线 `a97f6dfd6` 中，停止成员没有可读取中断的 runtime，审批回执持续未读，阻塞持久化 Team 完成。修复 `2a42d3202` 让收件人在原 checkpoint 上判断回执是否仍有效。独立代码复审通过，启动接管与取消语义见 S_03。

回归提交 `66471dba9` 使用 SQLite、Native/ReAct 和 mock 模型。新增 10 项通过，覆盖失效消费、有效审批只执行一次、缺少凭据或无权回执、装载失败和停机竞争。关联回归 474 项通过、19 项跳过。群聊清表用例的顺序依赖在基线和候选都能复现，单跑均通过。

## 2026-11-26：本 PR 的 UT 后续修复

审批 CAS 所需的 memory SQLite 读锁触发了既有群聊通知取消竞争：取消 get_message 会使 SQLAlchemy 关闭唯一内存连接；未等待通知退出又与 dispose 并发，导致关库挂起和后续路径测试污染。仅给 close 加锁仍会丢失成员表，因此不采用。保留读写互斥，仅在通知 owner 内保护完整查询并延迟传播取消，pause/stop 等待通知排空后释放会话。契约见 S_28。

候选为 b8948bf13 上的未提交修复。审批、CAS、数据库、群聊及生命周期定向回归 684 项通过、20 项跳过；原报告同序链扩大到 53 项通过。另两项 PostgreSQL FakeEngine 和已注册 session 表断言失败在修改前 HEAD 与 develop 46014823e 均复现，保持原样。新增事件屏障覆盖锁等待、cursor 边界、重复取消、并发 drain、通知超时交叠、查询失败和会话释放顺序。全量 CI 尚未验证。

## 2026-11-26：通知重入与恢复资格竞态收尾

按 D-20261126-1704-team-lifecycle-boundaries 实施：通知关闭只排空框架消息读取，当前回调请求停止时跳过自身取消，其余回调维持取消策略。审批接管在同一 write/session 内先占用 STOPPED 成员，再复核预先验证的审批 ID 集合及既有广播、群提及、群通知未读；资格失效则 rollback，不创建 runtime。事务结果确认承受重复取消；仅明确未接管且仍由当前 task 持有的预约可释放，实际启动和异常保留本轮尝试记录。接管后的实例继续正常收件。

验证候选为 399177342 上的本地修复，Python 3.11、真实 SQLite、Native Harness 与 mock 模型。通知定向 10 项、审批恢复定向 28 项通过；关联回归 700 项通过、20 项跳过，仅排除此前 HEAD/develop 已复现的两项基线失败；原失败顺序链 53 项通过。独立代码复审 Approve，无阻塞项。未运行静态工具、完整 CI 或 PostgreSQL/MySQL 集成；验证时尚未提交或推送。整个 stop 的不可取消保证不在本次范围；原有条件清理 await 的取消边界保持。
