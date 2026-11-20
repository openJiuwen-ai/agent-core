---
id: F_20261029_1036_ttse-consult-prerequisites
title: TTSE 经验检索前置提示词约束
type: F
author: xinyang_bali
author_email: 835397263@qq.com
modules: [openjiuwen/agent_evolving/ttse]
files:
  - openjiuwen/agent_evolving/ttse/render.py
  - openjiuwen/agent_evolving/ttse/consult.py
designs: []
design_note: 根据用户明确要求加强复杂任务及使用 skill 前的经验检索提示词，属于局部提示词调整，无独立设计。
---

# TTSE 经验检索前置提示词约束

日期：2026-10-29。

## 背景与决策归属

原目录引导允许无匹配类目或空目录时直接执行，且没有明确要求使用 skill 前检索。
本次局部替代 `DISK_CATALOG_GUIDANCE_CN/EN` 的调用时机规则，并同步工具描述。
这是经验消费策略的独立调整，与 F_20261027_1810_ttse-plugin-runtime 的插件构造和关闭生命周期决策无替代关系。

## 实际实施与决策

- 工具可用时，相对复杂任务必须在具体规划或执行前检索；读取 SKILL.md、加载或调用任何 skill 前也必须检索，包括简单任务。
- 等待结果并结合适用经验后再执行依赖操作，禁止并行执行依赖动作或事后补查。
- 类目不明确、无匹配类目或目录为空时使用 `all`；query 包含任务目标和拟用 skill 名称或能力。
- 同一任务内结果覆盖目标和拟用 skill 时可复用；目标变化、未覆盖的 skill、新故障需要补查。
- 简单闲聊和无需 skill 的一步回答可跳过；检索无匹配或工具不可用时继续，避免反复空查。

保留历史经验服从当前工具证据的原则。使用现有 `all` 检索能力，不增加参数或运行时门禁。
拒绝继续以“目录无匹配”为跳过条件，因为类目名称不能证明经验无关。
未采用强制拦截其他工具的方案：用户要求加强 prompt，本次范围为模型协作约束。

## 验证

在本仓 `.venv` 激活后设置 `PYTHONPATH` 为仓库根、`PYTHONUTF8=1`，执行：

```text
python -m pytest -o addopts='' -q --tb=short --show-capture=no tests/unit_tests/agent_evolving/ttse/test_ttse_core.py tests/unit_tests/harness/rails/evolution/test_ttse_rail.py
```

结果：111 passed，2 条已有 Pydantic 弃用警告。证据对应本次两个提示词文件的工作区修改。
既有测试覆盖目录引导注入、工具检索与 Rail 行为；人工核对中英文规则及工具描述一致。
未新增复述文案的字符串断言，未执行格式化或静态检查。

## 已知限制

此次为提示词约束，未新增运行时强制调用机制，也未执行真实模型调用率对比评测。
单测通过不能证明模型始终遵循先检索再执行，实际调用频率仍需任务运行观察。
