# External CLI Member Hardening, Team-Rail System Prompt, Gemini, and Live Output Surfacing

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-27 |
| 范围 | `external/cli_agent/adapters.py`、`external/cli_agent/spawn.py`、`external/runtime.py`、`mcp/server.py`、`prompts/sections.py`、`prompts/__init__.py`、`rails/team_policy_rail.py`、`spawn/external_cli_spawn.py`、`tests/unit_tests/agent_teams/external/`、`tests/unit_tests/agent_teams/prompts/` |
| 测试基线 | 本特性涉及的测试：`pytest external/test_cli_agent.py external/test_external_cli_spawn.py external/test_cli_adapter_injection.py external/test_mcp_server.py prompts/test_member_system_prompt.py test_team_policy_rail.py` → 86 passed |
| Refs | `#751` |

## 背景

[[F_21_external-agent-access]] 与 [[F_22_external-cli-spawn-member-and-mcp-injection]] 落地了
外部 CLI 成员的接入面、`spawn_member` 角色、静态 spec 配置与 claude/codex 的 MCP 自动注入。
随后的审查暴露出一批可靠性缺口与能力空白,本特性集中收口:

1. **静默失败**:流式 CLI 崩溃(auth/quota/crash)时 stdout EOF 被当作"成功的空轮";
   openclaw/hermes 的团队 MCP 不自动注入却不报错,成员静默拿不到协同工具。
2. **粗暴超时**:一次性 CLI 用固定墙钟上限,会杀掉正在干长活的进程。
3. **身份缺失**:外部 CLI 成员只拿到 persona 文本(且只进 `AgentCard.description`,从不下发),
   无法获得与进程内成员一致的团队角色设定。
4. **工具越权**:所有成员拿到同一套全量 MCP 工具,reviewer 型角色也能 `claim_task`。
5. **可观测性**:外部 CLI 的 stdout 完全不可见,多 agent 流程难调试。
6. **覆盖面**:缺 gemini;codex 未用结构化 `--json`。

## 设计与数据结构

### Adapter(`external/cli_agent/adapters.py`)
- 新增 `gemini` adapter:`gemini -o stream-json -y -p <prompt>`,一次性 re-invoke;
  跨轮用 `session_flag="--session-id"`(首轮)+ `resume_flag="--resume"`(后续轮)续接同一
  client-chosen UUID(对齐 clowder 的 `--resume <id>` 实践)。
- codex 切到 `codex exec --json`:`completion=COMPLETION_CODEX_JSON`(识别 `{"type":"turn.completed"}`)。
- 新增 `resume_flag` 字段 + `build_turn_command` 的"首轮 session_flag / 后续 resume_flag"逻辑
  (openclaw 只有 session_flag → 每轮 `--session-id`;hermes 只有 continue_args → 行为不变)。
- 系统提示词注入策略 `system_prompt_inject` + `system_prompt_args()`:claude
  `--append-system-prompt`(`SYSTEM_PROMPT_CLAUDE_APPEND`)、codex
  `-c developer_instructions=<json>`(`SYSTEM_PROMPT_CODEX_DEVELOPER`,经 `codex exec
  --strict-config` 验证可用;`base_instructions` 被拒)。无 flag 的 CLI 由 spawn 路径把
  提示词 prepend 到首条消息。
- MCP 带外注册 `mcp_register_command()` + 策略 `MCP_INJECT_GEMINI_ADD` / `MCP_INJECT_HERMES_ADD`:
  `<cli> mcp add ...`,供无 launch-flag 的 CLI 在 spawn 时注册一次。
- 结构化输出摘要 `structured_output` 字段 + `summarize_output_line()`:claude/codex/gemini 的
  JSON 事件提取 assistant text / tool 描述并跳过 lifecycle 事件;纯文本 CLI 整行即叙述。

### Runtime(`external/runtime.py`)
- `run_streaming` 现产出 chunk:`_drive` 改为 async generator,每条 narration 摘要包成
  `OutputSchema(type="llm_output", payload={"content", "result_type"})` yield 出去,由
  `StreamController._tag_chunk` 升级为带 `source_member`/`role` 的 `TeamOutputSchema` 并
  fan-out 到 leader 流——与进程内成员同路(见 [[F_02_member-attributed-streaming]])。
- 流式 runtime(claude):行循环内**实时** yield;premature-EOF(进程崩溃)不再静默,按
  `returncode` 抛带 stderr tail 的结构化错误。
- 一次性 runtime(codex/gemini/openclaw/hermes):用 **asyncio.Queue 桥接**把并发
  `_drain_stdout` 的摘要**实时**送出;`_run_once` 的 `finally` 投放 `None` 哨兵保证
  `_drive` 的消费循环在任何退出路径(完成/超时/abort/spawn 失败)都能收尾,watchdog 是兜底。
- 超时:固定墙钟换成**无输出(inactivity)超时**;`turn_timeout_s` 降级为可选绝对上限。
- `abort()`:立即终止在跑子进程,`_drive` 不再启动下一轮。

### Prompts(`prompts/sections.py` / `rails/team_policy_rail.py` / `spawn/external_cli_spawn.py`)
- 抽出 `build_team_static_sections(...)` 作为静态 section 集的**单一真相源**:
  `TeamPolicyRail._build_static_sections` 委托给它(净减重复);新增
  `build_team_member_system_prompt(...)` 用一个**只装 team section** 的 `SystemPromptBuilder`
  渲染成独立字符串。
- `external_cli_spawn` 据 ctx/spec(role/persona/lifecycle/teammate_mode/`_resolve_team_mode`/
  language + backend 的 human/bridge 名单)构建该成员的系统提示词,经 adapter 通道下发
  (claude `--append-system-prompt`;其余 prepend 到首条消息)。

### MCP server(`mcp/server.py`)
- `_ROLE_TOOLS` 按角色裁剪工具:leader/teammate 全量;未知/reviewer 型只读+消息(无
  claim/complete/update),向后兼容现有角色。
- `_bind_session_context(client)` 集中处理 FastMCP per-task contextvar 重绑,移除散落的隐式重置。

## 决策

- **系统提示词 = team-rail sections,不只是 persona**:外部 CLI 成员应与进程内成员拿到
  同一套 role/workflow/lifecycle/persona section,因此复用 `build_team_static_sections`;
  但**排除**其它 DeepAgent rail(safety/workspace/memory)——它们对非 DeepAgent 的 CLI 不适用。
  dynamic 的 info/members section 不进静态提示词(成员经 MCP `list_members`/`read_inbox` 自取)。
- **codex 用 `developer_instructions` 而非 prepend / `base_instructions`**:实测
  `base_instructions` 不可经 `-c` 覆盖且会冲掉 codex 自带脚手架;`developer_instructions`
  是叠加的 developer-message 层,经 strict-config 验证可用。
- **一次性 CLI 也做实时 surface(queue 桥接)而非批量**:一次性进程的 stdout 排空与 stderr
  排空、inactivity watchdog 并发跑,产 chunk 的排空任务与生成器 yield 点解耦,故用 queue
  搭桥;流式 claude 的输出消费本就是扁平单循环,直接 yield 即实时,无需 queue。
- **MCP 静默缺口改为"自动注册 + 大声告警"**:gemini/hermes 经 `mcp add` 子命令注册;openclaw
  无已知注册方式 → spawn 时 `team_logger.warning`,不再静默放行。

## 拒绝的方案

- **系统提示词只下发 persona**:初版只把 `ctx.persona` 注入。拒绝——外部 CLI 成员需与进程内
  成员行为一致,角色/工作流/生命周期都来自 team-rail section,只给 persona 会让异构团队的
  外部成员缺失团队协作守则。改为复用 `build_team_static_sections`。
- **codex 用 `base_instructions` 或 prepend 到 prompt**:`base_instructions` 实测不可经 `-c`
  覆盖且会冲掉 codex 自带脚手架;prepend 丢失 system/user 分层。改用 `-c developer_instructions`。
- **reinvoke 批量(turn 结束后再 yield)**:初版把摘要收集到 `_round_chunks`、turn 后成块 yield,
  以回避并发。拒绝——长 turn 下看不到实时进度;改为 asyncio.Queue 桥接,在不动 watchdog/abort
  并发的前提下实时 surface。
- **runtime 直接构造 `TeamOutputSchema`**:拒绝——会让 runtime 依赖 team schema/blueprint 且
  绕过统一的 member/role 打标。改为 yield 朴素 `OutputSchema`,由 `StreamController._tag_chunk`
  升级打标(成员归属逻辑集中一处)。
- **为外部 CLI runtime 新建独立 `S_` spec**:评估后沿用既有惯例(经 features [[F_21_external-agent-access]] /
  [[F_22_external-cli-spawn-member-and-mcp-injection]] / 本文 + CLAUDE.md 描述),未新增 spec,避免
  超出本次"补文档"范围;prompt 装配的规约变动落到 [[S_09_prompts-and-rails]]。

## 验证基线

- 本特性测试 86 passed(见元信息);新增实时性用例
  `test_reinvoke_surfaces_chunks_live_during_turn`(dribble 假 CLI,首 chunk <0.3s 到达,
  连跑 3 次无抖动)。
- codex `-c developer_instructions` 在本机 codex 0.133 经 `--strict-config` 实测通过;
  gemini 0.43 的 adapter argv / mcp-add 命令构造已单测。

## 已知遗留

- **codex/gemini 的 `summarize_output_line` 字段提取为 best-effort**:claude 事件 schema 高置信,
  codex/gemini 走防御式通用兜底,未对真实 CLI 输出最终校验(`summarize_output_line` docstring 标 VERIFY)。
- **gemini 跨轮 `--resume` 接受 client-chosen id 未经真实 API 验证**:若某版本 `--resume`
  只认 latest/index,需改为从 gemini 输出捕获其自生成 session id(需在 reinvoke runtime 里解析输出)。
- **openclaw MCP 仍无自动注册**:缺已知注册命令,spawn 时告警,需用户带外配置。
- **reinvoke 实时性以进程为单位**:一次性进程内逐行实时 surface,但跨轮仍是"每轮新进程"。
