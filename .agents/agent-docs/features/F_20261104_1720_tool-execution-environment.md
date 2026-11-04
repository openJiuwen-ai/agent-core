---
id: F_20261104_1720_tool-execution-environment
title: 工具执行环境作用域
type: F
author: alan_cheng
author_email: chengshuo5@huawei.com
modules: [core/sys_operation, harness]
files:
  - openjiuwen/core/sys_operation/execution_environment.py
  - openjiuwen/core/single_agent/ability_manager.py
  - openjiuwen/harness/schema/build_context.py
  - openjiuwen/core/sys_operation/local/shell_operation.py
  - openjiuwen/core/sys_operation/sandbox/shell_operation.py
designs: []
design_note: 无独立 D；在现有 harness 工具调用与 team 会话存储契约上的小范围扩展。
---

# 工具执行环境作用域

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-11-04 |
| 范围 | AbilityManager、BuildContext、shell 本地与沙箱执行 |
| 测试 | `tests/unit_tests/core/sys_operation/test_execution_environment.py` |

## 背景

同一进程中的多个成员需要不同的命令环境。让模型在每次 bash 参数中填写环境容易遗漏，
修改进程全局环境会在并发执行时串用身份。平台策略还可能在会话运行中改变。

## 数据结构与决策

`ExecutionEnvironment` 保存不可变的 `values` 和 `overrides`，值为 null 表示删除变量。
优先级是执行端基础环境、values、显式调用参数、overrides。
`BuildContext.resolve_execution_environment()` 是平台异步扩展点，默认返回空环境。
Spec 构建和 NativeHarness 装配把 resolver 绑定到本 agent 的 AbilityManager。
实际执行单个工具时获取快照，在 context manager 中绑定，异常和取消均恢复原作用域。
无 resolver 的 agent 显式建立空环境，不能继承另一个成员的身份。

本地 shell 在 prepare_environment 合并到环境副本；沙箱只传增量，不能复制宿主机环境。
工具构造、模型 schema 和权限 rail 不需要新增环境参数。后台命令继承启动时快照。
执行日志中 environment 的值脱敏。

## 拒绝的方案

- 修改 os.environ：同进程成员并发时互相污染。
- 用 before/after rail 配对绑定：异常、取消与重试不应依赖两个分散回调清理。
- 把成员信息层层传给 BashTool 构造函数：漏掉备用工具且耦合产品身份策略。
- 将 Git 域名写入 core：域名和会话开关属于平台策略。

## 验证

覆盖优先级、删除、不可变快照、并发隔离、取消恢复、非成员隔离、真实 shell 普通/流式执行
及沙箱仅传递环境增量。Swarm 的端到端测试验证真实 Git author/committer。

## 边界

本能力覆盖框架工具执行作用域内接入 shell 环境协议的调用。不自动配置外部 MCP 服务、
浏览器服务和独立第三方 CLI 的进程环境，也不构成防止 shell 自行覆盖变量的安全边界。
