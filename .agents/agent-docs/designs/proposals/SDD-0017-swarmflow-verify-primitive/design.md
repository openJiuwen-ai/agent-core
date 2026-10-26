---
id: SDD-0017
title: Swarmflow 验证原语 verify()：把任务验收机制抽象为可脚本调用的原语
slug: swarmflow-verify-primitive
status: draft
stage: proposals
reviewers: []
created: 2026-08-26
updated: 2026-08-26
version: 1
domain: core
modules: [agent_teams/workflow]
tags: [swarmflow, verification, reviewer, primitive, orchestration]
related: [SDD-0015]
author: '@sodawlh'
author_note: 由历史 owner 字段原值迁移
type: D
files: []
author_email: null
---

# Swarmflow 验证原语 verify()：把任务验收机制抽象为可脚本调用的原语

## 1. 背景与问题 (Why)

AgentTeam 的**调度模式**（见 SDD-0015）已经沉淀了一套成熟的任务验收机制：Leader 在 `create_task` 时给任务挂结构化 reviewer 列表（`verifier` / `inspector` / `challenger`），调度器自动拉起临时 reviewer 智能体投票，用「一票否决 + 检视平均分门槛」判定 pass/fail，并驱动返工 / 升级。这套机制的判定语义（哪些 reviewer 一票否决、inspector 平均分多少算通过）已经被产品验证过、可稳定收集多验证者反馈。

但 Swarmflow 是**另一条编排路径**：脚本用 `agent()` / `agent_session()` / `parallel()` 等原语声明式编排，Worker 是一次性调用。当脚本需要对自己的产物做质量把关时，目前只能**手写验证调用、聚合、分支控制**——把「逐个 reviewer 派发、结构化收集投票、按语义判定」这一整坨逻辑散在脚本里。这带来三个问题：

1. **重复且易错**：每次想验收一份产物都要重写「起 N 个验证 agent → 收结构化投票 → 一票否决 / 平均分判定 → 聚合反馈」的样板，判定语义漂移（有人写任一 fail 即败，有人写多数通过）。
2. **与已有机制割裂**：调度模式的 reviewer 提示词（`reviewer_verifier` / `reviewer_inspector` / `reviewer_challenger` + 默认打分维度表）已经打磨过校验口径，脚本侧却无法复用，质量把控标准不统一。
3. **没有一等公民身份**：验证作为一个概念在 Swarmflow 脚本里「看不见」——没有结构化的 `verify()` 入口，没有专有的进度事件，审查逻辑与执行逻辑混在一起，可读性和可观测性都差。

本设计把「任务验收机制」从调度模式里抽象出来，收敛成一个**可被脚本直接调用的 Swarmflow 原语 `verify()`**，让任何脚本都能用同一套被验证过的验收语义为任意产物把关。

设计基于 `openjiuwen/agent_teams` 当前实现逆向整理：`agent/scheduling/verdict.py`（判定）、`agent/scheduling/scheduler.py`（reviewer 派发）、`prompts/{cn,en}/reviewer_*.md`（reviewer 提示词）、`workflow/engine/primitives.py`（原语宿主）。

## 2. 目标与非目标

**目标**
- 提供 Swarmflow 脚本可直接调用的模块级原语 `verify()`，与 `agent()` / `agent_session()` / `parallel()` 同级（`from swarmflow import verify`）。
- 复用被调度模式验证过的验收语义：verifier / challenger 一票否决，inspector 平均分 ≥ 阈值（默认 0.85）才算通过。
- 让 `verify()` 天然继承引擎既有能力：journal 断点续跑、并发门禁、spawn 上限、token 预算、progress 事件、MockBackend 离线预演——全部通过组合 `agent()` 免费获得。
- 提供业务侧辅助，把「reviewer 类型 → 结构化投票 schema + 产物形态」映射为脚本易用的入参，并参照调度模式的 reviewer 提示词产出 Swarmflow 专用 reviewer 提示词（cn/en 成对）。
- 向 leader（写脚本的 LLM）提供「reviewer 组成建议」，指导其在调用 `verify()` 时按场景分配 verifier / inspector / challenger 的数量与组合，并把该指引下沉到 `swarmflow.md` 工具描述。

**非目标**
- 不在 `verify()` 内驱动返工循环。返工（让执行者按反馈重做）是脚本的控制流职责，脚本自己写 while 循环（见 §4.4）。
- 不替换调度模式的验证机制。调度模式的 `verify_task` / `settle_review_tally` 保持原样，本设计只是在 Swarmflow 侧新增一个可复用入口。
- 不让 `engine/` 依赖任何 `agent_teams` 业务模块。判定逻辑在 engine 内**内置一份通用实现**，不 import 业务 `verdict.py`（铁律 1）。
- 不把 reviewer 变成常驻团队成员。`verify()` 的 reviewer 是一次性 `agent()` 调用，用完即弃。
- 不支持通过 `verify()` 直接操作团队任务看板 / DB。它只对传入的产物做判定，产物如何产生、如何返工由脚本负责。

## 3. 术语与上下文

| 术语 | 本文含义 |
|---|---|
| verify 原语 | 本设计新增的 Swarmflow 模块级函数 `verify()`；对一份产物跑一轮 reviewer 并给出 pass/fail 判定 |
| reviewer | 一个对产物做独立判定的验证智能体；由一次 `agent()` 调用承载 |
| verdict reviewer | 投 pass/fail 二元票的 reviewer，对应调度模式的 `verifier` / `challenger` |
| score reviewer | 投 0~1 分数的 reviewer，对应调度模式的 `inspector` |
| 一票否决 | 任一 verdict reviewer 投 fail，则该轮判定 fail |
| 阈值 | score reviewer 平均分须 ≥ 该值（默认 0.85）才判 pass |
| 产物 (deliverable) | 待验证的对象：一段文本（代码 / 报告 / 回复）或若干文件路径 |
| engine | `workflow/engine/`，业务无关的编排引擎；铁律 1：不 import 任何 agent_teams 业务模块 |

## 4. 方案设计 (What / How)

### 4.1 总体架构

`verify()` 放在 engine 层、作为**模块级原语**，与 `agent()` / `parallel()` 并列。它不引入新的 provider 方法，而是**组合已有的 `agent()`**：每个 reviewer 都是一次带结构化 schema 的 `agent()` 调用，返回的投票汇总后交给 engine 内置的通用判定。

```
脚本 (async def run)
   │  from swarmflow import verify
   │  from agent_teams.workflow.review import build_reviewers
   │  result = await verify(reviewers)
   ▼
┌────────────────────────── workflow/engine/ (业务无关) ─────────────────────────┐
│  facade.verify() ──► primitives.verify()                                         │
│      │                 │ 并行派发（复用 parallel()，每票独立 journal 结构键）      │
│      │                 │   └─► agent(prompt, schema=…)   (verdict / score schema) │
│      │                 ▼                                                          │
│      │                 settle_verify_tally(tally, threshold)  ── 内置通用判定     │
│      └────────────────► VerifyResult{verdict, votes, feedback, passed}            │
└──────────────────────────────────────────────────────────────────────────────────┘
   ▲ 业务层（仅提示词 / 产物封装，engine 不依赖）
┌─────────────────────── workflow/review.py ───────────────────────────────────────┐
│  build_reviewers(deliverable, specs, acceptance, language)                       │
│     type→kind 映射（verifier/challenger→verdict，inspector→score）                │
│     └─► 渲染 Swarmflow 专用 reviewer 提示词（参照 prompts/reviewer_*.md）         │
└──────────────────────────────────────────────────────────────────────────────────┘
```

分层边界清晰：engine 只认识「kind(pass/fail 或 score) + prompt」的 reviewer，做派发、收集、判定；业务层负责把「type 语义 + 产物 + 验收标准」翻译成 reviewer prompt。两者通过 `Reviewer{kind, prompt, label, options}` 这一中立契约解耦。

### 4.2 数据流与时序

单次 `verify()` 调用（并行 reviewer，无返工循环）：

```
脚本                primitives.verify            agent() × N             settle_verify_tally
 │                        │                         │                        │
 │  await verify(         │                         │                        │
 │    reviewers=[...])    │  foreach reviewer:      │                        │
 │                        │  ──(parallel)──────────►│  agent(schema=…)      │
 │                        │                         │  返回 {decision|score, │
 │                        │                         │        feedback}       │
 │                        │  ◄──────────────────────│  (None = 未投票)       │
 │                        │  汇总 votes             │                        │
 │                        │  ────────────────────────────────────────────────►│
 │                        │                        verdict: pass|fail|None   │
 │                        │  ◄────────────────────────────────────────────────│
 │                        │  聚合 feedback                                     │
 │  ◄── VerifyResult ─────│                                                   │
 │    verdict=pass        │                                                   │
```

- 每个 reviewer 的 `agent()` 走既有 journal / 并发门禁 / 预算路径，天然支持断点续跑与预算治理。
- reviewer 返回 `None`（`agent()` 失败 / 被跳过）视为该 reviewer 未投票；未投满时判定为 undecided（`verdict=None`）。
- `verify()` 只做一轮判定，**不等待返工**。脚本拿到 fail 后自行组织执行者重做并再次 `verify()`。

### 4.3 接口与数据模型

**engine 层（`workflow/engine/verify.py` 新增，`primitives.py` / `facade.py` 导出）：**

```python
class Reviewer:
    kind: Literal["verdict", "score"]   # verdict = pass/fail；score = 0~1
    prompt: str                          # 业务层渲染好的完整 reviewer 提示词
    label: str | None = None
    options: dict | None = None

class VerifyVote:
    kind: Literal["verdict", "score"]
    decision: bool | None = None   # verdict 用：True=pass
    score: float | None = None     # score 用：0~1
    feedback: str = ""             # 附带理由 / 评分报告 / 威胁清单

class VerifyResult:
    verdict: Literal["pass", "fail"] | None  # None = undecided（有 reviewer 未投满）
    votes: list[VerifyVote]
    feedback: str                            # 聚合的失败反馈（喂给返工）
    passed: bool                             # verdict == "pass"

async def verify(
    reviewers: Sequence[Reviewer],
    *,
    threshold: float = 0.85,
    label: str | None = None,
    phase: str | None = None,
    options: dict | None = None,
) -> VerifyResult
```

**内置通用判定（`workflow/engine/verify.py`，不依赖业务）：**

```python
def settle_verify_tally(tally, threshold=0.85) -> Literal["pass", "fail", "undecided"]:
    # verdict 池：任一 fail → fail（一票否决）；未投满 → undecided
    # score 池：平均分 < threshold → fail；未投满 → undecided
    # 两池都判定后取最严格；无池 → pass
```
注：`settle_verify_tally` 是纯函数，"无池 → pass" 是防御性默认——`verify()` 在入口已拒绝空 reviewer 列表（见 §4.4），故经 `verify()` 调用时该分支不可达，不会出现"空列表静默 pass"。

engine 内部为 reviewer 构造两种 JSON Schema（业务无关的中立 schema）：
- `kind="verdict"`：`{decision: "pass"|"fail", feedback: str}`
- `kind="score"`：`{score: number 0~1, feedback: str}`

**业务层（`workflow/review.py` 新增）：**

```python
def build_reviewers(
    deliverable: str | list[str],      # 文本内容 或 文件路径清单
    specs: Sequence[dict],             # [{type: verifier|inspector|challenger,
                                       #   instruction?: str, label?: str}]
    *,
    acceptance: str | None = None,     # 验收标准（可选），参照 scheduler_review_request
    language: str = "cn",
) -> list[Reviewer]
```

`build_reviewers` 只做「类型 → kind 映射 + prompt 渲染 + 产物封装」，不碰判定。`verify()` 只认 `Reviewer`。

**reviewer 组成建议（分配原则）：** `verify()` 不决定 reviewer 的数量与组合——那由调用它的脚本决定。写脚本时（对 LLM leader 而言是在 `swarmflow` 工具描述里获得指引，对人而言是本设计文档），三种类型的适用场景与数量基调如下，脱胎于调度模式 `dispatch_scheduled_leader.md` 的分配原则，但适配到「单个产物」而非「任务 + `.team/` 工作区」的模型：

| type | 适用场景 | 数量基调 |
|---|---|---|
| `verifier` | 几乎所有需要把关的产物都应配，是基本质量保障；逐项对照验收标准确认"做到了没有" | ≥1，主力 |
| `inspector` | 需要多维度综合评分的关键交付物 / 被下游消费、规范与一致性要求高的产物 | 按关键度；轻量产物可不配 |
| `challenger` | 无确定性验收标准、开放性 / 设计 / 规划 / 调研类，对下游有决定性 / 方针性影响的产物 | 此类必配；验证 / 测试类产物不配 |

组合基调（按产物重要性与开放性）：
- **轻量把关**：1 个 `verifier`。
- **重要交付物**：`verifier` + `inspector`（可多个 inspector 分维度）。
- **开放性 / 高风险设计**：`verifier` + `challenger`（视需要再加 `inspector`）。

注：`verify()` 单次只判定一份产物；同一脚本对不同阶段产物可用不同组成多次调用。

**Requirement / Scenario：**
> **Requirement:** 系统 SHALL 提供模块级原语 `verify()`，对给定 reviewer 列表运行一轮判定，返回结构化 `VerifyResult`。
> **Scenario:** WHEN 脚本 `await verify(reviewers=[...])` THEN 返回含 `verdict` / `votes` / `feedback` 的 `VerifyResult`。
>
> **Requirement:** 系统 SHALL 对 verdict reviewer 采用一票否决，对 score reviewer 采用平均分阈值判定。
> **Scenario:** WHEN 任一 verdict reviewer 投 fail，且无 score 池 THEN `verdict="fail"`；WHEN 全部 score reviewer 平均分 ≥ 0.85 THEN `verdict="pass"`。
>
> **Requirement:** 系统 SHALL 在存在 reviewer 未投票时返回 undecided（`verdict=None`），而非臆断。
> **Scenario:** WHEN 某 reviewer 的 `agent()` 返回 `None` THEN `verdict=None` 且 `feedback` 标注缺失票。

### 4.4 关键流程

**脚本侧返工闭环（单次判定 + 脚本驱动）：**

```python
from swarmflow import verify
from agent_teams.workflow.review import build_reviewers

deliverable = "…实现代码…"              # 或 ["src/x.py", "docs/y.md"]
reviewers = build_reviewers(deliverable, [
    {"type": "verifier", "instruction": "跑单测验证边界情况"},
    {"type": "inspector"},             # 无 instruction → 默认 6 维打分表
])
for rnd in range(3):                    # 返工循环由脚本负责
    r = await verify(reviewers, label=f"verify-{rnd}")
    if r.passed:
        break
    deliverable = await agent(f"按反馈返工后重新实现：\n{r.feedback}\n原产物：{deliverable}")
    reviewers = build_reviewers(deliverable, [...])   # 用新产物重建 reviewer
```

**边界 / 异常处理：**
- **reviewer 未投**：`agent()` 返回 `None` → 记未投票；`verify()` 返回 undecided，脚本决定重试或放弃（不自动重试，保持单次判定语义）。
- **空 reviewer 列表**：`verify()` 校验非空，否则返回明确错误（无验收语义可言，不允许静默 pass）。
- **文本产物**：`build_reviewers` 把文本内联进 prompt；**路径产物**：prompt 列出路径并指示 reviewer 用文件工具读取（Worker 自带 teammate 能力的文件工具）。
- **inspector 缺 instruction**：回填默认 6 维打分表（参照 `reviewer_dims_for_inspector`）。
- **`verify()` 与预算 / 并发**：它调 `agent()`，自动受 `_check_budget` / spawn 上限 / 并发门禁约束，无额外样板。

### 4.5 配置与开关

无新增配置项。`verify()` 的阈值（默认 0.85）、label / phase / options 均为调用参数，不引入特性开关。返工轮数与循环由脚本控制，无全局开关。

## 5. 关键决策与权衡 (ADR)

### 5.1 `verify()` 作为 engine 模块级原语，而非业务级函数
- **决策：** `verify()` 落在 `workflow/engine/primitives.py`，与 `agent()` / `parallel()` 同级，经 `facade.py` 导出为 `from swarmflow import verify`。
- **背景：** 原语是脚本与引擎之间的唯一接缝，统一经 `from swarmflow import ...` 命名空间暴露。要让 `verify()` 成为一等原语、进入与 `agent()` 相同的结构键 / journal / progress 体系，就必须落在 engine 层，而不是散在业务层。
- **备选与理由：** 备选是放 `workflow/` 顶层的业务级函数。否决理由：它与 `agent()` / `agent_session()` 处于不同层、无法以原语身份进入统一 facade / 结构键体系；且业务级函数需自行接 engine 的 contextvar / journal 样板。engine 级 `verify()` 组合 `agent()`，天然继承全部引擎能力。

### 5.2 `verify()` 组合 `agent()`，不新增 provider 方法
- **决策：** `verify()` 内部调用既有 `agent()`（每个 reviewer 一次结构化 schema 调用），不新增 `Provider.verify`。
- **背景：** 铁律 1 要求 engine 业务无关；同时不破坏 provider 可替换接缝（MockBackend 预演）。
- **备选与理由：** 备选是加 `Provider.verify` + backend 方法。否决理由：`agent()` 已把「journal / 并发门禁 / 预算 / progress / 结构化输出」全部封装好，`verify()` 只是它的编排糖，没必要把同一套逻辑在 backend 再实现一遍；组合 `agent()` 让 MockBackend 与真实 worker 路径零差异工作。

### 5.3 判定逻辑在 engine 内置一份，不 import 业务 `settle_review_tally`
- **决策：** engine 内新增约 20 行通用判定 `settle_verify_tally`（一票否决 + 平均分阈值），业务 `agent/scheduling/verdict.py` 保持原样。
- **背景：** 铁律 1：engine 不得 import 任何 `agent_teams` 业务模块。
- **备选与理由：** 备选是把 verdict 逻辑抽到中性模块、调度模式与 engine 共用。否决理由：改动面大、牵动已稳定的调度路径；`settle_review_tally` 的 tally 数据结构（`verdict_total` / `inspector_count` 等）与业务耦合，抽出来收益低。轻微重复（约 20 行）换来 engine 自洽与铁律 1 不破，值得。

### 5.4 单次判定，返工循环由脚本驱动
- **决策：** `verify()` 只对一份产物做一轮判定并返回 verdict + feedback，不内置返工循环。
- **背景：** 判定与执行调度是两件独立的事。`verify()` 只负责"这份产物当下过不过"，不持有执行者身份、返工手段或重提时机——这些都属于脚本的控制流。
- **备选与理由：** 备选是 `verify()` 接收 rework 回调自动循环到 max_rounds。否决理由：返工语义（执行者是谁、怎么改、改完怎么重提）完全由脚本控制流决定，塞进 `verify()` 会让原语既负责判定又负责执行调度，职责混在一起、可复用性下降。脚本侧一个 while 循环即覆盖，职责单一。

### 5.5 产物同时支持文本与文件路径
- **决策：** `build_reviewers` 的 `deliverable` 接受 `str`（文本内联进 prompt）或 `list[str]`（文件路径，prompt 指示 reviewer 读取）。
- **背景：** 产物输入需同时支持文本与文件路径两形态。
- **备选与理由：** 备选只支持路径（贴近调度模式）或只支持文本（最简）。否决理由：Swarmflow Worker 自带文件工具，路径形态能验证落盘的产物；但脚本里大量中间结果（推理文本、生成的回复）是内存字符串，内联更直接。两形态都保留，由 `build_reviewers` 统一封装。

### 5.6 Swarmflow 专用 reviewer 提示词模板，参照调度模式但适配结构化投票
- **决策：** 新增 `prompts/{cn,en}/swarmflow_reviewer_{verifier,inspector,challenger}.md` 三个模板（cn/en 成对），**参照**调度模式同名模板的核心理念 / 工作流程 / 打分表写法，但把「投票裁决」从 `verify_task` 工具改为「结构化输出提交 verdict / score + feedback」。
- **背景：** 调度模式 `reviewer_*.md` 引用 `verify_task` / `view_task` / `.team/` 工作区，这些团队工具 Swarmflow Worker 没有。
- **备选与理由：** 备选直接复用调度模式模板。否决理由：模板里硬编码 `verify_task`，复用必然失效。另备选把调度模式模板改成不含 `verify_task`，让两侧共用一份——否决理由：改已稳定调度路径、有回归风险，且调度模式模板语义（任务 / `.team/`）与 Swarmflow（内联产物）不同。新增专用模板 + 明确标注与调度模式模板的 drift 关系（见 §6）。

## 6. 影响面与兼容性

- **新增文件**：`workflow/engine/verify.py`（原语 + 判定 + 数据类）、`workflow/review.py`（业务辅助）、`prompts/{cn,en}/swarmflow_reviewer_{verifier,inspector,challenger}.md`。
- **修改文件**：`workflow/engine/primitives.py`（`verify()` 实现 + 导出）、`workflow/engine/facade.py`（`__all__` 增加 `verify` / `Reviewer` / `VerifyResult`）、`tools/locales/descs/{cn,en}/swarmflow.md`（新增「reviewer 组成建议（分配原则）」小节）、`REGISTRY.md` + `INDEX.md`。
- **不改变**：`agent()` / `agent_session()` / `parallel()` 等既有原语；调度模式的 `verify_task` / `settle_review_tally` / `reviewer_*.md` 保持原样；`AgentBackend` 协议（`verify()` 组合 `agent()`，不新增 backend 方法）。
- **向后兼容**：完全新增，无 **BREAKING**。`engine/` 仍业务无关；业务辅助 `workflow/review.py` 只在顶层，不破坏铁律 1。
- **模板 drift**：`swarmflow_reviewer_*.md` 与调度模式 `reviewer_*.md` 是「同源但独立演化」的关系。若调度模式调整评审口径，Swarmflow 模板需人工同步。归档时在 §12 标注。

## 7. 测试与验证

**单元测试**（`tests/unit_tests/agent_teams/workflow/`）：
- `settle_verify_tally`：verdict 一票否决、score 平均分边界（=0.85 通过 / <0.85 失败）、未投满 → undecided、空池 → pass、阈值参数化。
- `verify()`（MockBackend）：多 reviewer 投票汇总、fail 反馈聚合、`agent()` 返回 None → 未投票 → undecided、空 reviewer 列表报错、并行 reviewer 各自独立 journal 结构键。
- `build_reviewers`：文本 / 路径两形态、type→kind 映射、inspector 缺 instruction 回填默认打分表、cn/en 模板渲染。
- `swarmflow.md` 指引断言：`tools/locales/descs/{cn,en}/swarmflow.md` 含「reviewer 组成建议（分配原则）」小节（对齐仓库对提示词 / 工具描述内容的断言做法）。

**集成 / E2E**：
- `preprocess_swarmflow`（MockBackend）跑一个含 `verify()` 的脚本，确认不炸、reviewer 以普通 agent 出现在 4 层结构里。
- 真实 worker 跑一次「实现 → verify → fail → 返工 → verify → pass」的完整脚本，确认闭环与判定正确。

**成功指标**：脚本中「写验收判定」的样板代码归零；同一份产物的判定结果与调度模式口径一致（一票否决 + 0.85 阈值）；`verify()` 下 review 的 token 消耗如实计入预算。

## 8. 可观测性

- `verify()` 组合 `agent()`，每个 reviewer 自动产出 `AGENT_STARTED` / `AGENT_COMPLETED` / `AGENT_FAILED` progress 事件，脚本调用方与 `WorkflowObserver` 可见每票。
- `verify()` 可传 `label` / `phase`，在事件里标识「这是第几轮验证」。
- 可在 `verify()` 入口 `log()` 一行「验证轮 X：N 个 reviewer」，聚合后 `log()` 判定结果与失败反馈摘要。
- 判定语义（一票否决 / 阈值）由 engine 内置，无需额外埋点；错误（空列表 / 未投满）走异常 / undecided 返回并 log。

## 9. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 判定语义与调度模式漂移 | 复用同一套「一票否决 + 0.85」语义；在文档 §6 / §12 标注 drift 关系，评审时对齐口径 |
| engine 内置判定与业务 `verdict.py` 重复维护 | 重复仅约 20 行纯函数；后续如需合并，抽中性模块 + 双端引用（记入 §12 遗留） |
| reviewer 提示词复用度不足 | 新增模板参照调度模式写法，保持三种角色核心理念一致 |
| 误判（reviewer 未投满即 undecided） | 显式 undecided 语义 + 脚本决定重试；不自动臆断 |

回滚：本设计为纯新增，删除新增文件 + 移除 facade 导出即可回退，不影响既有原语与调度模式。

## 10. 开放问题 (Open Questions)

- [ ] reviewer 提示词模板与调度模式 `reviewer_*.md` 的同步维护机制（是否抽共用 fragment）。
- [ ] `verify()` 是否需要独立的 `ProgressKind.VERIFY` 事件，还是复用 `agent()` 的既有事件足够。
- [ ] 阈值是否允许 per-reviewer 覆盖（如某 inspector 权重更高），还是保持单一阈值。

## 11. 评审记录 (Iteration Log)

| 轮次 | 日期 | 版本 | 评审人 | 结论 | 主要修改/讨论 |
|---|---|---|---|---|---|
| 1 | 2026-08-26 | 1 | 待定 | 待定 | 首次建档 |

## 12. 实现记录与偏差

### 实际实现
（待实现后填写。）

### 与设计的偏差
（待实现后填写。）

### 链接
（待实现后填写。）

### 遗留与后续
- 判定逻辑与调度模式 `settle_review_tally` 的合并（若后续决定抽中性共享模块）。
- reviewer 提示词模板的共享 / 同步机制。

## 13. 参考与关联设计

- [SDD-0015](proposals/SDD-0015-agent-team-scheduled-dispatch/design.md)（agent-team-scheduled-dispatch）——调度模式，本设计的判定语义与 reviewer 提示词的来源。
- `openjiuwen/agent_teams/agent/scheduling/verdict.py`（`settle_review_tally`）
- `openjiuwen/agent_teams/workflow/engine/primitives.py`（原语宿主）
- `openjiuwen/agent_teams/prompts/{cn,en}/reviewer_{verifier,inspector,challenger}.md` 与 `reviewer_dims_for_inspector.md`
