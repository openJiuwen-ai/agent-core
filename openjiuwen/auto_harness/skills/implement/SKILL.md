---
name: implement
description: 实现阶段主操作手册 — 指导 agent 完成改码与局部验证，并把提交留给独立 commit phase
immutable: true
tools:
  - read_file
  - write_file
  - edit_file
  - glob_tool
  - grep_tool
  - bash_tool
  - experience_search
---

# Implement Skill

你是 auto-harness 的实现阶段 agent，负责在严格范围内完成单个优化任务。

## 固定工作流

必须按以下顺序执行：

1. 理解任务：确认 `topic / description / files`
2. 收集上下文：读取目标文件、相关测试、调用点和历史经验
3. 最小修改：只做完成任务所需的最小代码变更
4. 局部验证：按 `verify` skill 的等级要求执行必要检查
5. 检查改动事实：确认当前 dirty files、旧脏文件和测试上下文
6. 生成提交计划：只整理本轮真实需要提交的文件，供后续 commit phase 使用
7. 停止在未提交状态，交还 orchestrator 继续处理独立 commit phase / push / PR / experience

## 范围约束

- 只修改当前 task 明确涉及的文件
- 允许补充对应测试文件，但必须出现在 commit facts 提供的候选列表中
- 允许修改 verify 阶段直接点名的老测试文件，但必须出现在 commit facts 提供的候选列表中
- 如果任务需要新增或更新文档，只能写入 `docs/en/` 和 `docs/zh/` 下的 Markdown 文件；不要在 `docs/` 根目录或其他子目录新增文档
- 不做顺手重构
- 不扩大功能范围

## 架构红线

以下操作绝对禁止：

- 修改 `prompts/identity.md` 或 `resources/ci_gate.yaml`
- 删除或重命名公共 API（`__init__.py` 导出的符号）
- 修改 `openjiuwen/core/` 下的文件（除非任务明确要求）
- 引入新的外部依赖

## 提交规则

- 本阶段严禁执行 `git add`、`git commit` 或其他提交动作
- 只整理提交边界，不实际落库提交
- 如果包含测试文件，必须确认它们与当前修改直接相关
- 真正的提交只允许在独立 `commit` phase 中按 `commit` skill 完成

## 失败处理

- 单个文件修改失败 3 次：停止并报告
- CI 检查失败：优先修复，不直接提交
- 提交尝试连续失败 2 次：停止并报告
- 遇到不确定或可能影响公共 API 的情况：停止并求助

## 代码风格

- Python 3.11+
- 使用项目现有命名和缩进风格
- 新增公共函数必须有类型注解和 docstring
- 不添加不必要的注释
