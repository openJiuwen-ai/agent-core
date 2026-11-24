---
id: B_20261118_1219_turn-step-runtime-cleanup
title: turn.step 测试会话绑定泄漏导致 PR 门禁偶发失败
type: B
author: liaojunzhuo-sefonsoft
author_email: liaojunzhuo@sefonsoft.com
modules:
  - tests/unit_tests/agent/react_agent
files:
  - tests/unit_tests/agent/react_agent/test_turn_step_event_integration.py
designs: []
design_note: 修复既有测试的资源清理与隔离，不改变生产代码或公开契约，无需独立设计。
---

# turn.step 测试会话绑定泄漏修复

## 现象与影响

Issue #2328。!3814 的远端 UT 报告中，模型重试用例失败于 `prompt.compose CORE returned an invalid output`；该 PR 的枚举回归测试均通过。失败用例单独执行、整文件普通执行均可通过，表明需要核对共享状态与回收时机。

## 基线对照与根因

在上游原始提交 `bdbb52a9ecbe2d285b5949e692abbbad0bc22dd1` 的独立 worktree 中，延迟循环垃圾回收，依次执行工具预取错误路径和模型重试用例，得到 1 passed、1 failed，异常与 CI 一致，证明问题不依赖 !3814 的枚举修改。

两个工具预取测试绑定 `MagicMock(spec=DeepAgent)` 到进程级注册表的固定会话 `step-session`，但 finally 未调用 unbind。注册表的弱引用不能保证循环引用对象在用例结束时立即回收，后续重试用例误读前例残留的模拟 Agent，进入 prompt.compose 后拿到非法模拟输出。

## 修复决策

在创建绑定的两个测试的 finally 中显式调用既有 AgentRuntime.unbind，分别覆盖正常返回及预期异常退出。保留生产注册表的弱引用语义，不修改事件、模型重试或异常处理，不跳过失败用例。

增加文件范围的回归 fixture：记录 bind 调用并持有调用参数，结束后用公开 get 检查这些绑定已解除。强引用使漏清理不能被 GC 提前回收掩盖，测试断言验证的是测试边界的共享状态。

## 验证证据

- Windows / Python 3.11.15，复用原仓库已通过 uv sync 安装的锁定测试环境；通过实际导入路径确认基线源码来自独立 worktree。
- 修复前：增加泄漏检测 fixture 后，两个生产绑定的用例出现 teardown 错误，原模型重试用例失败。
- 修复后：文件全部 12 项用例通过；延迟循环 GC 后再次运行全部 12 项也通过。
- ReActAgent 目录全部 254 项测试通过，存在既有弃用和序列化告警。
- 未调用真实模型、外部服务或修改生产行为；未执行全仓本地测试，远端完整 UT 以新 PR 的 CI 为准。
- 按根规则未运行格式化及静态分析工具。

## 资料与后续

本记录是此项测试隔离缺陷的唯一维护位置，按仓库工具刷新相关文档索引。测试显式获取的全局资源应在 finally 中对称释放，弱引用不替代测试生命周期清理。

等待远端门禁与维护者评审。该修复合入后，原枚举 PR 可以同步上游并重新运行门禁；目前不把本地通过等同于远端 ci-successful。

## 2026-11-23 上游同步与冲突处理

此前两个 PR 均已通过远端 CI 和 CLA；随后 develop 更新至 `d297129ef402c8685102a2804a38e1269ed3886f`，导致生成索引和测试文件的导入区发生冲突。

合并上游并重新生成文档索引；导入区同时保留上游新增的 uuid 与回归 fixture 使用的 Iterator。保留上游唯一会话 ID 机制，以及本修复的显式解绑和泄漏断言；唯一 ID 避免跨用例复用，显式解绑保证资源按生命周期释放。

使用 `uv sync --group test --extra observability --python 3.11` 创建匹配新锁文件的隔离环境，合并后的 ReActAgent 目录 262 项测试通过。默认测试环境缺少上游导入所需的 OpenTelemetry exporter，安装仓库已有的 observability extra 后完成验证，未修改依赖定义或锁文件。新提交的远端门禁需要重新确认。

同一候选关闭循环 GC 后，turn.step 文件 12 项测试通过；原有绑定清理和泄漏检查仍然有效。
