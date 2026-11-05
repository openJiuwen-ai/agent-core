---
id: F_20261022_2319_inline-member-worktree-isolation
title: 调度模式内联成员支持 worktree 隔离
type: F
author: alan_cheng
author_email: chengshuo5@huawei.com
modules:
- openjiuwen/agent_teams
files:
- openjiuwen/agent_teams/tools/tool_task.py
- openjiuwen/agent_teams/tools/locales/cn.py
- openjiuwen/agent_teams/tools/locales/en.py
- openjiuwen/agent_teams/tools/locales/descs/cn/fragments/create_task_inline_member.md
- openjiuwen/agent_teams/tools/locales/descs/en/fragments/create_task_inline_member.md
designs: []
design_note: 对 F_118 内联成员参数遗漏的小范围补齐，复用既有 `isolation` 契约，无独立设计
---

# 调度模式内联成员支持 worktree 隔离

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-22 |
| 状态 | 已实现 |
| 适用范围 | **仅 `dispatch_mode == "scheduled"`** 的 `create_task` 内联 `member`（autonomous 形态本来就不接受 `member`） |
| 范围 | `tools/tool_task.py`（`_inline_member_schema` / `_validate_inline_members` / `_resolve_inline_experts` / `_materialize_inline_members`）、`tools/locales/{cn,en}.py`（`create_task_scheduled.task.member` 与新 key `create_task_scheduled.task.member.isolation`）、`tools/locales/descs/{cn,en}/fragments/create_task_inline_member.md`；文档：`docs/specs/S_08`（不变量 25）、`F_118` 勘误、`tools/AGENTS.md` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/` → 3953 passed / 36 skipped / 2 xfailed |
| Refs | #1435 |

## 背景

F_118 给调度模式 `create_task` 加了内联 `member`，声称"五个公开参数与 `spawn_teammate` 逐字相同"。
但 `spawn_teammate` 的 `isolation` 早在 2026-06（F_38）就已存在，F_118 落地时它其实有 7 个参数，
内联成员只取了 5 个，`isolation` / `permissions` 被漏掉，文档也没有留下拒绝理由。

后果：leader 想让"只为一个任务存在的专家"在隔离检出里改代码，只能退回 `spawn_teammate` +
`assignee` + 手动退场的三步仪式——恰好是 F_118 要消灭的那条流程。底层没有任何阻碍：
`tool_member.register_teammate` 本来就接受 `isolation` 并传给 `TeamBackend.spawn_member`，内联路径
只是调用时没传。

## 决策

### D1. `isolation` 进内联 `member` schema，取值与校验和 `spawn_teammate` 一致

- schema 加 `isolation: {"type": "string", "enum": ["worktree"]}`，描述走新 key
  `create_task_scheduled.task.member.isolation`。`spawn_teammate` 的 `isolation` 描述是硬编码英文、
  没有 locale key，可复用的只有 `spawn_expert.isolation`，但那条讲的是"外部运行时专家不支持"——
  内联语境要同时覆盖 `cli_agent`，所以单开一个 key，与 `create_task_scheduled.task.member.*` 其余字段同一命名空间。
- `_materialize_inline_members` 把 `member.get("isolation")` 透传给 `register_teammate`。合法性
  （`"worktree"` 或 None）与宿主能力（`validate_worktree_isolation` 未配置即失败）仍由
  `TeamBackend.spawn_member` 统一判，与 `spawn_teammate` / `spawn_expert` 走同一道闸。
- `_validate_inline_members` 在纯校验阶段额外拒绝非法取值——内联是批量写，后端那道检查发生在
  前面成员已落行之后（虽有回滚），能在写库前拦下的就在写库前拦。

### D2. 外部 CLI 内联成员带 `isolation` 在写库前拒绝

与 `spawn_expert._spawn_external` 同一规则（外部运行时不在 team worktree 里跑）：

- `cli_agent` 成员：`_validate_inline_members` 的纯校验与取值检查一起拒绝（不放进
  `_check_inline_external_cli`，以免它越过 pylint 的 return 数阈值）。
- 外部运行时专家：模板要解析后才知道运行时，放在 `_resolve_inline_experts`——它本来就是"所有专家模板在任何写入之前解析"的那一步，和它对 `external_cli` 的反向检查对称。

### D3. 不改 worktree 生命周期

动手前核对过 `lifetime` 与 worktree 的交互，结论是无需改动：

- 成员 worktree 的收尾只在**团队级**触发（任务清单清空的 `finalize_non_contributing_worktrees`、
  `clean_team` 前的 `finalize_all_member_worktrees_for_team_clean`），**成员退场（SHUTDOWN / DORMANT）不碰 worktree**。
- `dormant` 成员被点名唤醒时，`SpawnManager.build_context_from_db` → `ensure_member_worktree`
  复用仍在且 scope 匹配的目录；若中途已被团队级收尾删掉（干净无提交），元数据被清空、重新创建。
  有改动的 worktree 保留给 leader 合并——与 `spawn_teammate` 建的成员完全一样。

这正是 F_118 "内联成员与 `spawn_teammate` 建的成员在运行时没有任何区别" 的自然推论。

## 拒绝的方案

- **把 `permissions` 一起补上**：同样被 F_118 漏掉，但本次需求只针对 `isolation`，`permissions`
  的收紧语义与调度模式 reviewer / 交接是否有交互没评估过。按"只做被要求的"留作遗留。
- **只靠后端 `spawn_member` 校验取值、不在纯校验里拦**：结果正确（失败会回滚），但批量里前面的
  成员会先落行再删，多一轮无谓的写与补偿；与本文件其余"写库前纯校验"的风格不一致。
- **复用 `spawn_expert.isolation` 描述串**：文案只提"外部运行时专家"，对 `cli_agent` 内联成员是误导。

## 验证

- `tests/unit_tests/agent_teams/tools/test_tool_variants.py`：
  - `test_scheduled_create_task_inline_member_takes_worktree_isolation`：schema 暴露 enum、带
    `isolation` 的成员行 `options.worktree.isolation == "worktree"`、validator 只对它调用一次、同批不带的成员无 worktree 配置。
  - `test_scheduled_create_task_inline_isolation_fails_without_worktree_support`：宿主未配 validator 时整批失败、零残留。
  - `test_scheduled_create_task_rejects_malformed_inline_member` 新增非法取值用例。
- `tests/unit_tests/agent_teams/tools/test_tool_expert.py`：
  - `test_inline_cli_member_rejections_write_nothing` 新增 `cli_agent + isolation` 用例。
  - `test_inline_external_expert_rejects_isolation_like_spawn_expert`。
- 基线：`pytest tests/unit_tests/agent_teams/tools/{test_tool_variants,test_tool_expert,test_locales_variants,test_spawn_teammate_model_group}.py` → 209 passed；`pytest tests/unit_tests/agent_teams/` → 3953 passed / 36 skipped / 2 xfailed。

## 已知遗留

- 内联 `member` 仍不支持 `permissions`（`spawn_teammate` / `spawn_expert` 有）。
- `spawn_teammate.isolation` 的描述仍是硬编码英文，未进 locale；本次没动，以免扩大改动面。
