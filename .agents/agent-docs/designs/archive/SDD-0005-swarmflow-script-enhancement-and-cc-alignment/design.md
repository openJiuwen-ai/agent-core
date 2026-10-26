---
id: SDD-0005
title: SwarmFlow 脚本定义增强与 Claude Code 工具对齐
slug: swarmflow-script-enhancement-and-cc-alignment
status: shipped
stage: archive
owner: "@alan_cheng"
reviewers: ["@arch-team", "@tooling-owner"]
created: 2026-06-22
updated: 2026-06-24
version: 1
domain: tooling
modules: [agent_teams/workflow]
tags: [swarmflow, claude-code, tool-description, script-dsl, options-bag]
related: [SDD-0002, SDD-0003, SDD-0004, SDD-0006]
approved-date: 2026-06-22
dev-start-date: 2026-06-22
archived-date: 2026-06-24
implementation:
  status: shipped
  repos:
    - https://gitcode.com/alan_cheng/agent-core
  prs: []
  commits:
    - alan_cheng/agent-core@b056a098
    - alan_cheng/agent-core@aebbd6fa
    - alan_cheng/agent-core@c061a5e3
    - alan_cheng/agent-core@7f7501dd
    - alan_cheng/agent-core@c5fa41c2
    - alan_cheng/agent-core@490bfc75
---

# SwarmFlow 脚本定义增强与 Claude Code 工具对齐

## 1. 背景与问题 (Why)

我们对照 Claude Code（下称 CC）的 Workflow 工具做了一次调研，想把 SwarmFlow 的特性工具向它对齐。
CC 的设计取向是"**一切编排指引都内嵌在工具的 `description` 里**"——调用它的模型自己现写脚本，所以
它那约 150 行的描述几乎全是"怎么写编排脚本"的手把手指引。对照下来 SwarmFlow 有三个问题：

1. **提示词散落三处、且不完整。** swarmflow 的指引散在工具描述（仅 12 行）、系统提示词的一个
   section（23 行）、和每个原语一行的 docstring 里。相比 CC 的一处内聚，既零散又缺"怎么写脚本"
   这一大块。

2. **工具接口没对齐 CC。** CC 支持多种脚本来源（内联 `script` / 具名 `name` / `resumeFromRunId`
   续跑）、`agent(isolation, agentType)`、预算硬天花板、单次扇出上限；SwarmFlow 这些要么缺、要么
   不对齐。

3. **分工不同带来的取舍。** CC 里模型自己写脚本，SwarmFlow 里 Leader 是旁观者、脚本是预先准备好
   的（当前只接受 `script_path`）。所以对齐时要诚实：**接口表面可以全摆出来对齐 CC，但对尚未接通
   执行的能力必须明确标注、并在调用时明确报错，绝不静默失败。**

本特性做两件事：把提示词集中进工具描述，并把接口表面对齐 CC——同时把"哪些真能用、哪些只是占位"
讲清楚。

## 2. 目标与非目标

**目标**

- 把全部编排指引集中进工具描述（中英双份），删掉分散的 section 及其装配链。
- 工具参数对齐 CC：加 `script` / `name` / `resume_id`，保留 `script_path` / `args`，`invoke` 做
  "四选一"校验。
- `agent()` 原语对齐 CC：加 `isolation` / `agent_type`（经 options 白名单）。
- 把会话原语的调优参数收进 `options` 字典（白名单校验）。
- 真正落地的执行能力：`parallel` / `pipeline` 单次扇出上限，超限显式报错。
- 对未接通执行的能力做"Availability"标注 + 调用时明确报错。

**非目标**

- 不实现内联 `script` / 具名 `name` / `resume_id` 的**工具层执行**（`invoke` 接收但明确拒绝）。
- 不实现 `agent_type` 的执行（参数接受但 backend 不读）。
- 不实现预算的引擎硬拦截（描述按硬天花板写，执行仍是软计数 + 脚本自检）。
- 不删 `enable_swarmflow` 配置字段（它仍是工具注入开关）。

## 3. 术语与上下文

| 术语 | 含义 |
|---|---|
| 工具描述 | swarmflow 工具的 `description` 文本，对标 CC 承载全部编排指引 |
| section | 系统提示词里的一段，旧机制曾用它放 swarmflow 指引 |
| options bag | 原语的调优参数字典，引擎按白名单校验 |
| Availability 标注 | 在描述里明示某能力"当前可用"还是"接口就位、执行推进中" |

## 4. 方案设计 (What / How)

### 4.1 总体架构

提示词从三处收敛到一处；接口表面对齐 CC、但执行能力分"真接通"和"占位"两档：

```
        改动前（提示词散三处）                改动后（收敛进工具描述）
  ┌─────────────────────────────┐     ┌──────────────────────────────────────┐
  │ 工具描述 12 行              ─┼──┐  │ 工具描述（cn ~6097 / en ~10433 字符，  │
  │ 原语 docstring 各 1 行     ─┼──┴─►│ 16 节）对标 CC：何时用 / 脚本来源 /     │
  │                             │     │ 原语 API / pipeline vs parallel / 确定 │
  │                             │     │ 性 / 并发上限 / schema / budget /       │
  │                             │     │ resume / 模式库 / 代码骨架             │
  ├─────────────────────────────┤     ├──────────────────────────────────────┤
  │ 系统提示 section 23 行     ─┼────►│ 删除 section 及其装配链               │
  │ (leader_swarmflow.md)       │     │ (leader_swarmflow.md / SWARMFLOW)      │
  └─────────────────────────────┘     └──────────────────────────────────────┘
```

工具注入链（与 section 无关，完整保留）：`enable_swarmflow` 配置 → 装配时（仅 Leader 且开启）建一个
worker 模型解析器 → 解析器非空才把 swarmflow 工具注入 Leader。本特性只清掉它在 section 路径上的死
传递，注入 gate 一字未动（用户 API 不变）。

### 4.2 数据流与时序

**脚本来源"四选一"校验（`invoke`，按真实顺序）：**

```
invoke(script_path / script / name / resume_id, args)
  │
  ├─ 四者全缺? ──是──► error: one of 'script_path'/'script'/'name'/'resume_id' is required
  │      否
  ├─ script_path 为空但其他有值? ──是──► error: '{x}' is not supported yet;
  │      否                                       provide 'script_path' to run a script from disk
  ├─ 已有并发运行? ──是──► error: A swarmflow run is already in progress
  │      否
  └─► super().invoke → 启动后台任务跑 script_path
```

关键：未接通的来源**不是静默无操作**，而是返回明确的 "not supported yet" 文案，模型一眼能看出
"这个能力还没接通、请用 script_path"。

### 4.3 接口与数据模型

**swarmflow 工具参数（`workflow/tool_swarmflow.py`）与真实执行状态**

| 参数 | 执行状态 | 证据 |
|---|---|---|
| `script_path` | **真能用** | 调 `run_swarmflow(script_path, ...)` |
| `script`（内联） | 接口就位、执行推进中 | invoke 返回 "not supported yet" |
| `name`（具名） | 接口就位、执行推进中 | invoke 返回 "not supported yet" |
| `resume_id`（续跑） | 接口就位、执行推进中 | invoke 返回 "not supported yet"（底层 journal 续跑能力已有，见 SDD-0003，只是工具入口未接） |
| `args` | 真能用（仅 `script_path` 路径） | 传给 `run_swarmflow(args=...)` |

**`agent()` 原语（真实签名 + 选项白名单）**

```python
async def agent(prompt, *, label=None, phase=None, schema=None, options=None) -> Any
# isolation / agent_type 走 options 字典，不在显式签名里
_ENGINE_OPTIONS = frozenset({"label", "phase", "schema", "model", "timeout", "isolation", "agent_type"})
```

> **诚实边界（纠正 F_42 文档的过时表述）：**
> - `isolation='worktree'` —— **已真正实现**，不是占位。引擎校验 `None`/`"worktree"` 后透传，
>   `TeamWorkerBackend` 会调 `create_owner_worktree(slug)` 真实创建 git worktree、worker 在其中跑、
>   完成后按变更情况收尾（干净则删、有改动则保留给后续集成）。F_42 称它"执行层未实现"已过时。
> - `agent_type` —— **仍是占位**：进白名单、不报错，但 backend 从不读取，对执行零影响。
> - **真实缺陷**：`call_signature` 只把 `label/phase/model` 折进指纹，**不含 `isolation`**。后果是
>   脚本若把某个 `agent()` 从无隔离改成 `isolation='worktree'`，resume 时因指纹不变会命中旧缓存、
>   直接返回旧结果、**不会真的去建 worktree**。这是 isolation 落地后产生的正确性缺口，需在"若
>   isolation 影响结果则纳入签名"时修复（见 §10 / §12）。

**单次扇出上限（`workflow/engine/primitives.py`）**

```python
_MAX_FANOUT = 4096
# parallel(thunks) / pipeline(items) 入口校验 len > _MAX_FANOUT 即 raise WorkflowError
```

4096 通过、4097 抛错；显式报错而非静默截断（对齐 CC）。

**预算（`Runtime`）**

`budget_total` 字段存在、`tokens_spent` 在每次 agent 调用后累加，`budget.spent()/remaining()` 读数
正确——但 `agent()` 执行路径里**没有**"超预算就 raise"的硬拦截。它是**软计数 + 脚本自检**。描述里
诚实写了：`（注：调用前硬拦截实现推进中，当前为计数 + 脚本自检。）`。另有 `spawn_limit`（默认
1000）是 agent 总次数上限，与 token 预算是两个独立机制。并发上限是 `max(1, min(16, CPU核数-2))`。

### 4.4 关键流程

- **提示词集中化**：删除 `leader_swarmflow.md`（cn/en）及其 section 装配链（`TeamSectionName.
  SWARMFLOW` / `build_team_swarmflow_section` / 仅为该 section 传递的 `enable_swarmflow`）。已确认全仓
  无 `leader_swarmflow.md` 文件、`sections.py` 无 swarmflow 匹配。全部指引重组进工具描述，逐节对标
  CC（共 16 个 `##` 小节：何时用/工作流形态/行为契约/脚本来源/脚本结构/原语 API/worker 能跑什么/
  pipeline vs parallel/确定性约束/并发上限/schema/budget/resume/模式库/代码骨架）。
- **Availability 标注（描述原文片段）**：
  - `script_path`（**当前可用**）：磁盘脚本路径，优先级最高。
  - `script` / `name` / `resume_id`（接口就位、执行推进中）。
  - `agent_type`（接口就位、执行推进中）。
- **工具描述加载不经模板格式化**：描述经 `translator("swarmflow")` 原样加载（不传 kwargs 即不过
  `PromptTemplate.format`），代码块里的花括号完整保留。

### 4.5 配置与开关

| 配置 | 值 | 说明 |
|---|---|---|
| `enable_swarmflow` | 配置字段（保留） | 工具注入 gate；仅 Leader + 开启时建 worker 模型解析器 |
| `_MAX_FANOUT` | 4096 | 单次 `parallel`/`pipeline` 上限 |
| `_ENGINE_OPTIONS` | 7 键 | + backend `KNOWN_OPTIONS`；未知键 fail-fast |
| `spawn_limit` | 1000 | agent 总次数上限（≠ token 预算） |
| 并发上限 | `max(1, min(16, CPU-2))` | 同时在跑的 agent 数 |

## 5. 关键决策与权衡 (ADR)

### 5.1 提示词集中进工具描述
- **选择：** 删 section，全部指引重组进工具描述，对齐 CC「内嵌 description」。
- **为什么：** 两处提示词（section + 描述）语义重叠、维护双份；CC 模式更内聚。

### 5.2 接口全面对齐 + 执行分档 + Availability 标注
- **选择：** 表面对齐 CC，未接通能力诚实标注 + `invoke` 明确报错。
- **为什么：** 纯按 CC 写但不标注状态，会让模型调用未接通能力却静默失败；标注 + 明确报错既对齐
  表面（脚本可针对完整 API 编写）又诚实。

### 5.3 单次扇出显式报错（不静默截断）
- **选择：** `parallel`/`pipeline` 超 4096 抛 `WorkflowError`。
- **为什么：** 对齐 CC「显式报错而非静默截断」，避免"看似覆盖全部其实丢了一截"。

### 5.4 被否决的备选与取舍理由
- **保留 section、只补工具描述**：违背"全放工具描述、对齐 CC"，且两处语义重叠维护双份。
- **纯按 CC 写描述、不标注实现状态**：会让模型调用未接通能力却静默失败。
- **删除 `enable_swarmflow` 字段**：它仍是工具注入开关，删除破坏用户 API；只清 section 路径的死
  传递。
- **（针对 agent_type/budget）把执行也一并做掉**：涉及 backend 与团队装配的较大改动，超出"接口 +
  提示词就位"的本轮范围。

## 6. 影响面与兼容性

- **改动**：`workflow/engine/{facade,primitives,seam,provider}.py`（`agent()` 加 isolation/agent_type
  + 扇出校验 + options bag）、`workflow/tool_swarmflow.py`（参数 + invoke 四选一）、`tools/locales/
  {cn,en}.py`（参数串）、`tools/locales/descs/{cn,en}/swarmflow.md`（重写）。
- **删除**：`prompts/{cn,en}/leader_swarmflow.md`、`prompts/sections.py` 的 swarmflow section、
  `rails/{team_policy_rail,elements}.py` + `agent/agent_configurator.py` 的 section 传递链。
- **兼容性**：`enable_swarmflow` 与工具注入链保留，用户 API 不变；`isolation`/`agent_type` 不入缓存
  签名 → worker resume 不因这两个参数变化而失效（但这对 isolation 是缺陷，见 §4.3）。无 BREAKING。

## 7. 测试与验证

- 引擎：`agent(..., isolation='worktree', agent_type='Explore')` 经 MockBackend 跑通（参数被接受、
  不报错）；`parallel`/`pipeline` 传 4097 抛 `WorkflowError`，4096 通过。
- 工具：`invoke` 仅给 `script_path` 通过；只给 `script`/`name`/`resume_id` 返回 "not supported yet"；
  四者全缺返回 "one of ... is required"；并发冲突仍拦截。
- 描述：cn/en 经 `translator("swarmflow")` 原样加载，含 isolation/resume_id/4096 等关键节；
  cn ~6097 / en ~10433 字符。
- section 移除：`build_team_static_sections(LEADER)` 产出 `[team_role, team_workflow, team_lifecycle]`
  （无 swarmflow），装配不报错。
- 合计：`workflow/` 83 passed + prompts/rails/agent 单测 63 passed。

## 8. 可观测性

- 工具描述字符数（cn ~6097 / en ~10433）作为内容完整性回归基线。
- `invoke` 对未接通来源的 "not supported yet" 报错可从日志区分"未实现"与"真失败"。
- 扇出超限的 `WorkflowError` 计数（显式报错而非静默截断）。

## 9. 风险与回滚

- **工具描述变长**增加 Leader 上下文 token，以换取编排指引内聚（符合 CC 取向）。
- **isolation 不入签名的正确性缺口**（见 §4.3）：当前若用 `isolation='worktree'` 且发生 resume
  命中，会跳过 worktree 创建——已记录为待修复项。
- **回滚**：接口为加法（保留 `script_path`/`args`），可逐项回退；section 删除可恢复但不建议（双份
  维护）。

## 10. 开放问题 (Open Questions)

- [x] 保留 section 还是全放工具描述？→ 全放工具描述，删 section。
- [x] 未接通能力如何处理？→ 表面对齐 + Availability 标注 + invoke 明确报错。
- [ ] `isolation` 影响结果，是否纳入 `call_signature`？（当前未纳入 = 正确性缺口）
- [ ] 内联 `script` / 具名 `name` / `resume_id` 工具层执行何时接通？预算硬拦截何时实现？

## 11. 评审记录 (Iteration Log)

| 轮次 | 日期 | 版本 | 评审人 | 结论 | 主要修改/讨论 |
|---|---|---|---|---|---|
| 1 | 2026-06-24 | 1 | @arch-team | approved | 回溯归档；内容以当前 HEAD 代码为准，纠正 F_42 关于 isolation 的过时表述并标注 call_signature 缺口 |

## 12. 实现记录与偏差

### 实际实现
工具描述重写并对标 CC 逐节（16 节，cn ~6097 / en ~10433 字符），删除 `leader_swarmflow.md` section
及其装配链；工具参数加 `script`/`name`/`resume_id`（invoke 四选一、未接通来源明确报错）；`agent()`
加 `isolation`/`agent_type`（经 options 白名单）；`parallel`/`pipeline` 单次扇出 ≤4096 显式报错；
会话原语调优参数收进 options bag。

### 与设计的偏差
- **与 F_42 文档不一致（以代码为准）**：F_42 称 `isolation='worktree'` 执行层未实现，**代码里它已
  真正创建/收尾 git worktree**；仅 `agent_type` 仍是占位、`budget` 仍是软计数。
- **新发现的正确性缺口**：`call_signature` 不含 `isolation`，resume 命中会跳过 worktree 创建——
  F_42 已预埋"将来若影响结果需纳入签名"，现 isolation 已实现，该缺口变为实在风险。
- options bag 收口作为独立 `refactor(swarm)`（`c5fa41c2`）落地。

### 链接
- 原始特性记录：`agent-core` 仓 `F_42_swarmflow-tool-claude-code-alignment.md`，规约 `S_18`（CC 对齐
  参数、单次扇出上限段）。本特性 footer 用 `Refs: #1047`。
- 关键代码：`workflow/tool_swarmflow.py`（参数 + invoke）、`workflow/engine/primitives.py`
  （`_ENGINE_OPTIONS`/`_MAX_FANOUT`）、`workflow/backends/team_worker_backend.py` + `worktree.py`
  （isolation 真实实现）、`tools/locales/descs/{cn,en}/swarmflow.md`（描述）。
- commits 见 frontmatter `implementation.commits`。

### 遗留与后续
- `isolation` 纳入 `call_signature`（修复 resume 命中跳过 worktree 的缺口）。
- 内联 `script` / 具名 `name` / `resume_id` 工具层执行未接通（`invoke` 明确拒绝）。
- `agent_type` 执行层未实现（backend 不读）。
- 预算硬天花板未实现（当前软计数 + 脚本自检）。
- 原语 docstring 未逐一补全（工具描述已自包含核心）。

## 13. 参考与关联设计

- SDD-0002（有状态会话）：会话 options 白名单同源。
- SDD-0003（中断恢复）：`resume_id` 续跑、`META["name"]` 必填的底层能力在彼处；`resume_id` 工具
  入口未接通即指向它。
- SDD-0004（嵌套加固）：`parallel`/`pipeline` 扇出上限、嵌套 `workflow()`。
- SDD-0006（异步工具管控框架）：`resume_id` 等脚本来源的执行层依赖异步工具执行框架。
- 原始特性记录：`agent-core` 仓 `F_42` / `S_18`。
