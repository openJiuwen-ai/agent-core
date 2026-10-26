# HITT 契约/名册按生命周期拆分 + 补完 HITT/Bridge 外置化

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-07-03 |
| 范围 | `prompts/sections.py`（HITT 拆成 contract/roster/combined 三 builder + bridge 外置化 + 删 14 个内联函数 + `_hitt_template_name`/`_bridge_template_name`/`_self_member_line` + `TeamSectionName.HITT_ROSTER`）；`prompts/__init__.py`（导出）；`prompts/{cn,en}/hitt_*.md`（契约 roster-agnostic 化）；`prompts/{cn,en}/attachment_notice.md`（attachment type 改口径）；`rails/team_policy_rail.py`（HITT 契约→builder、名册/members/info→attachment 的路由拆分）；`tests/unit_tests/agent_teams/test_team_policy_rail.py`（HITT 拆分覆盖） |
| 测试基线 | `test_team_policy_rail.py` 34 passed（含新增 4）、`test_hitt.py` 61 passed、`test_bridge_section.py` 7 passed、`test_team_section_cache.py` 9 passed；`tests/unit_tests/agent_teams/` 全套件 1692 passed / 16 skipped |
| Refs | #751 |

## 背景

本特性修正 [[F_46]] 的信息分层，并补完提交 `a088ba39` 遗留的半成品外置化。

**痛点 1 — attachment 混入静态信息（F_46 遗留）。** F_46 把 `team_members` / `team_info` /
`team_hitt` 三个 section 一刀切当"动态"、全移到 per-round attachment。但三者真实易变性天差地别：
`team_members` 每次 spawn 变（真高频），`team_info` 建队后恒定（`TeamDao` 无 update 路径），而
`team_hitt` 是**静态协作契约 + 动态人类成员名册**的混合体。attachment 每轮无条件重渲染 + 追加到
消息尾部（哪怕内容一字未变、用完即弃），于是 `team_hitt` 那段最靠前（P:12）、约 1.5KB 的**静态
规则**每轮都在消息尾部被重发一遍，既浪费 token，又稀释了 attachment 本应传达的"这是当前变化
状态"信号。

**痛点 2 — `sections.py` 与 md 逐字重复（a088ba39 遗留）。** 提交 `a088ba39` 声称把 HITT/Bridge
外置到 md 模板，实际是半成品：建了 14 个 `hitt_*.md`/`bridge_*.md`（cn/en）+ 更新了文档，但
`build_team_hitt_section` / `build_team_bridge_section` 仍调 `sections.py` 里 14 个内联字符串函数、
从不 `load_template`。md 是**从未被加载的孤儿死文件**，与内联字符串逐字重复。`prompts/AGENTS.md`
里 `{{roster}}`/`{{peers}}`、`load_template(...).format(...)`、`_hitt_template_name` 全是"超前于代码"
的描述。只有 `attachment_notice` / `inbound_tags` 真正外置了。

## 决策

### D1：`team_hitt` 按信息生命周期拆成契约 + 名册

- **静态契约（协作规则）→ system prompt builder（P:12）**：`build_team_hitt_contract_section`
  从 `hitt_*.md` 契约模板渲染，roster-agnostic（不列人类成员名字），只注入 `{{peers}}`（自身
  名字，恒定）。它每轮仍从 members 探针缓存刷新，但内容仅在**新增 human agent**时才变，所以前缀
  在常见场景字节稳定、KV 命中；仅那一次罕见变更才正确失效一次。
- **动态名册（人类成员名单）→ attachment（`type=team_hitt_roster`）**：`build_team_hitt_roster_section`
  用既有 `_format_human_agent_roster` 生成名字串。anonymous teammate（本就无名册）与无 human agent
  时返回 None。
- `TeamPolicyRail._sync_dynamic_sections`（原 `_sync_dynamic_attachments`）把契约 upsert 进
  `system_prompt_builder`（None 则 `remove_section`），名册/`team_members`/`team_info` upsert 进
  attachment。契约 + 名册共用 `_refresh_member_sections` 的**同一次** members 探针，拆分零额外查询。
- `uninit` 追加移除 builder 里的 `HITT` 契约（builder-bound 动态 section，不在 `_static_sections`）。

### D2：`team_info` / `team_members` 保持在 attachment（不动）

- 原计划一并把恒定的 `team_info` 移回 system prompt。**经用户决策保留在 attachment**：团队元数据
  当前恒定，但未来可能动态可变（改名 / 改 desc 等），留在 attachment 免得将来再改架构。`team_members`
  本就是真高频，留在 attachment 无争议。本次二者路由完全不动。

### D3：补完 HITT/Bridge 外置化，md 为唯一真相源

- `build_team_hitt_contract_section` / `build_team_hitt_roster_section` / `build_team_bridge_section`
  改走 `load_template(name, lang).format({...}).content`；新增 `_hitt_template_name(role, expose)` /
  `_bridge_template_name(role)` 选择器与 `_self_member_line`（`{{peers}}` 生成）。删掉 8 个
  `_hitt_section_*` + 6 个 `_bridge_section_*` 内联函数。bridge 是静态 section（建队冻结），**不拆**，
  仅完成外置化——输出与旧内联逐字一致（md 是 verbatim 拷贝，`test_bridge_section.py` 免改即过）。
- 保留组合入口 `build_team_hitt_section`（= 契约段 + 名册段拼一段），供**外部 CLI 成员**
  （`build_team_static_sections` → `build_team_member_system_prompt`，无 attachment 通道）与
  `test_hitt.py`。三 builder 共用同一份契约模板 + 同一个名册生成器，零模板重复。
- `attachment_notice.md`（cn/en）改口径：attachment type = `team_members` / `team_info` /
  `team_hitt_roster`；HITT 协作规则在系统提示词里、稳定不变。

## 拒绝的方案

- **整段 HITT 移回 builder 不拆**：契约 + 名册整段进 P:12 builder，新增 human agent 时会失效
  P:12 之后整段前缀。名册变化虽罕见，但拆分后只动尾部 attachment、永不失效前缀，更干净。用户
  明确选择拆分。
- **反向删 md、保留内联 Python**：能去重，但与本模块"md 是行为契约、改提示词不动 Python"的既定
  模式（role/workflow/lifecycle/attachment_notice 全走 md）相悖，还要回滚 a088ba39 已更新的文档。
- **把 `team_info` 一并移回 system prompt**：技术上可行（`TeamDao` 无 update 路径、可证恒定），但
  用户预判团队元数据未来会动态可变，保留在 attachment 避免二次改架构（见 D2）。
- **给名册单独建 md 模板**：名册是 `_format_human_agent_roster` 纯生成的一行字符串，无需模板；
  契约模板只保留规则正文。

## 验证

- 单测按 CLAUDE.local.md 约定只跑 targeted pytest（不 make check / ruff / mypy）：
  `test_team_policy_rail.py` 34、`test_hitt.py` 61、`test_bridge_section.py` 7、`test_team_section_cache.py`
  9 全绿；`tests/unit_tests/agent_teams/` 全套件 1692 passed / 16 skipped。
- 冒烟脚本核验：leader 契约含规则不含名字、名册仅名字、组合入口两者皆含、anonymous teammate 无
  名册、human_agent 契约含 `{{peers}}` 自身名字不含 roster、bridge cn/en 的 `{{roster}}`/`{{peers}}`
  正确注入。
- 干净树与本分支两次全套件均 1692 passed；曾偶发的 3 个 `reliability/test_integration.py` 失败经
  确认是全套件 async teardown 的 flaky（`Event loop is closed` 竞态，单独跑全过），与本改动无关。

## 已知遗留

- **KV-cache 实际收益**需真实多轮场景评测：HITT 静态规则从每轮尾部 attachment 移进缓存前缀，理论
  上省 token 且稳前缀，但需观测"人类成员协作规则"类问题不退化。
- `reliability/test_integration.py` 在全套件下的 async teardown flaky 属既有问题，非本次引入，未处理。
- 若后续确认 `team_info` 恒定且不会动态化，可考虑随 HITT 契约一并移回 system prompt（当前按用户
  决策保留在 attachment）。
