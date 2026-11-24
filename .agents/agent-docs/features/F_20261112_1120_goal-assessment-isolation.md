---
id: F_20261112_1120_goal-assessment-isolation
title: Goal 评估调用的观测隔离实施
type: F
author: alan_cheng
author_email: chengshuo5@huawei.com
created: 2026-11-12
updated: 2026-11-12
modules: [core/foundation/llm, extensions/observability, harness, agent_evolving/trajectory]
files:
  - openjiuwen/core/foundation/llm/call_scope.py
  - openjiuwen/extensions/observability/callback_handler.py
  - openjiuwen/extensions/observability/semconv.py
  - openjiuwen/extensions/observability/trajectory_events.py
  - openjiuwen/harness/rails/task_completion_rail.py
  - openjiuwen/harness/observability/rail.py
  - openjiuwen/agent_evolving/trajectory/messages.py
  - openjiuwen/agent_evolving/trajectory/spans.py
designs: [D-20261112-1120-goal-assessment-isolation]
---

# Goal 评估调用的观测隔离实施

## 依据与背景

用户确认的 [D-20261112-1120-goal-assessment-isolation](../designs/active/D-20261112-1120-goal-assessment-isolation/design.md) 解决独立模型评估被记录为领导执行上下文的问题；当前契约同步到 [S_11](../specs/harness/S_11_goal-and-evaluation.md)。
基线为 core 4836fc2815b897bf022e36d5df22481fb446047d，开发在现有 codex/develop，保留原有未跟踪文件。
本记录拥有观测隔离的新决策；F_20261110_1922_team-goal-mode 对 Goal 执行及状态权威的决定继续有效。

## 实际实现与 why

- 在 foundation 的模型调用作用域中增加不可变、task-local 的 LlmObservationContext，scope 仅向 callback 暴露用途和关联字段，不把 metadata 作为供应商参数传递，也不扩展动态 kwargs。
- transcript assessor 为一次逻辑评估绑定 purpose=goal_assessment、goal id、attempt number、assessment id、strategy。兼容模型参数的 TypeError 重试沿用该关联，异常和取消经 finally 恢复外层 scope。
- 共享 OtelCallbackHandler 识别该用途，保留领导的 owner / session；reasoning 子记录同步用途和关联，不伪装成新 teammate。
- 独立评估不推进主 context.window.commit。输入、输出、usage 和失败仍完整采集，执行窗口的下一条 delta 继续基于评估前窗口。
- Goal 执行轮由可信 run_kind / run_context、GoalManager 标记，并传播到 ReAct 子 span；真正执行工作的轮次仍在执行轨迹。
- trajectory_to_messages 消费方显式跳过 goal_assessment，避免无窗口诊断和评估文本进入演进训练消息。原始 Trajectory 不删记录。

否决只做前端过滤（上下文链仍被推进）、完全不采集（丢成本和错误）、新建伪成员（混淆身份）。未改变 GoalManager 状态或评估策略，无目标方案偏差。

## 验证

macOS arm64 / Python 3.11 本仓 venv；运行前 source .venv/bin/activate 并设置 PYTHONPATH=.:$PYTHONPATH。

```bash
python -m pytest tests/unit_tests/extensions/observability \
  tests/unit_tests/core/foundation/llm/test_model_call_scope.py \
  tests/unit_tests/harness/test_task_completion_extensions.py \
  tests/unit_tests/harness/observability \
  tests/unit_tests/agent_evolving/trajectory \
  tests/unit_tests/agent_teams/harness/test_goal_rounds.py -o addopts= -q
```

结果：417 passed, 1 skipped。新回归覆盖观测 scope 跨 task 隔离及取消/异常恢复、assessor 不向模型 transport 泄漏 metadata、真实 callback 输出带关联信息、评估不产生主窗口 commit、后续输入 delta 连续、消息重建不混入评估，以及现有 Goal 续跑/停止行为。
追加真实 typed RunContext / RunKind 的执行轮元数据传播回归，覆盖枚举、字符串及非 Goal 对照；真实服务复验见 Swarm 记录。
Swarm 使用 link-core 在独立 venv 中 editable 指向本源码，跨仓 UI 与存储验证记录在 Swarm 的 F_20261112_1120_goal-assessment-view。

## 限制与交付

未修改用户旧轨迹，不基于提示词重分类历史记录。未执行 Windows/Linux 或性能测试。core 修复独立提交至 fix/goal-assessment-isolation，关联 Issue #2297；Swarm 本地 editable 仅供联调，发布需先合入 core 后更新远程固定依赖。未运行 formatter、lint 或静态检查。
