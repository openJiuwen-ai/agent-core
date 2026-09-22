# F_114 团队工具的命名空间声明：裸名归谁

日期：2026-09-22

## 背景

会话 `web_1a0c33bad95_ead7d9a7b3bf` 中，跑 haiku 的 Claude Code 成员把团队的 `send_message`
认成了 CLI 自带的 `SendMessage`（轨迹原始记录）：

```
seq=82  ToolSearch  {"query": "select:SendMessage,mcp__openjiuwen-team__claim_task"}
seq=114 SendMessage {"to":"*", "summary":…, "message":…, "recipient":"*", "content":…}
        → <tool_use_error>broadcast (to: "*") is no longer supported
```

同一次工具搜索里，`claim_task` 写了全限定名而 `send_message` 写成了内置名；调用参数是两套
schema 的混合。后续 seq=131/132/133/154 继续误调，seq=138 误调 `ListAgents`。

两个原因叠加：

1. 团队提示词（`prompts/cn|en/*.md`、`i18n.py` 的 note 文案）里工具一律是**裸名**，而成员实际看到
   的名字带命名空间；
2. CLI 自带工具与团队协作工具语义重叠。Claude Code 2.1.278 有 `Agent` / `ListAgents` /
   `SendMessage` / `TaskStop`（`ListAgents` 的描述里就写着 "the teammates on your team"）；
   Codex 0.154.0 有 `collaboration` 命名空间（`spawn_agent` / `send_message` / `wait_agent` /
   `list_agents` / `followup_task`，CLI 自带提示词写作 `to=functions.collaboration.spawn_agent`），
   由 `features.multi_agent_v2` 控制。

## 决策

### 不移除任何内置工具

三方 harness 的 subagent 由它自己管理，team 层的 teammate 是更上层的设计，两套机制并存、互不侵占。
实测 Claude Code 的 `--disallowedTools` 能把工具从工具目录、ToolSearch 索引乃至 CLI 自带提示词里
一并摘掉，Codex 也能用 `features.multi_agent_v2=false` 关掉整组——**都不采用**：那是拿走成员的正常
能力去换提示词的清晰度。

### 不逐条替换模板里的工具名

两家 CLI 的全限定形式不同（见下表），替换会把厂商细节焊进团队模板，而且是全量改动。

### 采用：划定区域 + 两处互不引用的声明

**team 说作用域**：`build_team_member_system_prompt(mcp_server_name=...)` 把装配好的提示词包进

```xml
<team-policy tools="openjiuwen-team">
<team-note kind="tool-namespace">
本策略区域，以及你收到的每一个 team-inbound / team-event / team-context / team-note 消息块，
凡是以裸名提到的工具，指的都是 MCP server `openjiuwen-team` 提供的那一个。
该 server 是本团队唯一的协作通道。CLI 自带的同名或近名工具不属于这个通道。
</team-note>

…（原有各 ## 小节，原样）…
</team-policy>
```

**provider 说命名**：各 provider 在宿主提示词之前拼上

```xml
<mcp-tools>
<server name="openjiuwen-team" tool-name="mcp__openjiuwen-team__{tool}"/>
</mcp-tools>
```

两句话唯一的连接键是 **server 名**。team 不知道全限定形式，provider 不知道团队语义；某个 provider
没实现第二句，第一句依然成立，不会悬空。

## 为什么是 XML 元素而不是 markdown 标题

- **边界**：成员提示词是 append 到 CLI preset 之后的，preset 自己也在用 `##`，再套一级标题没有
  结束标记；XML 有闭合标签。
- **命名空间是数据**：markdown 标题挂不了属性。
- **与既有约定一致**：运行时通道本就是 `<team-inbound type=…>` / `<team-event kind=…>` /
  `<team-context>` / `<team-note kind=…>` 这套词汇，属性早就在承载契约令牌。

由此得到一个额外收益：作用域写成"本区域 + 所有 `team-*` 消息块"，`i18n.py` 里嵌在
`<team-note kind="reply-hint">` 的"请务必通过 `send_message` 回复"这类文案**一条都不用改**，天然
落在声明的覆盖范围内。

## 两家 CLI 的命名形式（取自厂商自己的记录）

| provider | 模型寻址用的名字 | 证据 |
|---|---|---|
| claudecode | `mcp__openjiuwen-team__send_message` | 轨迹 seq=82，模型自己写出的 `select:mcp__openjiuwen-team__claim_task` |
| codex | `mcp__openjiuwen_team.send_message` | `~/.codex/sessions` rollout 里 19 条 `function_call`，`namespace="mcp__openjiuwen_team"` + `name="send_message"` |

server 名在 Codex 侧会被 `codex_server_key`（`-` → `_`）改写，因为它要做 TOML bare key；
这个改写同时决定了模型看到的命名空间。Codex 的 `tool-name` 由 `namespaced_tool_name` 生成，与
`codex/observation.py` 读回一次调用用的是同一个函数，声明和观测不会各自漂。

## 落点

| 文件 | 改动 |
|---|---|
| `agent_teams/inbound_render.py` | `render_team_policy`；`<team-policy>` 进词汇表。正文不转义（是提示词不是数据，转义会破坏 markdown） |
| `agent_teams/prompts/{cn,en}/tool_namespace.md` | 声明文案（A 类模板，可随工作区演化） |
| `agent_teams/prompts/sections.py` | `build_team_member_system_prompt(mcp_server_name=...)` |
| `agent_teams/external/cli_agent/__init__.py` | `TEAM_MCP_SERVER_NAME`，取代两处硬编码默认值 |
| `agent_teams/spawn/external_cli_spawn.py` | 传入 server 名 |
| `harness_providers/mcp_naming.py` | `mcp_tool_naming_preamble`，声明的措辞只有一处 |
| `harness_providers/claudecode/options.py` | `claude_mcp_tool_naming`，在 `build_claude_options` 里领衔提示词（append / replace 两种模式都带） |
| `harness_providers/codex/options.py` | `codex_server_key` / `namespaced_tool_name` / `codex_mcp_tool_naming`；`observation.py` 改用共享实现 |
| `harness_providers/codex/harness.py` | `_connect` 先拼声明，再分别喂 `build_thread_options` 与 `append_developer_instructions` |

进程内成员不受影响：它的工具就是裸名调用，`mcp_server_name=None` 时提示词逐字不变。

## 验证

- `tests/unit_tests/agent_teams/prompts/test_member_system_prompt.py`：包裹形态、声明覆盖四类消息块、
  不给 server 名时不包裹，且提示词里不出现任何 `mcp__`（全限定形式不归 team 层说）。
- `tests/unit_tests/harness_providers/test_claudecode.py`：声明领衔、无 MCP server 时提示词逐字不变。
- `tests/unit_tests/harness_providers/test_codex.py`：声明形态；并断言"声明出去的名字"与
  `_called_tool_name` 读回的名字一致。
