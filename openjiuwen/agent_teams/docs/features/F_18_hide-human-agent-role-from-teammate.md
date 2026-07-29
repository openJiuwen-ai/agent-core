# Hide Human-Agent Role from Teammate Prompts (Switchable)

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-16 |
| 范围 | `openjiuwen/agent_teams/schema/blueprint.py`、`openjiuwen/agent_teams/agent/agent_configurator.py`、`openjiuwen/agent_teams/rails/team_policy_rail.py`、`openjiuwen/agent_teams/prompts/sections.py`、`openjiuwen/agent_teams/docs/specs/S_09_prompts-and-rails.md`、`tests/unit_tests/agent_teams/test_hitt.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/`：1021 passed / 16 skipped；新增 5 条 hitt expose-flag 锁定用例 |
| Refs | `#751` |

## 背景

排查"成员 role 是否会被泄漏到其它成员的 LLM 上下文"时定位到一处**有意为之**的暴露：
`build_team_hitt_section` 给 `TeamRole.TEAMMATE` 也渲染一段 section，列出全量
`human_agent` 成员的 `member_name` 并标注"真实人类 / real humans"，注入到每个
teammate 的 system prompt 中。

闭环路径：

- `rails/team_policy_rail.py:107` `TeamPolicyRail.__init__` 拉 `team_backend.human_agent_names()`。
- `rails/team_policy_rail.py:189` `_build_static_sections` **不分 role** 把名单传给 `build_team_hitt_section`。
- `prompts/sections.py` 中 `build_team_hitt_section` 按 role 派发，TEAMMATE 走 `_hitt_section_teammate_cn/en`，输出：

  ```
  # HITT — 与人类成员协作
  团队里存在下列人类成员（真实人类）：m1, m2 …
  ```

- `before_model_call` 把这段写进每个 teammate 的 system prompt。

设计原意（旧 docstring 自述）：让 teammate "exactly whom to address via send_message"
+ 提醒 plain text 对真人不可见 + 提示真人可能持有 teammate 没有的决策权。这里有两层
诉求被混在一起：

1. **教导价值**（普适）：跨 peer 通信走 `send_message`、容忍响应延迟、不要假设 plain text 可见。
2. **身份暴露**（敏感）：具体哪些 peer 是 human_agent。

第 1 条对每个 teammate 都有价值，第 2 条与"成员 role 不对外公开"的安全姿态冲突。把
两者合并在同一段 section 里就会"要么全留要么全删"，缺乏弹性。

## 决策

**两层处理**：

1. **拆教导与身份**：把"教导"做成 role-neutral 匿名段（不列名单、不说"real humans"、不
   暗示为什么有 peer 会异步），把"身份暴露"做成需要显式打开的开关变体。
2. **加 spec 级开关** `TeamAgentSpec.expose_human_agents_to_teammates: bool = False`，
   默认 `False`（fail-safe，保护 role 隐私）。

具体落地：

| 文件 | 改动 |
|---|---|
| `schema/blueprint.py` | `TeamAgentSpec` 加 `expose_human_agents_to_teammates: bool = False` + docstring（说明对 LEADER / HUMAN_AGENT 无影响） |
| `agent/agent_configurator.py` | 构造 `TeamPolicyRail` 时透传 `expose_human_agents_to_teammates=spec.expose_human_agents_to_teammates` |
| `rails/team_policy_rail.py` | `__init__` + `_build_static_sections` 接受新参数；唯一消费点是 `build_team_hitt_section(..., expose_human_agents_to_teammates=...)` |
| `prompts/sections.py` | `build_team_hitt_section` 加参数；新增 `_hitt_section_teammate_anonymous_cn/en`（默认）+ 复活 `_hitt_section_teammate_cn/en`（True 路径）；模块头部 P:12 注释 + docstring 同步说明 |
| `tests/unit_tests/agent_teams/test_hitt.py` | 替换前一版"TEAMMATE 拿 `None`"用例：新增 5 条 level0 用例，覆盖 (a) 默认 anonymous 不含名单 / 不含 "真实人类" cn+en，(b) 开关打开后含名单 cn+en，(c) 开关对 LEADER / HUMAN_AGENT 无效果 |
| `docs/specs/S_09_prompts-and-rails.md` | 第 7 条铁律新增子条：TEAMMATE 默认 anonymous 变体 + 开关切换语义；元信息修订日期 + 关联 feature 刷新 |

### Anonymous 段的措辞约束

匿名段必须**只承载普适教导**，不能暗示 role 存在：

- ❌ "可能存在真人成员" — 字面承认 human_agent，等于暗示泄漏
- ❌ "某些成员通过 Inbox 驱动" — 暗示了 HITT 机制
- ✅ "部分 peer 不会主动读取你的 plain text 输出，且回复节奏可能慢于一般 LLM 队友" — 描述行为
  而不命名身份；teammate 即使推断也只能得出"有 peer 比较慢"，无法反推具体名字

cn / en 文案严格对齐这条约束，verbatim 见 `prompts/sections.py:_hitt_section_teammate_anonymous_cn/en`。

## 拒绝的方案

- **(B) 完全不渲染 teammate HITT section**（此 feature 的第一版方案）：拒绝。失去
  普适教导价值——cross-member `send_message` 契约 + 延迟容忍 + 不要催促 这些在
  teammate prompt 里有真实作用，不该跟 role 暴露绑死。
- **(C) 保留名单但伪装成"通信约束名单"**（"以下成员只能通过 send_message 联系"）：
  拒绝。LLM 一眼能推出"这些名字大概是真人"，是 cosmetic 改动不解决根因。
- **运行时按 caller role 在 backend 层裁掉 `human_agent_names()` 返回值**：拒绝。
  `human_agent_names()` 是 leader 路径调度真实需要的 ground truth，不能因为
  prompt 层一个误用就砍残上游 API。修复锚在 prompt 装配层是正确分层。
- **在 `build_team` 工具上加 LLM-facing 参数**：拒绝。这是部署期 policy，不是
  leader LLM 该决定的事；放 spec 而不是工具入参。
- **两个独立开关**（一个控制是否渲染 section、一个控制是否暴露名单）：拒绝。
  4 种组合里只有 2 种有意义（"渲染但不含名单" / "渲染且含名单"），另两种
  ("不渲染但开了名单 flag" 等)是噪声。单 flag 二态足够。
- **把通用教导下沉到 `teammate_policy.md`，HITT section 在 False 时彻底不渲染**：
  拒绝。`teammate_policy.md` 承载的是"角色身份和决策原则"，把"如何对待异步 peer"
  这种**条件性**协作守则塞进去会违反 prompts 子模块的分层（参见
  `prompts/CLAUDE.md` 编辑规则 5）：teammate_policy 在所有团队都生效，但这条
  教导只在团队真有异步 peer 时才有意义。

## 验证

- 单元测试：
  - `tests/unit_tests/agent_teams/test_hitt.py`：61 passed（含 5 新增 expose-flag 用例 + 1 既有 leader/human_agent 不受影响断言）。
  - `tests/unit_tests/agent_teams/test_team_policy_rail.py` + `test_policy.py` + `test_team_agent.py` + `test_team_section_cache.py`：48 passed。
  - 全 `tests/unit_tests/agent_teams/`：1021 passed / 16 skipped，零回归（spec 加字段后 spec 序列化 / checkpoint / model_copy 等路径均通过）。
- 行为基线：
  - 默认（`False`）：teammate 拿 anonymous section，文本中不含任何具体
    `member_name`、不含 "真实人类" / "real humans" 字样。
  - 显式 `True`：teammate 拿 legacy roster section，行为等同改动前。
  - LEADER / HUMAN_AGENT 在 True/False 下产出**字节完全相同**的内容（`leader_off.content == leader_on.content`），有断言锁定。

## 已知遗留

- 排查报告里识别到的其它"内部 / 日志 / Monitor 出现 role"的位置（`TeamOutputSchema.role`、
  EventBus 日志、handler 内部 `is_human_agent` 布尔分支、`i18n.py` 的死模板
  `hitt.human_agent_spawned`）**均不进入 LLM prompt**，本次不动。后续若发现其中
  有任何一处被引入新的 LLM-side 渲染路径，需要复用本 feature 的策略
  （prompt 装配层拦截，开关决定是否暴露，不动 backend API）。
- `i18n.py:96 / 203` 的 `hitt.human_agent_spawned` 模板是 grep 无引用的死代码，
  本次为了改动聚焦不一并清理；下次触碰 `i18n.py` 时顺手删除即可。
- 现在 anonymous 段中"回复节奏可能慢于一般 LLM 队友"已经是非常弱的 hint。如果业务
  方仍想消除这个 hint，下一步可以再加一个更弱的 anonymous 变体（"对所有 peer 一律
  走 `send_message`，不要假设可见性"）；但目前的措辞已经够 role-private，且保留了
  最有价值的"耐心等待 + 不要催"教导，没必要再分层。
