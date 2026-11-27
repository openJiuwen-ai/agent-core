---
id: B_20261118_1142_shared-templates-name-leader-only-tools
title: 全员共享提示词模板点名 leader 专属工具误导 teammate
type: B
author: zhanglongmin
author_email: zhanglongmin@huawei.com
modules:
- openjiuwen/agent_teams
files:
- openjiuwen/agent_teams/prompts/cn/inbound_tags.md
- openjiuwen/agent_teams/prompts/en/inbound_tags.md
- openjiuwen/agent_teams/prompts/cn/hitt_teammate_anonymous.md
- openjiuwen/agent_teams/prompts/en/hitt_teammate_anonymous.md
- openjiuwen/agent_teams/prompts/cn/tool_namespace.md
- openjiuwen/agent_teams/prompts/en/tool_namespace.md
- tests/unit_tests/agent_teams/prompts/test_shared_template_tool_names.py
designs: []
design_note: 日常文案修复，无独立设计文档；行为依据 F_76（提示词分层与渐进式披露）、F_122（framework 建队的 <team-policy> 投递）与 prompts/AGENTS.md 编辑规则 2。
---

# 全员共享提示词模板点名 leader 专属工具误导 teammate 修复记录

## 现象与影响

用户报告：`inbound_tags.md` 的 `<team-policy>` 条目原文——"只在团队由系统预先建好、而你没有调用 `build_team` 的情况下出现：随第一条输入投递一次，之后长期有效，与 `build_team` 返回的准则等价"——对 teammate 有误导，会让它以为自己持有 `build_team` 工具。

事实链：

- `inbound_tags.md` 是**全员常驻**模板（`TeamPolicyRail` 要求静态 section 成员间逐字一致以保前缀 KV cache 稳定），teammate 必读；
- `<team-policy>` 块的真实接收者只有 framework 建队（`bootstrap_mode="framework"`）的 leader（F_122；其验证用例明确断言 teammate 不出块），另有外部 CLI 成员的系统提示词容器形态（F_114，`<team-policy tools="...">`）；
- `build_team` 在 `LEADER_ONLY_TOOLS`（`tool_permissions.py`），teammate 任何 dispatch 模式下都没有；
- 原文以"你"预设读者可能调用 `build_team` 并两次点名该工具——正是 prompts/AGENTS.md 编辑规则 2 所说"读得到、用不了"的提示词比缺失更糟。

排查同时发现两处同类问题：

1. `hitt_teammate_anonymous.md`（HITT 团队默认的 teammate 契约，`expose_human_agents_to_teammates=False` 时生效）第 6 行："如需推进，请提交 `update_task` 或与 leader 协商"。`update_task` 同在 `LEADER_ONLY_TOOLS`：autonomous 成员完全没有，scheduled 成员仅在 `member_subtasks_enabled()` gate 下持有子任务形态（`MemberSubtaskUpdateTool`，只能改自己父任务的子任务）。HITT 与 dispatch_mode 正交，autonomous HITT teammate 必读此模板。该句自 HITT prompt 外置化（`2a4cb6023`，2026-07-03）起就存在，历史遗留。
2. `tool_namespace.md` 例子列表含 `claim_task`——scheduled 外部 CLI 成员没有该工具（`MEMBER_ONLY_TOOLS_SCHEDULED` 不含它）。语境是裸名命名空间归属声明而非调用引导，危害小，属顺手对齐。

## 关联设计与当前契约

- `designs` 为空：日常文案修复，未命中 jw-design 触发条件。
- F_122 §D3 对本句的要求原是中立的："`inbound_tags.md`（cn/en）补一句：`<team-policy>` 是团队协同准则，随首条输入到达一次、长期有效"。实际落地写入了 `build_team` 引用与 leader 人称预设，属实现偏离设计。
- leader 视角的"没有 `build_team` 这一步，不要去找这个工具"由 `leader_prebuilt.md` 独立承担（F_122 起手段），本次不改变该分层——inbound_tags 去掉工具引用不丢失 leader 侧信息。
- `tool_namespace.md` 的容器语义声明（"本策略区域"）不受本次影响。

## 根因与修复决策

根因：全员逐字一致的模板在描述投递机制时以 leader 为假想读者使用第二人称（"你没有调用 `build_team`"），点名了其余角色不持有的工具。prompts/AGENTS.md 编辑规则 2 的原文只覆盖 capability gate 维度（"能力关掉时文案消失"），没有覆盖角色差异维度，使这类表述在 review 中漏过。

修复（cn/en 成对）：

1. `inbound_tags.md` `<team-policy>` 条目改为角色中立表述：只讲标签语义（团队协同准则）、投递特征（只在部分成员上出现、随首条输入投递一次、长期有效不撤回不刷新）与预防性说明（"没有收到它也是正常的，不是内容缺失"，仿 `from="user"` 条目"名册里找不到 user 是正常的"的既有写法）；删除全部 `build_team` 引用。
2. `hitt_teammate_anonymous.md` 删掉"请提交 `update_task`"，保留"与 leader 协商"出口（最小 diff，不新增行为指导）。
3. `tool_namespace.md` 例子 `claim_task` → `workspace_meta`（SHARED_TOOLS 成员，全模式通用）。

拒绝的备选：

- **按 role 分化 inbound_tags 模板**：违反 `TeamPolicyRail` "成员间逐字一致"约束（前缀 KV cache 稳定），系统提示词会按角色分叉。
- **保留 `build_team` 字样加 caveat（如"若你有此工具"）**：仍然点名工具，不解决"让模型去够它"的问题；task_state_autonomous 的先例（连"本模式没有验证闸"都不写）明确否定这种写法。
- **在 inbound_tags 里完整解释外部 CLI 成员的 `<team-policy tools>` 容器**：容器语义已由 `tool_namespace.md` 声明，两处重复必然漂移；且对进程内成员是纯噪音。

## 回归证据与适用范围

- 候选：分支 `fix_0.1.21_1` 工作区（本次修复未提交前的全部改动）。
- 新增 `tests/unit_tests/agent_teams/prompts/test_shared_template_tool_names.py`（4 用例）：cn/en 双语断言 `inbound_tags` 模板不含 `build_team`、`hitt_teammate_anonymous` 模板不含 `update_task`，4 passed。
- 受影响范围整体回归：`pytest tests/unit_tests/agent_teams/prompts/ tests/unit_tests/agent_teams/test_team_policy_rail.py tests/unit_tests/agent_teams/test_team_context_policy.py` → **175 passed, 1 deselected**。deselected 的 `test_the_inner_agent_gets_the_very_session_the_native_holds` 在本环境因缺 `opentelemetry.exporter` 包于 import 阶段失败（`ModuleNotFoundError`），属环境基线失败，与本次改动无关（改动仅涉及 `.md` 模板、新增测试文件与 AGENTS.md）。
- 既有断言核对无冲突：`test_member_system_prompt.py:141-178`（查 `<team-policy tools=` 形态）、`test_inbound_tags_document_every_team_state_tag`（查三个标签名）均不锁本次修改的措辞。
- 未验证：真实 LLM 行为——提示词文案的防误导效果无法用单测证明，需线上观察 teammate 是否仍尝试调用 `build_team` / `update_task`。

## 防错模式与资料同步

- 提炼模式：**全员逐字一致的共享模板不得点名任何单角色持有的工具**（编辑规则 2 的角色维度推广）。已写回 `prompts/AGENTS.md` 编辑规则 2（能力 gate 句之后补"角色维度同理"一句）。
- 本次同步：`prompts/AGENTS.md` 编辑规则 2；S_09 无需修订（未描述该条目措辞，cn/en 逐字一致约束仍满足）；F_122 不追补（投递行为未变，仅文案纠偏）。

## 遗留与后续

- `inbound_tags.md` 第 18 行仍把 `<team-policy>` 列为顶层平级标签——对外部 CLI 成员（其系统提示词整体包在 `<team-policy tools>` 容器内，F_114）该层级关系不严格成立。容器语义与历史块语义共用一个元素名的澄清属设计层面取舍，本次不动，留待后续需要时单独评估。
- 其余无遗留。
