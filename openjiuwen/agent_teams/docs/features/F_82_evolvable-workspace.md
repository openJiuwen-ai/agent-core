# 可演进 workspace（A 提示词 / B DB 值 / C tool 描述 三类文本演进）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-17 |
| 范围 | `openjiuwen/agent_teams/team_workspace/{assembler,workspace_store,workspace_cache,frontmatter,layout}.py`（新增/重构）、`openjiuwen/agent_teams/prompts/loader.py`（`make_template_loader`）、`openjiuwen/agent_teams/tools/locales/__init__.py`（`make_translator` 加 cache 参数）、`openjiuwen/agent_teams/agent/agent_configurator.py`（`_assemble_member_workspace`）、`openjiuwen/agent_teams/schema/blueprint.py` + `schema/team.py`（`evolution_enabled`）；测试 `tests/unit_tests/agent_teams/team_workspace/test_{frontmatter,layout,workspace_store,workspace_cache,workspace_assembler}.py` |
| 测试基线 | 新增 UT 全过；ST 在独立分支上验证，不进本分支 |
| Refs | `specs/S_24_evolvable-workspace.md` |

## 背景

成员 workspace 中模型可见的文本有三类：**A 提示词模板**（`prompts/<lang>/*.md`）、**B DB 值**（`team_member`/`team_info` 行的 `desc`/`prompt` 列）、**C tool 描述**（`locales/descs/<lang>/` md + `STRINGS` dict）。三个问题：

- **演进方不可见**：值散在代码常量、DB 列、md 文件里，演进方（人或自演进程序）没有统一的编辑面。
- **改框架默认即改行为**：代码升级会静默覆盖演进方的定制。
- **回退无门**：想回到框架默认只能改代码。

本特性把三类文本统一为 `YAML frontmatter + body` 文件落盘到 team-workspace 目录：装配阶段写框架基线，演进方改文件 body、下次 run 生效（Runner 边界失效 + lazy 重读，运行期不热更新）。删文件回代码默认；body hash 与基线一致（未演进）的文件不认演进，框架默认变了自动升级。

## 数据结构 / 状态机

### 统一文件形态

```text
---
kind: prompt            # prompt | member | team | tool | tool_params
name: leader_bootstrap  # 文件标识
language: cn            # 语言
baseline_sha256: <hash> # 落盘时 body 的 sha256
evolved: false          # 落盘时恒 false；读侧按 hash 比对判断
---
<body>
```

### 三类落盘位置（`WorkspaceLayout` 常量，单一真相）

| 类 | 框架源 | workspace 目标 | frontmatter kind |
|---|---|---|---|
| A 提示词 | `prompts/<lang>/*.md`（rglob 全量） | `<team>/team-workspace/prompts/system/<name>.<lang>.md` | `prompt` |
| B member | DB 列（desc/prompt） | `<member_dir>/prompts/identity/card.md` + `member_prompt.md` | `member` |
| B team | DB 列（team_info.desc/prompt） | `<team>/team-workspace/prompts/identity/team_card.md` + `team_prompt.md` | `team` |
| C tool 级 | `locales/descs/<lang>/<domain>/<key>.md` | `<team>/team-workspace/prompts/tool/<domain>/<key>.<lang>.md` | `tool` |
| C 参数级 | `locales/<lang>.py` 的 `STRINGS` dict（完整 dict 原样） | `<team>/team-workspace/prompts/tool/tool.param.<lang>.md`（JSON dict） | `tool_params` |

workspace 根 = `paths.team_workspace_dir`（顶层单一真相）；workspace 内子路径 = `WorkspaceLayout`。

### 写盘规则（`WorkspaceAssembler._write_baseline` 原语，幂等）

1. **首次装配（文件不存在）**：写 framework 基线，frontmatter 记 `baseline_sha256 = body_sha256(body)`、`evolved: false`。
2. **文件已存在，body hash == `baseline_sha256`**（未演进）：框架默认变了（hash 不等）→ 用新默认覆盖并更新 `baseline_sha256`；框架默认没变 → 不覆盖。
3. **文件已存在，body hash != `baseline_sha256`**（已演进）：**不覆盖**（演进优先）。
4. **无 frontmatter 手写文件**：视为已演进（无基线可比对），body 总是生效，不覆盖。
5. **畸形 frontmatter**（YAML 解析失败或非 mapping 根）：文件视为**无效**——读侧回退 framework 默认 / DB 值，写侧以新基线重建。

### 演进判定（读侧唯一依据）

`body_sha256(body) != frontmatter.baseline_sha256` → 已演进 → 读侧用文件 body 覆盖代码默认/DB 值；hash 一致 → 未演进 → 走默认；畸形 frontmatter → 文件无效 → 走默认。总开关 `evolution_enabled` 关时文件照常写、读侧一律不覆盖。

## 决策

### D1：一个 cache 单例，三类分 dict 字段（不分三个 cache 类）

读侧只有一个 `WorkspaceCache` 实例（team 级单例，挂 `TeamWorkspaceManager`，`TeamBackend.workspace_cache` property 委托 manager 一行）。内部 A/B/C 各占 dict 字段。写读脱钩：写盘在装配阶段（`WorkspaceAssembler`），读取在运行期 lazy get（miss 读一次常驻，hit 零 IO）。

### D2：两个读侧入口工厂，可选 cache 参数，默认 None 零侵入

`make_template_loader(cache=None)` 与 `make_translator(lang, cache=None)` 各加可选参数：`cache=None` 时与原签名完全等价（11 个 `make_translator` 调用点 + N 个 `load_template` 调用点全部不改）；只有能拿到 cache 的装配点（rails / worker backend / scheduler / tool factory）显式传 cache。

### D3：演进判定用 body hash 而非 mtime / 探针

cache 无 probe、无 mtime：`get*` miss 时读一次进内存，hit 纯 dict 查找、零文件 IO（连 stat 都没有）。hash 比对天然支持"框架默认变化自动升级"（新基线 hash 不同 → 覆盖），mtime 做不到。

### D4：run 边界失效，运行期不热更新

装配时写基线 + 建空 cache；演进方改文件 → 下次 run 生效（Runner finally `invalidate` 清空 dict，下次 run 第一次 `get*` 重读）。运行期不监听文件变化（不做 hot reload），保持稳态零 IO。

### D5：总开关 `evolution_enabled` 只管读侧

写盘不受开关影响（文件总是写入，首次装配即使关闭也建基线），读侧关闭时 cache build 不读文件、所有值 None、调用方走默认。开关在 `TeamAgentSpec.evolution_enabled`（默认 true），随装配 ctx 传入。

### D6：无 frontmatter 手写文件视为已演进；畸形 frontmatter 视为无效

无基线可比对 → body 总是生效、永不覆盖（演进方的编辑永远不丢）。畸形 frontmatter 相反——文件无效，读侧不认 body（回退默认），写侧可重建基线：无效文件不配做演进保护。

### D7：B 类双写（DB 裸值 + 文件演进值）

B 类 member 级由 `_assemble_member_workspace` 在成员 spawn / 恢复时写；team 级仅当 ctx 带 DB 值（`team_info` 行存在）才写。读侧 `TeamBackend` overlay（`get_member` / `list_members` / `get_team_info`）用 cache 演进值覆盖 DB 裸值；`display_name` 不演进（回退 DB 列）。

### D8：C 类参数级全工具一个 JSON dict 文件

完整 `STRINGS` dict 原样落盘 `tool.param.<lang>.md`（点分键保持 `"<desc_key>.<param>"` 平铺形态，不做嵌套聚合），读侧按第一个点拆分还原。scheduled 变体（`create_task_scheduled` 等）代码里复用 base key，落盘忠实映射——只有 base 条目，演进方改 base 同时影响变体。

### D9：路径单一真相

workspace 内部相对路径（`prompts/system` / `prompts/tool` / `prompts/identity` / `tool.param` 名 / `MEMBER_IDENTITY_REL`）收敛到无状态 `WorkspaceLayout` 一个模块，写侧（assembler/store）与读侧（cache/loader）共用同一套规则；workspace 根字面量收敛到顶层 `paths.team_workspace_dir` 一处。新增可演进文本类只需在 layout 加一行常量 + 一对方法。

## 拒绝的方案

### R1：A/B/C 三个独立 cache 类

演进判定、build 时机、attach 生命周期完全相同，拆三个类只是重复三份机械代码。一个实例三类 dict 字段、一个 build 入口、一次扫描，读侧消费者按类查询即可。

### R2：破坏式改造读侧签名（改全部调用点）

`make_translator` 11 个调用点全部显式传 cache 会放大改动面、牵动 UT 与 external CLI 路径。可选参数 + 默认 `None` 走原逻辑，改动收敛到装配点。

### R3：mtime / 探针做演进判定

mtime 无法表达"框架默认变化 → 自动升级"（基线也变了）；探针在稳态读路径引入 IO。body hash 一次读入内存后纯 dict 查找，判定在 build 期完成。

### R4：运行期热更新（监听文件变化）

热更新破坏"读侧零 IO + 单例 cache"的核心不变量，还要处理并发读改写一致性。演进语义明确是"改文件 + 重启生效"（spawn-only），不需要热更新。

### R5：`agent_teams.paths` 函数承载 A/B/C 落盘路径

`agent_teams.paths` 的 `team_workspace_dir` / `team_member_workspace_dir` 管 team/member 根目录在哪；`WorkspaceLayout` 管 workspace 根内相对路径怎么拼。两者都是纯路径规则但职责正交——混在一起会让"改一个路径要搜两个模块"。
