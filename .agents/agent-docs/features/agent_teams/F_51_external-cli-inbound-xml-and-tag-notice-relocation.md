# 外部 CLI 入站渲染统一为 XML + tag 说明就近归属 build_team_static_sections

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-07-06 |
| 范围 | `external/format.py`（消息/任务板改走 `inbound_render` 的 `<team-inbound>`/`<team-event>` XML）；`external/client.py`（`is_human_agent` + `read_inbox` 传入）；`prompts/sections.py`（`include_tag_notices`→`include_attachment_notice`：inbound_tags 无条件、attachment_notice 门控，两段说明就近在 `build_team_static_sections` 内构造）；`rails/team_policy_rail.py`（传 `include_attachment_notice=True`、删 rail 侧 append 与孤儿 import）；测试 `external/test_format.py` + `test_team_policy_rail.py` |
| 测试基线 | `external/test_format.py` + `test_client.py` + `test_team_policy_rail.py` 全绿；`tests/unit_tests/agent_teams/` 1715 passed / 16 skipped |
| Refs | #751 |

## 背景

两个遗留在本次一并收口：

**痛点 1 — 外部 CLI 的入站消息没走 XML。** [[F_46]] 把进程内成员的入站消息/事件统一渲染成
`<team-inbound>`/`<team-event>` XML（`inbound_render`），但**外部 CLI 成员**（cli-agent 三方团队
成员，经 `ExternalTeamClient.read_inbox`）仍走 `external/format.py` 的旧 i18n 糊串
（`dispatcher.msg_received` 等）——同一个团队里，进程内成员和外部 CLI 成员看到的消息形态不一致。
`read_inbox` 的 docstring 早写着"mirror the in-process dispatcher……same shape",但实现名不副实。

**痛点 2 — [[F_50]] 的 tag 说明就近归属没到位。** F_50 期间把两个说明 section（attachment_notice /
inbound_tags）从 rail 的额外 `append` 收进 `build_team_static_sections`（`include_tag_notices` 门控），
但把外部 CLI 一并排除在两段说明之外。既然痛点 1 要让外部 CLI 也渲染 `<team-inbound>` XML,它就
**应该**拿到 inbound_tags 说明。

## 决策

### D1：`external/format.py` 统一为 XML（mirror 进程内）
- `render_message` 改走 `inbound_render.render_inbound(...)`，note 逻辑与进程内 `_format_message`
  逐一对齐：teammate → `reply-hint`（`dispatcher.reply_hint`）；human_agent → `for="controller"`
  + `hitt-silence`（`hitt.silence_note`）。
- `render_task_board` 改走 `render_event(kind="task-board", body=…)`；`render_task_line`（进程内
  `TaskBoardHandler` 也复用的行格式）保持不变。
- `external/client.py` 加 `is_human_agent`（role=="human_agent"），`read_inbox` 传给 `render_messages`。

### D2：tag 说明按机制归属，不再一刀切
- inbound_tags：**无条件**由 `build_team_static_sections` 构造——进程内 + 外部 CLI 都渲染
  `<team-inbound>`/`<team-event>` XML,都需要这份说明。
- attachment_notice：由 `include_attachment_notice` 门控——只有进程内 DeepAgent 有
  `PromptAttachmentManager`、会看到 `<prompt-attachment>`；外部 CLI 靠 MCP 工具自取状态、架构上
  拿不到 attachment，给了会误导它去找不存在的东西。rail 传 `include_attachment_notice=True`,
  `build_team_member_system_prompt`（外部 CLI）走默认 False。
- rail 的 `_build_static_sections` 不再自己 `append` 两段说明、删掉两个孤儿 import。

## 拒绝的方案
- **外部 CLI 保留旧 i18n 糊串**：与进程内形态永久分叉，`read_inbox` 的"same shape"承诺永远是空话。
- **两段说明都给外部 CLI（完全一致）**：attachment_notice 对外部 CLI 不成立（它无 attachment
  机制），给了会误导 LLM 去找消息尾部并不存在的 `<prompt-attachment>`。按"只给 inbound_tags"落地。
- **`include_tag_notices` 捆绑两段**（F_50 期间的中间态）：两段说明对应两套不同机制，外部 CLI 只
  命中其中一套，捆绑无法表达；拆成"inbound_tags 无条件 + attachment_notice 门控"才准确。

## 验证
- `external/test_format.py`：`render_message` 产出 `<team-inbound>` + reply-hint；human_agent 产出
  `for="controller"` + hitt-silence；broadcast → `type="broadcast"`；`render_task_board` 包进
  `<team-event kind="task-board">`。
- `test_team_policy_rail.py::TestTagNoticeInclusion`：`build_team_static_sections` 默认含 inbound_tags
  不含 attachment_notice、`include_attachment_notice=True` 时两者皆含；`build_team_member_system_prompt`
  含 inbound_tags 不含 attachment_notice；rail `_static_sections` 两者皆含。
- 全套件 `tests/unit_tests/agent_teams/` 1715 passed / 16 skipped。

## 已知遗留
- 外部 CLI 成员目前主要是 teammate；`is_human_agent` 分支已就位，但 human_agent 作为外部 CLI 的
  场景尚无 e2e 覆盖。
- external e2e（`tests/system_tests/agent_swarm/agent_team_external_cli_e2e.py`）未纳入本次自动化
  校验（需真实 CLI + 凭证）。
