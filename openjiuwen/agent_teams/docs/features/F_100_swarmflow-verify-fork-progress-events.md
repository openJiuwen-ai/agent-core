# Swarmflow verify/fork 结构化进度事件（VERIFY_STARTED/SETTLED + fork 父标识）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-18 |
| 范围 | `workflow/engine/progress.py`（ProgressKind + 事件字段）、`workflow/engine/primitives.py`（verify() 事件发射、AgentSession fork 父标识）、`schema/events.py`（WorkflowProgressTeamEvent +5 字段）、`workflow/tool_swarmflow.py`（字段桥接）、测试 `test_verify_fork_progress_events.py`（新增）；下游消费在 jiuwenclaw（workflow_state 折叠 + web/TUI 渲染，另仓提交） |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/` 离线 UT 28 passed（含新增 7）；真实 LLM ST（verify_haiku + fork_explore，增强断言本地不提交）2026-09-18 实跑双 PASS：verify 轮事件 started=1/settled=1、vote names={verifier-1, inspector-1}；fork lineage opt-cost/opt-perf 均 node_type=agent_session_fork + parent_session_id=architect |
| Refs | #751（SDD-0017 verify / SDD-0014 fork 的可观测性补全） |

## 背景

verify() 的轮次边界与 verdict 此前只走两条 LOG 文本（`verify: dispatching N reviewer(s)` / `verify: verdict=...`），前端要翻日志区才能看到裁决；undecided（有 reviewer 未投）完全不可见，用户看起来像"还没跑完"而不是"基础设施故障建议重试"。fork 子会话虽有 `node_type="agent_session_fork"` 标记（SDD-0014 §8），但事件不带父会话标识，"从谁分叉"的父子关系在事件流里不存在，前端画不出分叉边。

## 决策

1. **新增 `VERIFY_STARTED` / `VERIFY_SETTLED` 两种事件**，不新建事件通道：`WorkflowProgressEvent` 加 4 个 verify 专有字段（`verify_reviewers` / `verify_verdict` / `verify_threshold` / `verify_votes`），加法兼容。STARTED 在 reviewer 扇出前发（reviewer 数 + 阈值），SETTLED 在计票后发（verdict + 逐票明细）。journal 重放时事件按原序重发，下游按 label 幂等折叠。

2. **settled 的每票带 `name`，镜像 `_reviewer_call` 的 label 规则**（`reviewer.label or f"{base}-{i}"`）。这是消费端能把票与 reviewer 自己的 agent 节点对上号的唯一途径——票的 name 必须与 reviewer `agent()` 的 label 逐字一致，否则前端组卡的 chips/折叠无法关联节点。decision 从引擎内部的 bool 映射回 `"pass"/"fail"`（None = 未投/畸形，绝不静默当通过）。

3. **fork 父子关系用字段不用新事件**：`AgentSession` 增加 `_parent_session_id`（fork() 时取父的 `self._label`），fork 子会话每次 `send()` 的 `AGENT_STARTED` 携带 `parent_session_id`。选父 **label** 而非 `_member_name`：前端 session 卡按 `name + node_type` 归组，label 是跨层唯一稳定键；member_name 是后端内部标识，前端不可见。父无 label 时为 None，前端优雅降级（不画边）。

4. **团队事件桥接逐字段透传**：`WorkflowProgressTeamEvent` 加同名 5 字段，`tool_swarmflow._build_progress_message` 显式搬运——该桥是逐字段构造（非 **payload 展开），漏改即字段在引擎→团队事件一跳被静默丢弃。

## 拒绝的方案

- **verify 发独立 VOTE 事件（每票一条）**：票已在 reviewer 自己的 AGENT_COMPLETED.outcome 里，再发一条是重复通道；SETTLED 聚合一次携带全部票，事件量 O(轮次) 而非 O(票数)。
- **`parent_session_id` 传 member_name**：前端无从解析（不进快照的 agent 字段），需要连带改后端折叠与快照 schema，收益为零——label 已满足跨层对齐。
- **VERIFY_SETTLED 携带聚合 feedback 全文**：feedback 是逐票 feedback 的拼接（`_aggregate_feedback`），票里已有，不重复传输。

## 验证

- 离线 UT（`test_verify_fork_progress_events.py`，MockBackend，7 例）：STARTED/SETTLED 各恰一次且有序；votes 的 name 与 reviewer 节点 label 对齐（含 `base-i` 回退）；SKIP reviewer → verdict=None + `voted=False`；fail 映射；fork 子会话 AGENT_STARTED 带 `parent_session_id="arch"` 而父/普通 session 恒为 None。
- 真实 LLM ST（`agent_team_swarmflow_verify_fork_st.py`，本地增强版不提交）：verify 轮事件断言 reviewers=2/threshold=0.6/vote_names={verifier-1, inspector-1}/settled verdict 与脚本 log 一致；fork 断言 opt-cost/opt-perf 的 agent_started 节点 `node_type=agent_session_fork` 且 `parent_session_id=architect`，父 architect 自身 turn 无父标识。
- 下游消费（jiuwenclaw）：workflow_state 折叠组卡 UT 126 passed；web/TUI 渲染详见 jiuwenclaw 仓设计文档（doc/analysis/2026-09/2026-09-18-swarmflow-verify-fork-fullstack-implementation.md）。

## 已知遗留

- 并发 verify 同 label（parallel 内两个不传 label 的 verify）会在下游折叠成同组卡；sequential rework（`label=f"verify-{rnd}"`）不受影响。组序号方案待真实场景反馈。
- votes 的 feedback 全文随 SETTLED 传输，8 reviewer 量级 ~数 KB，尚无分页诉求；超出时考虑 summary + get_agent 按需拉取。
- AOCI 认知层维护（aoci.txt）本次未执行——当前会话无 aoci MCP 工具可用，受影响条目待下次具备工具的会话收尾。
