# 有界执行与调用级日志

| 项 | 值 |
|---|---|
| 日期 | 2026-10-22 |
| 范围 | DeepAgent/ReAct 迭代耗尽、单轮宿主上限、调用日志 |
| 测试基线 | 本轮未执行测试；仅源码审查 |
| Refs | S_01、S_02；docs/superpowers/plans/2026-10-22-rsi-termination-diagnosis-plan.md |

## 背景

RSI 中求解器耗尽 100 次迭代后仅返回错误，已有成果没有最终收尾；诊断仅看到结果，
缺少独立运行日志和有效配置，容易把运行机制归因为 Prompt/Skill 问题。

## 状态与配置

通用 DeepAgent 默认 error。按用户最新要求，新建 RSI 默认 300 步且耗尽直接停止，
不启用额外收尾；候选 runtime.yaml
可调整实际步数与耗尽策略，宿主硬上限独立固定。所有策略进入评测身份。
调用日志按 Agent/session/invocation 区分，单文件 4 MiB、单记录 2 KiB，记录有限脱敏摘要。

## 决策

- 复用 ReAct 现有收尾，显式禁止收尾的框架重试，保留预算耗尽元数据。
- 硬上限仅支持单轮；不改变已有未设置硬上限的 task-loop 语义。
- 流式生命周期由独立 producer Task 拥有，ContextVar 不跨消费者 Task。
- 同时保留原全局日志与调用摘要，缺失/截断显式记录。

## 拒绝的方案

- 给每个 Agent 调用反复重配全局 log_path：并发串写。
- 在 async generator yield 外长期保持消费者 ContextVar：跨 Task reset 失败。
- 分别为循环、格式修复与收尾追加独立预算：调用次数相乘。
- 达到上限就自动扩充预算：无法追溯候选的成本与运行条件。

## 验证与已知边界

独立源码复核覆盖了调用上限、外层 Rail 回调、流式取消和收尾覆盖元数据。
未运行回归或真实模型实验；工具 stdout/stderr 的原始结果仍由执行轨迹承载。
