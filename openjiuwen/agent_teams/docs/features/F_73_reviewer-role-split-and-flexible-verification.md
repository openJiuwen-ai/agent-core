# 判断者角色拆分与弹性验证

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-07 |
| 状态 | **已实现** |
| 范围 | `agent/scheduling/`（settle_review_tally、temp reviewer retry、escalation）、`tools/`（reviewer 结构化对象、类型校验、分数投票、tally 分组、clean_reviewers 自动编号）、`models.py`（reviewer DB 格式升级）、`prompts/`（verifier/inspector/challenger 三套系统提示词 + 评分维度表 + dispatch 提示词）、`i18n.py` + `locales/`（新 locale 字符串）、`schema/`（TaskGraphSpec.reviewer 类型变更、verify_vote_threshold 删除） |
| 测试基线 | `python -m pytest tests/unit_tests/agent_teams/agent/test_team_scheduler.py tests/unit_tests/agent_teams/tools/test_tool_variants.py tests/unit_tests/agent_teams/test_verify_gate.py --override-ini="addopts="` → **72 passed** |
| Refs | #751 |
| 关系 | 建立在 F_62（调度模式 runtime + review voting）与 F_63（交接消息两阶段渲染）之上。复用 F_57 工具形态框架（verify_task desc_key 分离）|

## 背景

F_62 引入的 reviewer 机制是单类型的：所有 reviewer 共用一个 `reviewer.md` 提示词，走 `pass/fail` 二元投票，用 `judge(pass, fail, total, threshold)` 做 quorum 判定。这在实际实验中出现几个问题：

1. **角色分工缺失**：所有 reviewer 做的事一样（逐项核对验收标准），没有"检视代码质量"和"发现盲区风险"的分工
2. **重复失败**：每轮 challenger 都能找到 5 个新问题（"能提建议 = fail"的铁律导致 finder 永动），单一排序算法被反复打回 4 轮
3. **僵硬的阈值判定**：`threshold=2/3` 让 leader 无法控制"几个 reviewer 够"，也不能表达"一个 fail 就是 fail"
4. **reviewer 命名负担**：leader 需要为每个 reviewer 起描述性名称，心智开销大
5. **model call 失败无重试**：reviewer harness `run_once` 在模型 API 断连时直接失败，无重试逻辑

## 核心洞察

三条，决定了整个设计的形状：

1. **reviewer 的角色分化由后端裁决逻辑定义，不看 name。** verifier/challenger 都是二元投票，区别在提示词教它们看什么；inspector 完全不同——投 0~1 分数而非 pass/fail。**后缀/type 字段选模板，不透明的裁决逻辑由 scheduler 执行**。
2. **一票否决是最简单也是最安全的判定策略。** 任何 reviewer 投 fail = 整体 fail。省掉 quorum 数学带来的边界情况，且语义明确——"没人能替别人通过的审核"。
3. **每一轮失败都应带上 assignee 的视角。** escalation 前让 assignee 写返工总结，leader 拿到 reviewer 反馈 + assignee 的复盘再做决定。不是"找替罪羊"，是"问题出在哪"。

## 设计

### 1. 三种 reviewer 类型

| 类型 | 投票值 | 裁决方式 | 提示词 | description 来源 |
|---|---|---|---|---|
| verifier | `"pass"` / `"fail"` | 一票否决 | `reviewer_verifier.md` | leader 在 create_task 提供 |
| inspector | `"0.85"` 浮点串 | 全部投完 → 平均 ≥ 0.85 | `reviewer_inspector.md` | leader 提供马克当打分表；empty → 默认 6 维 |
| challenger | `"pass"` / `"fail"` | 一票否决 | `reviewer_challenger.md` | 不需要 |

verifier + challenger 合并为二元票池，跟 inspector score pool 分开判定。任意池 fail → 整体 fail。任意池未全票 → UNDECIDED。

### 2. 投票裁决（数据结构与算法）

**票据存储（`team_review_vote_*` 表）**：`decision` 列保持 VARCHAR，存入 `"pass"`、`"fail"`、`"0.85"` 等值。schema 不变。

**tally 分组（`task_manager.py:get_review_tally`）**：扩展返回 dict，从"计数 pass/fail"变为两组统计：

```python
{
    # 二元票池（verifier + challenger）
    "verdict_pass_count": int, "verdict_fail_count": int,
    "verdict_total": int, "verdict_voted": int,
    # 分数票池（inspector）
    "inspector_count": int, "inspector_voted": int,
    "inspector_scores": {name: float}, "inspector_avg": float | None,
    # 兼容旧字段
    "pass_count": int, "fail_count": int, "reviewer_count": int,
}
```

**settle_review_tally（`verdict.py`）**：替换原 `judge()`：

```
binary pool: voted < total → UNDECIDED; fail > 0 → FAIL
inspector pool: voted < total → UNDECIDED; avg < 0.85 → FAIL
全部 pass → PASS
```

删除了 `verify_vote_threshold` spec 字段（与 `judge()` 耦合的参数），判定从可配置降为固定语义。

### 3. reviewer_id 自动编号

leader 在 create_task 里只传 `type` 和 `description`。`_clean_reviewers()` 按 type 自动编号：`verifier_1`、`inspector_1`、`challenger_2`，计数器在单次调用内按类型递增。reviewer_id 从工具 schema 中完全删除——模型不可见，代码层自动补。

### 4. temp reviewer harner 三度重试

181001（model call failed）错误触发最多 3 次重试，每次重建 harness（`run_once` 每次执行后 tear down 工具集）。sleep 间隔：attempt 0→0s, 1→2s, 2→4s。全失败时弃 `_review_dispatched`，下一轮 scan 重 dispatch。

### 5. DB 格式原地升级

`reviewer` DB 列（VARCHAR/JSON）从旧格式 `["name1", "name2"]` 升级为 `[{"type": "verifier", "reviewer_id": "name1", "description": ""}]`。`models.py:reviewers()` 读时自动检测格式并升级（不修改 DB 行），`reviewer_details()` 返回完整结构化列表。

#### 6. enable_task_verification 生命周期

三层控制：用户 spec（天花板） + leader override（AND 语义）。effective = `spec AND (leader if leader else true)`。`false` 时 scheduler 跳过所有 IN_REVIEW task，create_task/update_task 自动清空 reviewer 字段。

#### 7. 升级流程增强

Round 耗尽时 scheduler 同时：
- escalation 注入 leader（一条 i18n 消息，含所有 feedback + inspector 平均分）
- 向 assignee 发送返工总结请求（`scheduler_rework_summary.md` 模板，assignee 通过 `send_message(to=leader)` 回复）

Leader 在 inbox 中同时看到 escalation 和 assignee 的总结后再决策（retry/replan/rollback）。summary 请求去重通过 `_summary_requested` set。

#### 8. 提示词分层

| 文件 | 职责 | 数量 |
|------|------|:----:|
| `reviewer_verifier.md` (cn+en) | 验证者系统提示词，`{reviewer}`=reviewer_id，`{description}`=实际侧重点 | 2 |
| `reviewer_inspector.md` (cn+en) | 检视者系统提示词，`{description}`=打分维度表 | 2 |
| `reviewer_challenger.md` (cn+en) | 挑战者系统提示词，无 `{description}` | 2 |
| `reviewer_dims_for_inspector.md` (cn+en) | 默认 6 维度通用打分表 | 2 |
| `dispatch_scheduled_leader.md` (cn+en) | leader 角色提示词，含 reviewer 分配原则 + inspector rubric 编写指南 + enable_task_verification 引用 | 2 |
| `scheduler_review_request.md` (cn+en) | 审查请求模板，含 `{{task.reviewer_description}}` 占位 | 2 |

## 拒绝的方案

1. **Post-hoc verification layer（像PR #2074）**：在 task COMPLETED 后异步调模型做六维度评分，写入 TEAM_MEMORY.md。不阻塞 task、不驱动返工——分数只能给人看，不能给 assignee 用。放弃，因为"阻塞式 reviewer 返工"更符合团队质量保障的定位。

2. **可配置的阈值/加权投票**：原本保留 `verify_vote_threshold`（2/3 quorum），允许用户自定义。实验证明 threshold 很难调——太高等于 one-vote all-pass，太低等于 majority。统一用一票否决 + inspector 平均分替代。

3. **challenger 永远 pass（不阻塞）+ 后续发送建议**：让 challenger 只提建议不投票。但 assignee 在 IN_REVIEW 外收到建议也无法返工；改在 `_settle_pass` 时附 challenger 建议给 assignee，但 task 已经完成。放弃——challenger 现在是正常的 pass/fail 投票者，只是提示词教它"pass 是默认结果，只有阻塞性缺陷才 fail"。

4. **trigger 解析 reviewer 后缀（`-inspector`、`-challenger`）映射模板**：最初设计用名称后缀匹配模板。放弃——改为结构化 `type` 字段，name 与 type 完全解耦。

## 已知遗留

- inspector 浮点投票类型路径没有单测（当前没引入 bug，生产端到端验证通过）
- 混合类型 reviewer（verifier + inspector + challenger）的单测未提供
- `181001` 重试魔法字符串没有命名常量
- `test_team_scheduler.py` 绑定 `AsyncMock` 为 `task_verification_enabled()` 返回值——正常可用但不够抗造
- `_dispatch_to_reviewer` 只支持 temp harness 路径；旧 `_send_as_leader` 路径从未实现
- 日志 `judge-pass/judge-fail` 的 `tally(pass= fail= total=)` 字段中 `total` 包含 inspector 总数，log 含义容易误导

## 实现补充

### evolve #1: 初始角色拆分 + 一票否决

2026-08-04 ~ 08-05：最初实现多类型 reviewer，用 `settle_review_tally` 替代 `judge()`，引入 reviewer_id 自动构造。leader 按 prompt 规则分配 reviewer 类型。verifier/inspector/challenger 三类提示词并行创建。

### evolve #2: description 正确语义分离

leader 提供 `description` 字段包含 5x 性能阈值，update_task 时只更新了 content 但没有更新已 spawn 的 reviewer prompt，导致 reviewer 执着于旧阈值不放。修正：`description` 应只描述验证方法，不再绑具体验收标准。

### evolve #3: challenger prompt 重构

challenger 原来 "能提建议 = fail" 逻辑导致每轮找到 5 个新问题，迭代 4 轮没有收敛趋势。重构后改为 "pass = 默认结果，只有在阻塞性缺陷（crash、数据错、安全漏洞、违反核心验收标准）时才投 fail"。

### evolve #4: inspector 评分指南独立化

硬编码的 6 维评分表抽下到 `reviewer_dims_for_inspector.md`，leader 可通过 `description` 字段提供任务专属打分表（接口设计、调研等示例），inspector prompt 内 `{description}` 占位符动态注入。

### evolve #5: enable_task_verification 启用

`enable_task_verification` flag 原本是纯 prompt 级 opt-in 开关，leader 不得覆盖。更正为 AND 语义（spec × leader 双人都需 pass），并整合进 scheduler、create_task、update_task 的 reviewer guard 中。`build_team` 工具描述里新增完整使用小节。

### evolve #6: escalation 流程增强

Round 耗尽时 scheduler 不仅向 leader 发 escalation，还向 assignee 发返工总结请求。assignee 通过 inbox 将总结发给 leader，leader 拿完整上下文做 retry/replan 决策。scheduler 中的 meta_rework_summary 邮件去重通过 `_summary_requested` set 实现。

### evolve #7: temp reviewer 三度重试

181001（模型连接失败）初次发生时 reviewer harness 工具集被 tear down，后续调用缺工具。改正方式：每次重试都重建 harness。最多 3 次 attempt，全失败时弃 `_review_dispatched`，让下一轮 scan 重新 dispatch。
