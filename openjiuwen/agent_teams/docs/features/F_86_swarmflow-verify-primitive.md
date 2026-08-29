# Swarmflow 验证原语 verify()：把任务验收机制抽象为可脚本调用的原语

为 Swarmflow 脚本引入 `verify()` 原语，把调度模式的验收语义（verdict 一票否决 + score 平均分门槛）复用为脚本可调用的多 reviewer 判定。设计草案见 swarm-design-docs 的 SDD-0017。

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-26 |
| 范围 | `openjiuwen/agent_teams/workflow/{engine/verify.py, engine/primitives.py, engine/facade.py, engine/__init__.py, review.py}`、`openjiuwen/agent_teams/prompts/{cn,en}/swarmflow_reviewer_{verifier,inspector,challenger}.md`、`openjiuwen/agent_teams/tools/locales/descs/{cn,en}/workflow/swarmflow.md`、`tests/unit_tests/agent_teams/workflow/test_verify.py` |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/` 全量通过 |
| Refs | #—（关联 SDD-0017） |

## 背景

Swarmflow 脚本要对产物做质量把关时，只能手写"起 N 个验证 agent → 收结构化投票 → 自己判 pass/fail → 聚合反馈"的样板，判定语义随脚本漂移；调度模式已打磨的 reviewer 提示词（verifier/inspector/challenger）脚本侧无法复用。`verify()` 把这一整套收敛成原语。

## 数据结构 / 状态机

- `Reviewer{kind: "verdict"|"score", prompt, label?, options?}` —— 一个待跑 reviewer。`kind` 决定投票 schema 与计票池：verdict 投 pass/fail（一票否决），score 投 0~1（平均分 ≥ 阈值）。
- `VerifyVote{kind, decision?, score?, feedback}` —— 单 reviewer 的解析后投票；字段为 `None` 表示未投/畸形。
- `VerifyResult{verdict: "pass"|"fail"|None, votes, feedback, passed}` —— 一轮判定结果；`verdict=None` = undecided。
- `settle_verify_tally(tally, threshold=0.85)` —— 纯函数判定：verdict 池任一 fail → fail、未投满 → undecided；score 池平均 < 阈值 → fail、未投满 → undecided；两池取最严格。

## 决策

1. **`verify()` 落在 engine 层、模块级原语**（`from swarmflow import verify`），与 `agent()`/`parallel()` 同级；`build_reviewers` 是业务侧辅助（`workflow/review.py`），把 `{type, instruction}` + 产物（文本/路径）渲染成 `Reviewer`。engine 只认 `Reviewer{kind, prompt}`，保持业务无关（铁律 1）。
2. **组合 `agent()`，不新增 provider 方法**：每个 reviewer 是一次带结构化 schema 的 `agent()`，经 `parallel()` 派发（每票独立 journal 结构键），天然继承 journal 续跑、并发门禁、预算、progress 事件、MockBackend 兼容。
3. **判定逻辑在 engine 内置一份** `settle_verify_tally`（约 20 行，一票否决 + 平均分阈值），不 import 业务 `agent/scheduling/verdict.py`（铁律 1）；与调度模式语义一致但独立维护。tally key 用中性名 `score_*`（而非业务名 `inspector_*`）。
4. **单次判定、返工循环由脚本驱动**：`verify()` 只对一份产物跑一轮，不自动返工；脚本拿到 `feedback` 自行组织执行者重做再 `verify()`。
5. **产物支持文本与文件路径两形态**：`build_reviewers` 的 `deliverable` 接受 `str`（内联）或 `list[str]`（路径，reviewer 用文件工具读取）。
6. **Swarmflow 专用 reviewer 模板**（`swarmflow_reviewer_{verifier,inspector,challenger}.md`，cn/en）：参照调度模式同名模板的核心理念/工作流程，但把投票从 `verify_task` 工具改为结构化输出（`decision`/`score` + `feedback`）；challenger 模板额外加 `{instruction}` 槽以支持多视角对抗。composition 引导写入 `swarmflow.md` 工具描述，给 leader 一个"三道门槛"（verifier 最低门槛 / inspector 质量门槛 / challenger 盲点）的决策启发。

## 拒绝的方案

- **业务级 `verify()` 放 `workflow/` 顶层**：无法以原语身份进入统一 facade/结构键体系，需自行接 engine 的 contextvar/journal 样板。否决。
- **新增 `Provider.verify` / backend 方法**：`agent()` 已封装 journal/门禁/预算/结构化输出，`verify()` 只是编排糖，无需在 backend 重实现。否决。
- **把 verdict 逻辑抽到中性模块与调度模式共用**：改动面大、牵动已稳定的调度路径；`settle_review_tally` 的 tally 结构与业务耦合，抽出来收益低。否决（轻量重复换取铁律 1 不破）。
- **直接复用调度模式 `reviewer_*.md`**：模板硬编码 `verify_task`，Swarmflow worker 无此工具。否决，改出专用模板并标注 drift 关系。
- **产物只支持路径（贴近调度模式）**：脚本里大量内存中间结果是字符串，只支持路径需先落盘。否决，两形态都支持。

## 验证

- `test_verify.py`：`settle_verify_tally` 纯函数（一票否决、score 阈值边界、undecided、空池防御）；`verify()` MockBackend 组合（多 reviewer 汇总、fail 聚合、None/畸形投票 → undecided、空列表报错、并行独立 journal 键）；`build_reviewers`（文本/路径、type→kind、未知 type 抛错、inspector 默认打分表、cn/en 渲染）；`swarmflow.md` 指引断言。
- `tests/unit_tests/agent_teams/workflow/` 全量通过。
- `ruff check` / mypy 对改动文件无新增错误。

## 已知遗留

- 判定逻辑与调度模式 `settle_review_tally` 的合并（若后续决定抽中性共享模块）。
- `swarmflow_reviewer_*` 与调度模式 `reviewer_*.md` 的 drift 同步机制。
- `verify()` 返回 verdict + 聚合文本 feedback，不产结构化 findings 列表；需要"收集结构化发现"的编排仍用裸 `agent(schema=...)`。
