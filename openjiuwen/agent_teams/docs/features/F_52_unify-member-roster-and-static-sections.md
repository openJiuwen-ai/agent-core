# 统一成员名册 + 团队 section 全静态化

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-07-06 |
| 范围 | `prompts/sections.py`（删 `team_hitt_roster` / `_hitt_roster_*` / `_format_human_agent_roster` / `_format_bridge_agent_roster` / `_bridge_template_name`；HITT collapse 成单个 `build_team_hitt_section`，gate 改 `hitt_enabled`；`build_team_bridge_section` 仅 BRIDGE_AGENT 自契约；`build_team_members_section` 加 `mark_humans`；`build_team_static_sections` / `build_team_member_system_prompt` 去 `bridge_agent_names`、`human_agent_names`→`hitt_enabled`；`peers`→`self_line` 改名）；`prompts/{cn,en}/*.md`（删 `bridge_leader`/`bridge_teammate` 4 个；`bridge_agent` 去 `{{roster}}`；HITT 契约"名册单独提供"→引用 `[human]`；attachment_notice 去 `team_hitt_roster`）；`rails/team_policy_rail.py`（HITT 契约进 `_static_sections` gated on `hitt_enabled()`，`_sync_dynamic_sections` 只剩 members+info attachment、不碰 builder）；`spawn/external_cli_spawn.py`（传 `hitt_enabled`）；测试 `test_hitt.py` / `test_bridge_section.py` / `test_team_policy_rail.py` |
| 测试基线 | `tests/unit_tests/agent_teams/` 1714 passed / 16 skipped |
| Refs | #751 |

## 背景

[[F_50]] 把 `team_hitt` 拆成"静态契约（builder）+ 动态人类名册 `team_hitt_roster`（attachment）"，
随后曾评估把 bridge 也做成对称的动态段 + 名册拆分。审视时发现更根本的收敛点：

- **平行名册冗余**。`team_members`（attachment，本就动态）已经包含**全部**成员——human_agent /
  bridge_agent / external-cli 都是 `create_member` 的行，`list_members()` 全都返回。再单独维护
  `team_hitt_roster`（人类名单）和拟建的 `team_bridge_roster`（桥接名单）是把同一份数据切三份。
- **bridge/cli 的特殊性不该被 peer 感知**。从其它成员视角，bridge / external-cli 就是普通 teammate；
  `bridge_leader` / `bridge_teammate` 这两段"专门告诉 leader/teammate 这些是桥接成员"的说明段，与
  "不感知特殊性"自相矛盾。
- **动态内容混进了静态段**。`build_team_static_sections` 里混着 `team_hitt_roster`（人类名单）和
  bridge 的 `{{roster}}`（桥接名单）——这些是动态成员列表，不该在静态段。且 F_50 让 rail 每轮把
  HITT 契约 upsert 进 `system_prompt_builder`，违反"静态进 builder、动态进 attachment"的干净分层。

## 决策

### D1：统一名册 + `[human]` 标记，删 `team_hitt_roster`
- 所有成员平等呈现在 `team_members`（attachment，动态）。`build_team_members_section` 加 `mark_humans`：
  为 `role == human_agent` 的成员追加 `[human]` 标记。bridge / external-cli **不标记**（行为与普通
  teammate 无异，无需 peer 感知）。
- 标记可见性沿用 `expose_human_agents_to_teammates`（F_18 隐私）：LEADER / HUMAN_AGENT 恒见，
  TEAMMATE 仅 expose 时见。rail 在 `_fetch_and_build_members_section` 算 `mark_humans`。
- 删 `TeamSectionName.HITT_ROSTER` / `build_team_hitt_roster_section` / `_hitt_roster_body` /
  `_format_human_agent_roster`。HITT 契约模板"名册单独提供"改为"在 `team_members` 名册中标记为
  `[human]`"。

### D2：bridge 收敛为 avatar 自契约
- 删 `bridge_leader.md` / `bridge_teammate.md`（cn+en 4 个）+ `_bridge_template_name` /
  `_format_bridge_agent_roster`。`build_team_bridge_section` 仅在 `role == BRIDGE_AGENT` 出
  `bridge_agent` 自契约（调度语义），其余角色返回 `None`。`bridge_agent` 模板去 `{{roster}}`（不再列
  其它 bridge），只保留 `{{self_line}}`。

### D3：HITT 契约静态化，`_sync_dynamic_sections` 不碰 builder
- HITT 契约是纯静态规则（人类名单已折进 `team_members`），gate 从"live 名册非空"改为
  `team_backend.hitt_enabled()`（sync capability flag，init 可取）。`build_team_hitt_section` 参数
  `human_agent_names`→`hitt_enabled: bool`，collapse 掉 `build_team_hitt_contract_section` /
  组合入口。HITT 契约进 `_static_sections`（rail init 建一次），HITT 一开即 present、无需先 spawn 人类
  （消除 F_46 注释里的 chicken-and-egg）。
- `_sync_dynamic_sections` 从此**只**把 `team_members` / `team_info` upsert 进 attachment，**不再有任何
  `system_prompt_builder` 操作**。`uninit` 只清 `_static_sections`（HITT 契约 / bridge 自契约都在其中）。

### D4：`peers`→`self_line` 改名 + 前置（同一工作单元的前置小重构）
- 自身名字行占位符 `{{peers}}`→`{{self_line}}`（对齐 `_self_member_line`，语义是"自身名字行"非
  "peers"）；`_hitt_contract_body` / `build_team_bridge_section` 仅在 HUMAN_AGENT / BRIDGE_AGENT 时才算
  `self_line`。`hitt_human_agent` 里 `{{self_line}}` 从第一段中间移到最前（标题下第一行）。

## 拒绝的方案
- **给 bridge 单独做动态段 + 契约/名册拆分（曾定的"B"）**：撤销。bridge 本就是 member、`team_members`
  已动态；再建平行的 `team_bridge_roster` 是给静态段做 split（负收益）。统一名册后 bridge 无需任何
  动态段。
- **保留 `team_hitt_roster`**：与 `team_members` 重复（人类本就在 `team_members`），改为 `[human]` 标记。
- **给所有成员标 role**：只标 human——human 有不同交互规则（不能 claim_task、要 leader 指派、可能沉默），
  leader 需要区分；bridge/cli 行为与普通 teammate 无异，标记只会泄漏无用的"特殊性"。
- **HITT 契约每轮刷新推 builder（F_50 路径）**：违反"静态进 builder、动态进 attachment"。gate 改
  `hitt_enabled()`（sync）后 HITT 契约可静态化，`_sync_dynamic_sections` 得以纯 attachment。
- **HITT 契约 gate 用 live 名册**：async、rail sync init 取不到，且有"HITT 开了但还没 spawn 人类→契约
  缺失"的 chicken-and-egg。`hitt_enabled()` 正为此设计。

## 验证
- 单测（按 CLAUDE.local.md 只跑 targeted pytest）：`test_hitt.py`（HITT 单契约、不列名字、expose 指向
  `[human]`）、`test_bridge_section.py`（仅 BRIDGE_AGENT 出自契约）、`test_team_policy_rail.py`
  （HITT 契约进 builder gated on `hitt_enabled`、`[human]` 折进 `team_members` 并受 expose 门控、
  `_sync_dynamic_sections` 只挂 members+info）全绿；全套件 `tests/unit_tests/agent_teams/`
  **1714 passed / 16 skipped**。
- 冒烟：external CLI 一次性 prompt 含 HITT 契约（hitt_enabled）+ `[human]` 引用、bridge 仅 avatar 出自
  契约、`team_members` 的 `[human]` 标记受 `mark_humans` 门控。

## 已知遗留
- `team_info` 仍每轮进 attachment（用户决策保留，未来元数据可能动态可变——见 [[F_46]] / 项目记忆）。
- human_agent 作为 external CLI 成员的场景无 e2e 覆盖（`is_human_agent` 分支已就位）。
- external e2e（真实 CLI + 凭证）未纳入本次自动化校验。
