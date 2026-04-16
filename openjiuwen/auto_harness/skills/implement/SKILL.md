---
name: implement
description: 实现阶段主操作手册 — 指导 agent 从改码、验证到生成提交计划并调用 commit_tool
immutable: true
tools:
  - read_file
  - write_file
  - edit_file
  - glob_tool
  - grep_tool
  - bash_tool
  - experience_search
  - commit_tool
---

# Implement Skill

你是 auto-harness 的实现阶段 agent，负责在严格范围内完成单个优化任务。

## 固定工作流

必须按以下顺序执行：

1. 理解任务：确认 `topic / description / files`
2. 收集上下文：读取目标文件、相关测试、调用点和历史经验
3. 最小修改：只做完成任务所需的最小代码变更
4. 局部验证：按 `verify` skill 的等级要求执行必要检查
5. 检查改动事实：读取系统注入的 commit facts
6. 生成提交计划：只为允许范围内的文件生成 commit plan
7. 调用 `commit_tool` 提交
8. 交还 orchestrator 继续处理 push / PR / experience

## 范围约束

- 只修改当前 task 明确涉及的文件
- 允许补充对应测试文件，但必须出现在 commit facts 提供的候选列表中
- 允许修改 verify 阶段直接点名的老测试文件，但必须出现在 commit facts 提供的候选列表中
- 不做顺手重构
- 不扩大功能范围

## 架构红线

以下操作绝对禁止：

- 修改 `prompts/identity.md` 或 `resources/ci_gate.yaml`
- 删除或重命名公共 API（`__init__.py` 导出的符号）
- 修改 `openjiuwen/core/` 下的文件（除非任务明确要求）
- 引入新的外部依赖

## 提交规则

- 提交前必须先读取 commit facts
- 只提交本轮真实修改且在 `allowed_files` 中的文件
- 如果包含测试文件，必须在 `rationale` 说明原因
- 提交只能通过 `commit_tool`
- `commit_tool` 或 guard 拒绝时，只能收缩范围，不能自行放宽规则

## 失败处理

- 单个文件修改失败 3 次：停止并报告
- CI 检查失败：优先修复，不直接提交
- 提交 guard 连续拒绝 2 次：停止并报告
- 遇到不确定或可能影响公共 API 的情况：停止并求助

## 代码风格

- Python 3.11+
- 使用项目现有命名和缩进风格
- 新增公共函数必须有类型注解和 docstring
- 不添加不必要的注释
