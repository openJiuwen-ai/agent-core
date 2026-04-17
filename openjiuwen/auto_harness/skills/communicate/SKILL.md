---
name: communicate
description: 沟通规范 — 约束 commit message、PR、journal 和求助信息的表达方式
immutable: true
tools:
  - bash_tool
  - experience_search
---

# Communicate Skill

你是沟通阶段的 agent，负责撰写高质量的技术沟通内容。

## Commit Message 规范

使用 conventional commits 格式：

```
<type>(<scope>): <subject>

<body>

<footer>
```

| type | 用途 |
|------|------|
| feat | 新功能 |
| fix | 修复 bug |
| refactor | 重构（不改变行为） |
| perf | 性能优化 |
| test | 测试变更 |
| docs | 文档变更 |
| chore | 构建/工具变更 |

规则：
- subject 不超过 50 字符，使用祈使语气
- body 说明 **为什么** 而非 **做了什么**
- 不在这里决定提交边界；边界由 implement skill + commit guard 控制

## PR 描述模板

```markdown
## Summary

- 变更目的（1-2 句话）
- 关键设计决策

## Changes

- 按模块列出主要变更
- 标注破坏性变更（如有）

## Test Plan

- [ ] 单测通过
- [ ] lint 通过
- [ ] 类型检查通过
- [ ] 手动验证步骤（如有）

## Related

- 关联 issue/任务编号
```

## Journal 记录

每个 session 结束时记录：

```markdown
## Session Journal

### 完成
- 任务列表及结果

### 未完成
- 剩余任务及原因

### 经验
- 本次 session 的关键发现
- 可复用的模式或需避免的陷阱

### 下一步
- 后续 session 的建议行动
```

## 跨 Session 接力

当任务无法在当前 session 完成时：

1. 创建 issue 描述剩余工作
2. 在 journal 中标注 `接力: <issue-id>`
3. issue 内容包含：
   - 当前进度
   - 已完成的步骤
   - 下一步具体操作
   - 相关文件列表

## 求助机制

遇到以下情况时主动求助：

1. **不确定**: 修改可能影响公共 API
2. **超出范围**: 任务需要修改架构红线内的文件
3. **反复失败**: 同一操作失败 3 次以上
4. **安全风险**: 发现潜在安全问题

求助格式：

```
[HELP] <类别>

问题: <简要描述>
上下文: <相关文件和行号>
已尝试: <已尝试的方案>
建议: <你认为的解决方向>
```

## 输出质量要求

- 使用中文撰写，技术术语保留英文
- 简洁明了，避免冗余描述
- 包含足够上下文让读者理解变更意图
- 代码引用使用 `file:line` 格式
