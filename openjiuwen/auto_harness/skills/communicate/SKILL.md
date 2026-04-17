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
- 不在这里决定提交边界；边界由 implement skill + commit skill 控制

## PR 描述模板

PR / MR 描述遵循 `$git-commit-push` skill 中的 GitCode 模板，不再自定义另一套简化格式。

创建 MR 时 body 使用以下模板，并根据实际情况填写 `/kind` 标签、概述、变更内容、验证结果和 checklist：

```markdown
<!--  Thanks for sending a pull request!  Here are some tips for you:

1) If this is your first time, please read our contributor guidelines: https://gitcode.com/openJiuwen/community/blob/master/CONTRIBUTING.md

2) If you want to contribute your code but don't know who will review and merge, please add label `openjiuwen-assistant` to the pull request, we will find and do it as soon as possible.
-->

**What type of PR is this?**
<!--
选择下面一种标签替换下方 `/kind <label>`，可选标签类型有：
- /kind bug
- /kind task
- /kind feature
- /kind refactor
- /kind clean_code
如PR描述不符合规范，修改PR描述后需要/check-pr重新检查PR规范。
-->
/kind <label>

## 概述
<简要描述变更内容和原因>

## 变更内容
<列出主要改动点>

## 验证结果
<描述测试/验证方式和结果>

**Self-checklist**:（**请自检，在[ ]内打上x，我们将检视你的完成情况，否则会导致pr无法合入**）

+ - [ ] **设计**：PR对应的方案是否已经经过Maintainer评审，方案检视意见是否均已答复并完成方案修改
+ - [ ] **测试**：PR中的代码是否已有UT/ST测试用例进行充分的覆盖，新增测试用例是否随本PR一并上库或已经上库
+ - [ ] **验证**：PR描述信息中是否已包含对该PR对应的Feature、Refactor、Bugfix的预期目标达成情况的详细验证结果描述
+ - [ ] **接口**：是否涉及对外接口变更，相应变更已得到接口评审组织的通过，API对应的注释信息已经刷新正确
+ - [ ] **文档**：是否涉及官网文档修改，如果涉及请及时提交资料到Doc仓
```

Kind 标签选择：

- `bug`：修复 bug
- `task`：任务类改动
- `feature`：新功能
- `refactor`：重构
- `clean_code`：代码清理

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
