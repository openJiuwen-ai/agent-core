# Block C：成员目录链接器（member-directory linker）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-20 |
| 范围 | 新增 `agent_teams/team_workspace/paths.py`（真实目录公式纯函数）、`dir_links.py`（symlink → junction 链接原语）、`ref_store.py`（`.refs.json` 引用计数）、`binder.py`（建真实目录 + link + refs）、`assembler.py`（mode 判定 + binder 组合）；改 `agent_teams/agent/agent_configurator.py`（workspace 装配替换 `ensure_team_member_workspace_link` 调用点）、`tools/team.py`（`_remove_cleanup_paths` 链接感知）、`schema/blueprint.py` + `schema/team.py`（`member_workspace_prefix` 开关）、`team_workspace/__init__.py`（导出）；`docs/specs/S_13` 修订；测试 `tests/unit_tests/agent_teams/team_workspace/test_workspace_paths.py`、`test_dir_links.py`、`test_ref_store.py`、`test_binder.py` + `test_team.py` 链接感知追加 |
| 测试基线 | Block C 单测 28 passed / 0 failed；装配级 ST（`block_c_workspace_st.py`，未提交）17 passed；冒烟验证 35 checks |
| Refs | 设计文档 `doc/analysis/evolvable-team/design-v5/2026-08-20-block-c-topology-v3.md` |

## 背景

### 症状

成员 workspace 的真实目录一直建在 team 树内（`<team>/workspaces/{member}_workspace/`）。
同一预配置成员被多个 team 复用时，各 team 各建一份、互相漂移；动态成员则没有
per-team 隔离。Block C 把成员真实目录**扁平化到 team 树外**，在 team 内原路径建
link（symlink / Windows junction）指过去，配合 `.refs.json` 引用计数做跨 team 共享复用。

### 定位

C = **成员目录链接器**，是 A（prompt/tool 演进）/ B（DB 长文本 spill-to-file）之后的
第三块。核心约束（用户裁决）：

1. **A/B 零感知**：`workspace_store` / `assembler` / `workspace_cache` 一概不改，
   继续用 `team_member_workspace_dir`。link 是透明层，把读写映射到 team 外真实目录。
2. **root 恒 team 内路径**：装配返回 `team_member_workspace_dir`，不做二次解析。
3. **兜底 = 退回 team 内**：link 建不出（EACCES/EPERM / junction 失败）→ 真实目录
   建在 team 内。两种情况下 team 内路径都有效，A/B 因此零感知。
4. **不新增转发方法**：link 路径直接用已有 `paths.team_member_workspace_dir`
   （`paths.py:154`，主干早就有）；删转发、删 `TeamWorkspacePaths` 类、删
   `resolve_member_access_root`（缺口 3）。
5. **`ensure_team_member_workspace_link` 原样保留**（worker 调用点照旧），仅
   configurator 装配点由 binder 取代。

## 三种成员模式的落盘

| 模式 | 真实目录 | team 内访问路径 |
|---|---|---|
| leader | `team_member_workspace_dir`（team 内，不拉平、不 link） | 就是真实目录 |
| predefined | `.agent_teams/<member>`（跨 team 共享，与动态齐平） | link → 真实目录 |
| dynamic | `.agent_teams/<team>#<member>/`（prefix 开）或 `.agent_teams/<member>/`（prefix 关） | link → 真实目录 |

- link 成功 → team 内路径是 link，透明映射到 team 外。
- link 失败 → 真实目录建在 team 内，team 内路径就是真实目录。
- **两种情况下 `team_member_workspace_dir` 都有效 → A/B 零感知、零改动。**
- **按成员 role 白名单判定是否 link 出去**（`binder.prepare_member_workspace`）：
  只有 `TEAMMATE` 与 `HUMAN_AGENT` 走 dynamic（link 出去）；`LEADER`（含按名判定的
  leader）留 team 内、`predefined` 走跨 team 共享、其余 role（`EXTERNAL_CLI`、
  `BRIDGE_AGENT`、`WORKER`、未知）一律退回 team 内真实目录，不 link 出去。外部 CLI 成员
  另有 `setup_agent` 的 `member_runtime is not None` 提前返回兜底，根本不触达装配段；
  白名单是 binder 直接看到任意 role 时的纵深防御。

## 为什么这样长这样（决策记录）

- **纯函数路径公式而非类**：旧实现 `TeamWorkspacePaths` 类是 `agent_teams/paths.py`
  的转发壳，被否决。v3 把真实目录公式收敛为 `team_workspace/paths.py` 的纯函数
  （`member_dir_name` / `member_real_dir`），link 路径从不在此转发。
- **删除缺口 3（`resolve_member_access_root`）**：一旦兜底是"退回 team 内"而不是
  "跑 team 外"，team 内路径永远有效，store 无需感知 link，缺口 3 从根上消失。
- **refs 定位带 mode**：predefined 成员的真实目录在 `.agent_teams/<member>`，refs
  查询/释放必须带 `mode` 才能定位 `.refs.json`（`binder.release(..., mode=...)`）。
- **refs 只属于 link-out 成员**：只有真实目录被 link 出 team 树、且可能跨 team 共享
  的成员才写 `.refs.json` —— 即 `dynamic`（teammate / human_agent）与 `predefined`。
  `leader` / `external_cli` / 其余 role 的真实目录在 team 树内、不 link 出去、不跨 team
  共享，**不写 `.refs.json`**：它随 team 树 `shutil.rmtree` 一起回收，ref 是只写不读的
  死文件（`delete_if_zero` 对非 dynamic 永远 False，cleanup 扫描也不进入 team 树内）。
- **动态成员 per-team 隔离**：`team#member` 是两个 team 各自的真实目录，refs 不跨
  team 累计；跨 team 共享只发生在 predefined（`.agent_teams/<member>`，refs 累计 teams）。
- **链接感知清理**：junction 若被 `shutil.rmtree` 会下钻删 target 内容（共享资产）。
  `_remove_cleanup_paths` 先 `is_dir_link` → `remove_dir_link`，非链接才 rmtree。
- **每成员按 role 白名单自判**：binder 只对**当前 configure 的成员**按 role 白名单判定——
  该成员是 `TEAMMATE`/`HUMAN_AGENT` 才建 team 外真实目录 + link，其余（leader /
  `EXTERNAL_CLI` / `BRIDGE_AGENT` / `WORKER`）一律 team 内真实目录，不扫别人的目录、不
  全队扫描、不依赖黑名单。成员真实目录创建只由 `prepare_member_workspace`（即 `MemberWorkspaceBinder.setup`）负责，不再有独立的全队迁移器。
- **cleanup_team 从 link 反向解析真实目录**：`MemberWorkspaceBinder.cleanup_team(team_name)`
  是删 team 前的一站式清理，**从 team 树内 link 反向解析真实目录**：遍历
  `{team_home}/workspaces/<member>_workspace`，对每个 link `os.readlink` 拿真实目录 →
  读其 `.refs.json` → `remove_ref(本 team)` → count 归零且 `kind == dynamic` 才
  `shutil.rmtree` 真实目录，`predefined` 只删 link（共享资产保留），无 refs / team 不在
  refs 列表仅删 link。**不遍历 `get_agent_teams_home()`、不依赖 `<team>#` 目录名前缀** ——
  成员→真实目录的映射始终来自 team 树内 link，因此 `member_workspace_prefix=False`
  （真实目录为无前缀 `.agent_teams/<member>`）与 prefix 开一样被正确回收。link 失败
  retreat 回退的真实目录（普通目录，在 team 树内）随 `cleanup_team` 直接 rmtree。必须在
  `shutil.rmtree(team_home)` 之前调（否则 junction 下钻 + 孤儿真实目录残留）。

## 验证基线

- 单测 28 个（`team_workspace/` 5 文件 + `test_team.py` 链接感知 2 个；含 role 白名单 + 外部 CLI 不被 link 出去回归 2 项）。
- 装配级 ST 17 项（真实 `TeamAgentSpec` 数据流 → 真实装配 → 文件系统断言，无 API key）。
- 冒烟 35 checks（隔离 home，覆盖建链 / 复用 / 释放 / 清理全链路）。

## 已知遗留

1. 兜底（link 建不出）下预配置成员失去跨 team 复用，退化为 team 内私有目录（可接受）。
2. 外部 CLI 成员（codex / claude）无 link：`setup_agent` 对 `member_runtime is not None`
   提前返回，不触达装配段；即便绕过早返回，binder 的 role 白名单也会把 `EXTERNAL_CLI`
   归入 team 内真实目录（兜底退回），不会被别的成员 configure 扫描时 link 出去。
3. WORKER（swarmflow）保持现役 `ensure_team_member_workspace_link`，不参与 binder/refs。
4. distributed workspace（git push/pull）跨节点一致性只保证 LOCAL 模式。

## 后续修复（2026-08-26）

初版 `cleanup_team` 先无差别删光 `workspaces/` 下 link，再按 `<team>#` 前缀扫
`.agent_teams/` 释放动态真实目录 —— 先删 link 等于扔掉成员→真实目录映射，逼自己靠
目录名前缀猜，导致两个缺口（commit `3bc6ac743`）：

- `member_workspace_prefix=False` 的动态成员真实目录（`.agent_teams/<member>`，无 `#`）
  不在前缀扫描范围，删 team 时残留 + `.refs.json` 悬空。
- leader / external_cli 被 `_setup_leader` 写了只写不读的死 `.refs.json`（真实目录在 team
  树内、无 link、不共享）。

修复：`cleanup_team` 改为从 team 树内 link 反向解析（`os.readlink`）真实目录，按
`.refs.json` 内容释放，不扫 team 外、不依赖前缀；`_setup_leader` 去掉 `add_ref`。
删除 `cleanup_team_links` / `cleanup_team_dynamic_members` / `release_predefined_refs`
（从-link-解析统一取代，无生产调用者）。回归测试：`test_binder.py` + `test_manager.py`
167 passed。
