# External Access Splits Into operator / member Scopes; member Reuses Real TeamTools

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-27 |
| 范围 | `openjiuwen/agent_teams/external/descriptor.py`、`external/client.py`、`external/cli_agent/spawn.py`、`mcp/server.py`、`skill/cli.py`、`skill/SKILL_member.md`(+`SKILL_operator.md`，删 `SKILL.md`)、`spawn/external_cli_spawn.py`、`mcp/__init__.py`、`skill/__init__.py`、`tests/unit_tests/agent_teams/external/`(conftest + test_mcp_server + test_cli) |
| 测试基线 | `pytest tests/unit_tests/agent_teams/`：1250 passed, 16 skipped；`external/` 82 passed |
| Refs | `#751` |

## 背景

外部接入（CLI + MCP）此前把两类完全不同的使用场景混成一套手扣工具（`mcp/server.py`
FastMCP + 按 team role 粗分；`skill/cli.py` 同样手扣），与**进程内原生成员**实际用的
`TeamTool` + `map_result()` 文本后处理**行为/结果不一致**——工具名不同、参数形态不同、丢掉了
`map_result` 的行为引导文本。这违背"外部成员与原生成员不可区分"的目标（见 [[F_21]] /
[[F_22]] / [[F_25]] 的接入面演进）。

把外部接入显式拆成两个一等场景，**首次连接时按 `descriptor.scope` 分化**：

- **member（cli-agent 三方团队成员）**：被 `external_cli_spawn` 拉起的第三方 CLI，作为团队
  一等成员。工具集 = **原生 teammate 完全对齐**——真实 `view_task` / `claim_task`（status
  claimed/**completed**）/ `send_message`，外加外部专有的 `read_inbox`（原生靠 coordination
  push 收消息，外部只能 pull，无原生对应）。MCP server-level **instructions 置空**：团队系统
  提示词已在 spawn 时直接注入 CLI（`build_team_member_system_prompt` →
  `--append-system-prompt` / `-c developer_instructions` / prepend，见 [[F_25]]），协议不在
  MCP 重复。
- **operator（团队外的非成员控制接口）**：团队**之外**通过工具操作/控制团队（不是 team role）。
  保留原有广义工具集（send/list/get/claimable/claim/complete/update/list_members/read_inbox）
  并扩到全团队控制（补 `create_task`）。MCP instructions = 控制工作流。

## 数据结构 / 判别

- `TeamJoinDescriptor.scope: Literal["operator", "member"]`（默认 `"operator"`，与 `role`
  正交）。`external_cli_spawn.descriptor_from_context` 显式设 `scope="member"`；手动下发的
  operator descriptor 用默认。

## 决策

- **member 复用真实 TeamTool**：`ExternalTeamClient.connect` 总是建最小 `TeamBackend`；
  `scope=="member"` 时 `create_team_tools(role="teammate", agent_team=backend, lang=...)`
  得 `{view_task, claim_task, send_message}`（已被 `_wrap_invoke_with_logging` 包装，
  `str(await tool.invoke(inputs))` == `map_result()` 文本），存 `client.tools`。MCP/CLI 直接
  驱动同一批 `TeamTool`，结果文本与进程内成员逐字一致。`complete_task` 折进
  `claim_task(status="completed")`；list/get/claimable 折进 `view_task(action=…)`。
- **operator 保留 + 扩展**：保留 `ExternalTeamClient` 的 per-op 方法（send/list/claim/...），
  新增 `create_task`（`task_manager.add`）。operator 工具在 MCP/CLI 仍以显式 schema/子命令暴露。
- **MCP 改用低层 `mcp.server.lowlevel.Server`**：FastMCP 从函数签名推断 schema，无法暴露
  真实工具的 `card.input_params`。低层 Server 的 `list_tools` 返回
  `types.Tool(inputSchema=<raw dict>)`、`call_tool` 返回 `list[TextContent]`，按
  `client.scope` 在请求期分化工具集；`build_server(scope=...)` 只决定 server-level
  instructions（member 空 / operator 工作流）。每个工具调用前 `client.bind_session_context()`
  重绑 session/language contextvar（每调用独立 task，否则按空 session 算错动态表名）。
- **`read_inbox`** 是唯一外部专有工具，渲染走 `format.py`（`render_messages` +
  `render_task_board`，镜像 dispatcher push 文本），member/operator 共用。
- **CLI（`skill/cli.py`）按 scope 分化子命令**：两段解析（先读 descriptor 拿 scope，再建
  scope 专属 parser）。member 子命令 = 真实工具（view_task / claim_task / send_message）+
  inbox；operator 保留原子命令 + create_task。SKILL.md 拆为 `SKILL_member.md`（成员协同协议）
  + `SKILL_operator.md`（团队外控制者操作手册）。

## 拒绝 / 推迟的方案

- **operator 也迁到真实 TeamTool**：推迟（"先保留"）。operator 是非成员控制者，与 member 的
  原生对齐目标不同；本轮只补 `create_task`，保留其余 per-op 实现。
- **operator 成员生命周期工具（spawn/shutdown/build/clean）**：未纳入。跨进程只能写 DB 行 /
  发信号，成员**进程的实际拉起**需团队本地 leader（`startup` 回调）——operator 给不了，做成
  半可用工具会误导。归本地 leader。
- **FastMCP 双框架（operator 留 FastMCP、member 用低层）**：拒绝。统一在低层 Server 上按
  scope 分化，单一框架。

## 验证

- 单测：`external/test_mcp_server.py`（member 工具集 == {read_inbox, view_task, claim_task,
  send_message} 且 instructions 空；operator 工具集含 create_task 且 instructions 非空；
  member `claim_task(status=claimed→completed)` 折叠；read_inbox 文本；operator create_task
  落库）、`external/test_cli.py`（member/operator 两组子命令）、`conftest` 加 `scope`。
  `external/test_client.py` / `test_format.py` 不变（client per-op 方法 + format 保留）→ 全绿。
- `pytest tests/unit_tests/agent_teams/`：1250 passed, 16 skipped。
- 端到端（用户自验）：member 场景 `agent_team_external_cli_e2e.py` 跑 4 成员 claim→
  `claim_task(completed)`→report；MCP 暴露的工具名/描述/结果文本与原生 teammate 逐字一致，
  instructions 空、系统提示词由 spawn 注入。

## 已知遗留

- operator 全控制的成员生命周期（spawn/shutdown/build/clean）跨进程不可行，归本地 leader；
  是否最终把 operator 也迁到真实 TeamTool 可后续评估。
- 低层 `call_tool` 返回 `list[TextContent]`（返 dict 会触发 structured 分支被 JSON 化）；
  `validate_input=True` 按真实 schema 校验（member `claim_task.status` / `send_message.to/content`
  必填——刻意对齐，spawn join prompt 已同步）。
