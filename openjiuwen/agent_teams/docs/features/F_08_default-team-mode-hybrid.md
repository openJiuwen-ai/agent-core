# 默认 team_mode 从 predefined 改为 hybrid

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-05-14 |
| 范围 | openjiuwen/agent_teams/agent/agent_configurator.py、schema/blueprint.py |
| 测试基线 | `pytest tests/unit_tests/agent_teams/test_hitt.py test_predefined_team.py test_team_agent_tools.py` → 75 passed |
| Refs | #751 |

## 背景

`TeamAgentSpec.team_mode` 为 `None` 时，`_resolve_team_mode` 自动派生模式。
旧逻辑：`predefined_members` 含非 HUMAN_AGENT 成员 → `predefined`，否则 `default`。

`predefined` 模式会把 `spawn_member` 从 Leader 工具集移除（`agent_configurator.py`
的 `exclude` 计算）。这意味着只要用户声明了一个预定义 teammate，团队就被锁死成
固定编制——哪怕用户只是想"预置几个基础成员、运行时还能按需扩员"，也必须显式写
`team_mode="hybrid"` 才能拿回 `spawn_member`。

默认值选择了限制性更强的一侧，与 `predefined_members` 这个字段名给人的直觉
（"预置"而非"封死"）不一致。

## 决策

把派生默认值从 `predefined` 改成 `hybrid`：

- `_resolve_team_mode`：非 HUMAN_AGENT predefined 非空时返回 `"hybrid"`（原 `"predefined"`）。
- `default` / 显式 `predefined` / 显式 `hybrid` 三条路径不变；显式 `team_mode` 永远
  优先，不被重新派生。
- HUMAN_AGENT-only roster 仍派生 `default`（HITT 不变）。

效果：预置成员的团队默认保留 `spawn_member`，Leader 既能用预定义成员、也能动态扩员。
要锁死固定编制的用户改为显式声明 `team_mode="predefined"`——把"封死"这个更强的
约束交给显式意图，而不是当作隐式默认。

文档同步：`schema/blueprint.py` docstring、`docs/specs/S_07`、`S_12`、
`docs/designs/architecture_cn.md`、`interaction/CLAUDE.md`。

## 拒绝的方案

- **保留 `predefined` 默认、只在文档里强调**：治标不治本，默认值本身就违反字段名直觉，
  文档补丁挡不住每个新用户踩坑。
- **新增 `auto` 显式枚举值替代 `None` 派生**：徒增一个枚举值和一层映射，派生逻辑本质
  没变，只是把问题挪了个名字。`None` 派生机制本身没问题，错的只是派生出的默认值。

## 验证

- `test_hitt.py`：`test_resolve_team_mode_hybrid_when_non_human_member` /
  `test_resolve_team_mode_hybrid_with_mixed_roster` 断言改为 `"hybrid"`；
  新增 `test_resolve_team_mode_explicit_predefined_overrides_derivation` 守住
  "显式 team_mode 不被重新派生"。
- `test_predefined_team.py` / `test_team_agent_tools.py`：未受影响（这些用例显式
  传 `exclude_tools` / `team_mode`，不依赖默认派生）。
- 75 passed。

## 已知遗留

- 无。`predefined` 模式本身的行为（移除 `spawn_member`、`leader_workflow_predefined.md`
  模板）保持不变，只是不再是隐式默认。
