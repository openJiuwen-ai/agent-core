---
id: B_20261104_2054_recovered-context-seed-and-execution-environment
title: 冷恢复后成员策略 seed 与执行环境传递修复
type: B
author: alan_cheng
author_email: chengshuo5@huawei.com
modules: [agent_teams, core/sys_operation]
files:
  - openjiuwen/agent_teams/agent/team_agent.py
  - openjiuwen/core/sys_operation/execution_environment.py
  - openjiuwen/core/sys_operation/sandbox/providers/base_provider.py
  - openjiuwen/core/sys_operation/sandbox/code_operation.py
  - openjiuwen/core/sys_operation/local/code_operation.py
designs: []
design_note: 无独立 D；修复 F_20261104_1720_tool-execution-environment / F_20261104_1721_session-execution-settings 在冷恢复与远程调用路径上的遗漏。
---

# 冷恢复后成员策略 seed 与执行环境传递修复

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-11-04 |
| 范围 | TeamAgent.recover_from_session、执行环境合并、沙箱 CodeOperation |
| 关联文档 | F_20261104_1721_session-execution-settings、F_20261104_1720_tool-execution-environment、S_18_harness-interaction-contract |

## 现象与根因

1. 旧 checkpoint 恢复时只替换 live build_context，build_context_seed 仍来自旧快照。
   子进程 spawn 排除 live context 后重新使用旧 seed，导致 leader 与成员的环境策略不同。
2. 沙箱 code 普通/流式接口未读取工具环境，而本地 code 已通过 prepare_environment 读取。
3. 环境增量在客户端按 os.name 转大写，使 Windows 客户端发送到 Linux 沙箱的变量名失真。

## 决策

恢复 live context 时同步深拷贝当前 runtime_spec 的 seed，包括清除过期 seed 的 None 情况。
沙箱 code 两个入口与 shell 一样合并默认值、显式参数与策略覆盖，只发送环境增量。
增量合并保留大小写和覆盖顺序；Windows 规则仅在实际执行端 apply_environment_overlay 应用。
相同原始键被覆盖时重新插入末尾，保证执行端忽略大小写后仍满足策略优先级。

## 拒绝的方案

不只修 leader 的内存标记，因为 process spawn 必须经过序列化。
不按客户端平台改写远程变量名，也不把本机完整环境传给沙箱。

## 验证

新增旧 checkpoint 恢复 → SpawnPayloadBuilder → 子进程 spec 反序列化用例，覆盖旧 seed
缺失/关闭、当前 seed 有值/为空、嵌套字典隔离；沙箱 code 普通/流式的环境覆盖与删除；
Windows 客户端到 POSIX 执行端的大小写，以及 Windows 多层覆盖优先级。

## 保留问题

本次只修复 review 的第 2～4 项。Swarm 分布式 bootstrap 未传递策略启用标记的第 1 项
仍待修复，不能据此宣称所有远端成员已自动启用会话环境。

验证记录：Core 定向回归 172 passed（恢复、环境、shell/code）；Swarm 兼容回归 22 passed。
未运行格式化、静态检查或真实跨机器集群。
