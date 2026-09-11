# Swarmflow verify 原语规约

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/workflow/` |
| 最近一次修订日期 | 2026-08-26 |
| 关联 feature | F_86_swarmflow-verify-primitive.md |

## 范围 / 边界

管：`verify()` 原语及其业务辅助 `build_reviewers` 的公共契约——调用形态、数据模型、判定语义、reviwer 类型到 kind 的映射、产物两形态、与编排引擎的接缝。

不管：`verify()` 如何驱动返工（那是脚本控制流职责）；调度模式的 `verify_task` / `settle_review_tally` / `reviewer_*.md`（保持原样，见 S_22）。

## 不变量

- **I-1 铁律 1**：`workflow/engine/` 不得 import 任何 `openjiuwen.agent_teams` 业务模块。`verify()` 只认 `Reviewer{kind, prompt}`，判定 `settle_verify_tally` 在 engine 内置，不 import 业务 `verdict.py`。
- **I-2 单次判定**：一次 `verify()` 只对一份产物跑一轮，不内置返工循环、不等待重做。
- **I-3 不臆断**：任一 reviewer 未投票（`agent()` 返回 `None`）或畸形投票时，`verdict=None`（undecided），绝不静默判 pass。
- **I-4 空列表拒绝**：`verify()` 拒绝空 reviewer 列表（抛 `WorkflowError`）；`settle_verify_tally` 的"无池 → pass"是防御性默认，经 `verify()` 不可达。
- **I-5 中性 tally key**：engine 的 tally dict key 用中性 `score_*`（`score_count`/`score_voted`/`score_avg`），不含业务角色名（如 inspector）。
- **I-6 组合 `agent()`**：每个 reviewer 是一次带结构化 schema 的 `agent()`，经 `parallel()` 派发，每票独立 journal 结构键；不新增 `Provider.verify` / backend 方法。

## 接口契约

### `verify(reviewers, *, threshold=0.85, label=None, phase=None, options=None) -> VerifyResult`

- `reviewers: Sequence[Reviewer]`，必须非空，否则 `WorkflowError`。
- `threshold: float`：score 池平均分门槛，默认 0.85。
- `label` / `phase` / `options`：透传给每个 reviewer 的 `agent()`。
- 返回 `VerifyResult{verdict, votes, feedback, passed}`。

### `build_reviewers(deliverable, specs, *, acceptance=None, language="cn") -> list[Reviewer]`

- `deliverable: str | Sequence[str]`——文本内容（内联）或文件路径清单（reviewer 用文件工具读取）。
- `specs: Sequence[dict]`——`{type, instruction?, label?}`。`type ∈ {verifier, inspector, challenger}`，未知 type 抛 `ValueError`；缺失 type 默认 `verifier`。
- `acceptance`：验收标准，注入每个 reviewer 提示词；`language`：`cn`/`en`。

### Reviewer 类型 → kind 映射

| type | kind | 投票 | 计票 |
|---|---|---|---|
| verifier | verdict | pass/fail | 一票否决 |
| challenger | verdict | pass/fail | 一票否决 |
| inspector | score | 0~1 | 平均分 ≥ 阈值 |

## 数据结构

- `Reviewer{kind, prompt, label?, options?}`：kind 决定 schema/计票池；prompt 是业务层渲染好的完整提示词（`build_reviewers` 从 `swarmflow_reviewer_*` 模板渲染）。
- `VerifyVote{kind, decision?, score?, feedback}`：`decision`（verdict，True=pass）/ `score`（score，0~1）任一，按 kind；`None` = 未投/畸形。
- `VerifyResult{verdict, votes, feedback, passed}`：`verdict` 为 `"pass"`/`"fail"`/`None`（undecided）。
- tally dict：`{verdict_total, verdict_voted, verdict_fail_count, score_count, score_voted, score_avg}`。

## 与其它 spec 的关系

- S_22（scheduling runtime）：判定语义（一票否决 + 阈值）同源，但本原语的 tally 在 engine 独立实现。
- S_18（swarmflow runtime）：`verify()` 作为原语加入编排引擎，共享 journal/并发/预算机制。
