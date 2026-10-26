# Human Agent — User-Driven `send_message` Relay

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-15 |
| 范围 | `openjiuwen/agent_teams/tools/team_tools.py`、`openjiuwen/agent_teams/tools/team.py`、`openjiuwen/agent_teams/tools/CLAUDE.md`、`openjiuwen/agent_teams/prompts/sections.py`、`openjiuwen/agent_teams/docs/specs/S_07_interaction-views-and-hitt.md`、`openjiuwen/agent_teams/docs/specs/S_08_team-tools-contract.md`、`tests/unit_tests/agent_teams/test_hitt.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/test_hitt.py` 通过 |
| Refs | `#751` |

## 背景

`HumanAgentInbox` 已经能让外部用户用 `@<member>` mention 路由直接对团队成员说话——但前提是用户**主动使用 mention 语法**。实际使用中，用户经常用自然语言下指令：

- 「告诉 leader 我去开会了，30 分钟回来」
- 「回复 `dev-1` 我同意他的方案」
- 「跟 PM 说一声需求变更」

这些都是用户**明确要求 avatar 替自己传话**的场景。但 F_04 的初始设计把 `send_message` 从 `HUMAN_AGENT_TOOLS` 里去掉了，理由是「用户应该用 Inbox `@member` 路由，不让 LLM 控对外发声」。结果是：用户用自然语言下达转发指令时，avatar 既无法 plain-text（团队看不到），又没有工具可调，整个语义闭环卡死。

**真问题**：avatar 缺一条「代用户转发」的出口。
**伪问题**：让 avatar 完全闭嘴并不能逼用户改用 mention 语法——只会让交互崩在 avatar 这一侧。

## 决策

### 1. 把 `send_message` 加回 `HUMAN_AGENT_TOOLS`

```python
HUMAN_AGENT_TOOLS = {
    "view_task",
    "member_complete_task",
    "send_message",
}
```

`SendMessageTool` 不做任何 caller-role 分支——和 leader / teammate 用同一份实现。`_auto_start_members` 内部本来就检查 `self._team.is_leader`，human_agent 不会触发 startup，无额外副作用。

允许的 `to` 形态保持完整（单个 `<member_name>` / 多人列表 / `"*"` 广播 / `"user"`），不在工具边界设限——见下文「拒绝的方案」。

### 2. 在 `team_hitt` prompt section 里强约束「用户驱动」语义

`_hitt_section_human_agent_cn` / `_en` 把过去的「你**没有 `send_message`**」翻面成「你**有 `send_message`**，但它是用户驱动的转发通道」，并明确列出四条使用规则：

1. **仅当**用户在当前轮 Inbox 输入里**明确**要求转告 / 通知 / 回复某成员，才调用 `send_message`。`to` 必须是用户点名的那个成员；`content` 要以「用户 `<member_name>` 让我转告：…」开头标明代发身份。
2. **不允许**把团队其它成员发给 avatar 的消息当作触发条件——那些消息不在 avatar 上下文里，运行时已转给用户。
3. **不允许**在没有用户明确转发指令时主动 broadcast / send_message。
4. 用户的指令本身只是对 avatar 说话（「帮我查任务 #3」）时，**不要**反向问团队，直接调对应工具或回给用户。

「行为准则」段补一句兜底：「如果用户没明确让你转告，就不要触发 `send_message`」。

### 3. 同步刷新 spec 与 tools/CLAUDE.md

- `S_08`「角色集合互相对称」（不变量 10）：删除「Human agent 不得拿到 `send_message`」的说法；改述为「可以拿到，但语义约束在 prompt 层」。
- `S_08` 集合表：`HUMAN_AGENT_TOOLS` 行补 `send_message`。
- `S_07` 运行约束 #2 与「与其它 spec 的关系 / tools 子系统」段：同步刷新工具集，加注 prompt 强约束的理由。
- `tools/CLAUDE.md` 的 Tool Catalogue 表：`send_message` 行追加 `human_agent` 维度的说明；同时补一条 `member_complete_task` 行（之前漏列）。

### 4. 测试

- `test_human_agent_role_tool_set`：断言 `send_message in HUMAN_AGENT_TOOLS`、`claim_task` / `update_task` / `spawn_member` 不在。
- 新增 `test_hitt_section_human_agent_send_message_is_user_driven_cn` / `_en`：断言 prompt section 同时出现「`有 send_message`」(中) / `do have send_message`（英），并出现「不允许」/`Never`、「转发通道 / 转告」/`user-driven / relay channel` 等约束关键词；旧的「没有 `send_message`」/`no send_message` 字样必须从 body 消失。

## 拒绝的方案

### A. 在 `SendMessageTool.invoke` 里加 caller-role 校验

参考 [[feedback_no_role_aware_tool_hacks]] 的反馈——为同一工具按调用方角色分支会让 `invoke` 越积越多 if/else，并且把「该不该转发」这种语义判断硬塞进静态校验。本来的设计哲学就是「一个工具一个 schema 一条语义；按角色不同就拆工具或者改 prompt」。所以拒绝。

工具实现保持单一职责，约束放在 prompt——LLM 在 prompt 引导下做语义判断比硬编码白名单灵活得多。如果未来观察到 LLM 越权滥用（例如自发广播）再追加静态护栏（如 `human_agent` 视角下拒收 `to="*"`），而不是先在工具里下手。

### B. 拆出独立的 `relay_message` 工具

考虑过给 avatar 一个名字不同、schema 也不同的「relay 专用」工具，强迫 LLM 在每次调用时显式声明 `relayed_from_user=<member>`。

代价：

- 多一份 ToolCard + i18n + 文档。
- 工具数量膨胀，本质只是 `send_message` 的子集。
- `send_message` 与 `relay_message` 之间的边界在 prompt 里仍然需要解释，并没省下 prompt 长度。
- 与 leader / teammate 的 `send_message` 收件视角割裂——他们不知道是 avatar 还是真人发出来的。

按 Linus 的「消除特殊情况」哲学，把 schema 完全相同的两个工具拆分只是制造特殊情况。所以**统一在 `send_message`**，让 prompt 引导 LLM 在 content 里自报「代用户转告」前缀，而不是在 schema 里反映这个角色差异。

### C. 限制 `to` 必须是单个成员（禁止广播 / 多播）

考虑过仅允许点对点，理由是「广播是 leader 的协调动作，avatar 不该越权」。

但用户最终选择「完整能力」，且场景上合理：用户完全可能说「告诉所有人我午休 1 小时」。这种全员通知用 broadcast 是最经济的；强行只让 avatar 点对点会逼着 avatar 串行调 N 次 `send_message`，污染消息总线并放大 token 消耗。

约束写在 prompt 一样可执行——「`to` 必须是用户点名的那个成员；用户没让你广播就不要广播」——足够细粒度。

## 验证

- `pytest tests/unit_tests/agent_teams/test_hitt.py -m level0`：含新增的两条 prompt section 用例 + 修订过的 tool-set 用例。
- `make check`：staged 文件 lint 应通过。

## 已知遗留

1. **未加静态护栏**：avatar 角色下没有禁止 `to="*"` / 多播。如果未来观察到模型在没有用户指令时自发广播，可以追加 caller-role-aware 的 broadcast 拒收（仍然只在 `SendMessageTool` 的 broadcast / multicast 分支顶端校验，不污染点对点路径）。
2. **prompt 长度**：human_agent section 又涨了一段，cn / en 都约 +400 chars。下一次 prompt 优化可考虑把「使用规则」四条压缩成两条，但目前可读性优先。
3. **未补 examples**：`examples/agent_teams/` 下没有专门展示 avatar 转发的样例。新加用例时再补一份「用户用自然语言让 avatar 转告 leader」的小 demo。
