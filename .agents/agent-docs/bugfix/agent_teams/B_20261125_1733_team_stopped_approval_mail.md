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
designs: [D-20261125-1414-team-stopped-approval-recovery]
---

# 停止成员的审批回执阻塞持久化 Team 完成

PR !3893 基线 `a97f6dfd6` 中，停止成员没有可读取中断的 runtime，审批回执持续未读，阻塞持久化 Team 完成。修复 `2a42d3202` 让收件人在原 checkpoint 上判断回执是否仍有效。独立代码复审通过，启动接管与取消语义见 S_03。

回归提交 `66471dba9` 使用 SQLite、Native/ReAct 和 mock 模型。新增 10 项通过，覆盖失效消费、有效审批只执行一次、缺少凭据或无权回执、装载失败和停机竞争。关联回归 474 项通过、19 项跳过。群聊清表用例的顺序依赖在基线和候选都能复现，单跑均通过。
