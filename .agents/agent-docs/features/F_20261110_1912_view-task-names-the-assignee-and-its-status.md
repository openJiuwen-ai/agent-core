---
id: F_20261110_1912_view-task-names-the-assignee-and-its-status
title: view_task 点名负责人及其成员状态
type: F
author: alan_cheng
author_email: chengshuo5@huawei.com
modules: [agent_teams]
files:
  - openjiuwen/agent_teams/tools/tool_task.py
  - openjiuwen/agent_teams/tools/task_manager.py
  - openjiuwen/agent_teams/schema/task.py
  - openjiuwen/agent_teams/tools/locales/descs/cn/task/view_task.md
  - openjiuwen/agent_teams/tools/locales/descs/en/task/view_task.md
designs: []
design_note: 小范围渲染与读取增强，方案在会话中与需求方确认，未单独立 D 文档
---

# view_task 点名负责人及其成员状态

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-11-10 |
| 范围 | `openjiuwen/agent_teams/tools/{tool_task,task_manager}.py`、`schema/task.py`、`tools/locales/descs/{cn,en}/task/view_task.md` |
| 关联 spec | S_08（不变量 14）、S_12（`TaskSummary` / `TaskDetail`） |
| 测试基线 | `tests/unit_tests/agent_teams/test_team_tools.py -k "ViewTask or view_task"` 12 通过 |

## 背景

leader 发现任务停滞时要回答两个问题：这个任务归谁，那个人现在在干什么。`view_task` 的数据里
一直有 `assignee`，但 list 档把它渲染成一个不带标签的括号，后面紧跟时间括号：

```
#t2 [in_progress] 修复登录 (dev-1) (2026-11-10 14:02:11 +08:00 (3 分钟前))
```

模型认不出 `(dev-1)` 是负责人，工具描述里也没有一句话告诉 leader 拿它去联系成员。第二个问题
则完全没有答案：leader 得再调一次 `list_members`，自己把两份结果按名字对上。

## 决策

1. **list 档给负责人加标签**：`(dev-1)` 改为 `assignee=dev-1`。get 档原本就是 `Assignee: dev-1`。
   对所有带 assignee 的任务统一生效，不按任务状态分支——scheduled 下"已指派未开工"的
   `PENDING` 任务同样有主。
2. **未结束的任务带上负责人的成员状态**：`TaskSummary` / `TaskDetail` 新增
   `assignee_status`，渲染为 `assignee=dev-1[busy]` / `Assignee: dev-1[busy]`。`busy` 说明有人在做，
   空闲或 `error` 才需要 leader 介入。
3. **状态在读取时取，不持久化**：`TeamTaskManager._member_statuses` 用一次名册投影
   （`member.get_member_roster`，只取三列）建 `成员名 → 状态` 表，list 与 get 共用。每次调用多
   一个与任务数无关的查询。
4. **已结束的任务不带成员状态**：`COMPLETED` / `CANCELLED` 的负责人是历史，旁边挂一个
   `[error]` 会被读成这个任务出了问题。判定在 `_open_assignee_status` 一处。负责人已不在名册
   时同样只留名字。
5. **描述补一条 leader 提示**：`view_task.md` 写明 assignee 的格式、成员名即 `send_message`
   收件人、各状态含义，以及"停滞时先看 assignee"的判据。

## 拒绝的方案

- **让 leader 自己再调 `list_members`**：能用，但每次排查都是两次工具调用加一次按名字对表，
  对表正是模型容易出错的地方。
- **把成员状态冗余写进任务行**：要在每次成员状态迁移时回写任务表，多一条必须保持一致的
  写路径；读时 join 一次名册投影便宜得多。
- **所有带 assignee 的任务都带状态**：实现少一个判断，但已完成任务上的成员状态是噪声，还会
  误导。
- **`action=in_review` 也带状态**：验证者看的是交付物，不需要作者的运行状态，不为它多付一次
  查询。

## 兼容性

list 档的面向模型文本从 `(name)` 变为 `assignee=name[status]`；`ToolOutput.data` 只新增可选
字段 `assignee_status`，既有字段不变。进程内成员、外部 CLI 成员与 MCP 共用同一个
`render_for_llm`，文本同步变化。

## 已知遗留

- 成员状态是读取时刻的快照，leader 读到 `busy` 后成员仍可能立刻出错；它是判断依据，不是
  保证。
- 没有做真实模型的对照实验来量化 leader 处理停滞任务的改善。
