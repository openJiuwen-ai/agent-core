# External Agent Access: skill+cli / mcp Wrappers

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-20 |
| 范围 | `openjiuwen/agent_teams/external/`、`openjiuwen/agent_teams/skill/`、`openjiuwen/agent_teams/mcp/`、`openjiuwen/agent_teams/__init__.py`、`openjiuwen/agent_teams/CLAUDE.md`、`pyproject.toml`、`tests/unit_tests/agent_teams/external/` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/external/`：25 passed |
| Refs | `#751` |

## 背景

`agent_teams` 的协同能力（发消息、任务板、成员管理）此前只服务**进程内**成员：
每个成员本地跑一个 DeepAgent，由 coordination kernel 驱动。需要让**外部 agent**
（第三方 CLI claudecode / codex / openclaw / hermes，或独立运行的 agent 服务进程）
也能作为一等成员加入团队、直接调用协同工具。

底座本就具备跨进程能力：SQLite WAL + `TeamTaskManager` / `TeamMessageManager`（DAO），
zmq messager（ROUTER/DEALER + XPUB/XSUB，地址走 `MessagerTransportConfig`），事件
topic 统一为 `TeamTopic.{TEAM,TASK,MESSAGE}.build(session_id, team_name)`。

核心洞察：**在 DB + zmq 这一层，外部成员与进程内成员完全对称**——外部成员调
`claim_task` publish 的是同样的 `EventMessage` 到同样的 topic，进程内成员的
`TaskBoardHandler` / `MessageHandler` 照常被 nudge，分不出对面是谁。唯一的不对称只在
"最后一公里"：框架往本地 DeepAgent 里塞 vs 外部 agent 自己读。

## 与 F_07 bridge 的关系

**正交互补，不替代**。F_07 bridge = 本地完整 DeepAgent avatar + relay 纯文本给
**无工具**的远程执行者（本地 LLM 做调度）。本特性 = 外部 agent 自己**直接调用协同
工具**（直连 DB + zmq），是自主的一等成员。两种外部能力档次各管一类；不复用
`BRIDGE_AGENT` 角色，避免语义冲突。

## 设计

三层 + 两种部署形态。

### L0 ops 核心 `external/`

- `descriptor.py`：`TeamJoinDescriptor`（`session_id` / `team_name` / `member_name` /
  `role` / `language` / `db_config` / `transport_config`）+ `TEAM_JOIN_ENV`
  （`OPENJIUWEN_TEAM_JOIN`）。JSON 可序列化，团队拉起外部 agent 时注入单环境变量，
  或运维下发给独立服务。`db_config` 用 `DatabaseConfig | MemoryDatabaseConfig` 联合
  （与 `schema/team.py` 一致），跨进程用文件 sqlite，单进程 / 测试用 memory。
- `client.py`：`ExternalTeamClient` 按 descriptor `get_shared_db` + `create_messager`，
  复用 manager 暴露 `send_message`（`to="*"` 广播）/ `list_tasks` / `claimable_tasks` /
  `get_task` / `claim_task` / `complete_task` / `update_task` / `list_members` +
  `fetch_inbox`（poll，读未读 + 标记已读 + 任务板）/ `watch`（订阅 MESSAGE/TASK，
  事件即唤醒后回查 DB——规避自发事件过滤）。`connect()` 设 `set_language` +
  `set_session_id`，保证 publish 落到正确 topic。
- `format.py`：纯函数 `render_message` / `render_messages` / `render_task_board`，复用
  `i18n.t` 文案，使外部成员看到与进程内 dispatcher 一致的文本。

### L1 前端

- `skill/cli.py`（`team-member` 入口）+ `skill/SKILL.md`：argparse 非交互脚本式 CLI
  （inbox/send/broadcast/task/claim/complete/update/members）+ 协同协议教学文档。
  与 `cli/` 交互式 TUI 区别明确：后者给人，本 CLI 给外部 agent 脚本化调用，可用 `print`。
- `mcp/server.py`（`openjiuwen-team-mcp` 入口）：FastMCP **stdio** server，ops 暴露为
  MCP 工具，协同协议放进 server-level `instructions`（工具 schema 自描述，无需单独
  skill）。client 在 server 事件循环内**懒连接**（首个工具调用时，从 env 读 descriptor），
  使 messager/DB 资源绑定到使用它的 loop。仓库首个 MCP server。

### 部署形态

- **形态 A 独立服务自驱**：无本地 presence，外部进程用 `fetch_inbox`/`watch` 自读
  （follow_up 语义，无 mid-turn steer），用工具行动，生命周期在外部。
- **形态 B team 拉起 CLI**（P2，本次未实现）：team 复用 coordination kernel，
  `deliver_input` 的"最后一公里"换成写外部 CLI 的 stdin（侧信道注入，支持 mid-turn
  steer）；规划见下"已知遗留"。

## 拒绝 / 推迟的方案

- **把协同 ops 在 CLI/MCP 里重写一遍**：拒绝。复用 `TeamTaskManager` /
  `TeamMessageManager`，保证写路径与进程内成员对称、事件语义一致。
- **inbound 用 sender_id 自过滤**：拒绝。`watch` 改为"事件即唤醒、再回查 DB"——
  回查只返回发给本成员的未读消息 + 当前任务板，天然排除自发事件，更简单更稳。
- **把 inbound 格式化从 coordination handler 抽成共享纯函数供两侧复用**：本次只实现
  `external/format.py`（外部侧），未重构 `message.py` / `task_board.py`，避免在 P1
  动到工作中的协调层。后续可做去重。

## P2 进度（形态 B：自动拉起 + 侧信道注入）

team 把第三方 CLI 拉成子进程、注入连接 descriptor + MCP 配置，并通过 **stdin 管道**做
mid-turn steer（用户选定 stdin 传输，Unix 优先、接口预留 PTY/Windows）。

**已实现并单测的building blocks（additive，不触碰运行核心）：**

- `agent/member_runtime.py`：`MemberRuntime` Protocol——把 coordination/StreamController/
  configurator 实际访问的 harness 实例面（7 个 round 方法 + find_rails/register_rail/
  unregister_rail/register_member_tools/inject_member_memory/run_agent_customizer +
  workspace/sys_operation 两个 property，共 15 个成员）抽成结构化契约。`TeamHarness`
  天然满足。
- `external/cli_agent/injector.py`：`Injector` Protocol + `StdinPipeInjector`（向子进程
  stdin 写 newline-framed 文本；仅对持续读 stdin 的 CLI 生效）。
- `external/cli_agent/adapters.py`：`CliAgentAdapter`（启动 argv + 输入framing + 轮次完成
  策略，数据驱动）+ claude/codex/openclaw/hermes/generic 注册表。per-CLI 适配：
  - **claude**（高置信）：`--print --input-format stream-json --output-format stream-json
    --verbose --dangerously-skip-permissions`；输入 NDJSON user message，轮次完成 =
    `{"type":"result"}` 事件。
  - **codex**（中置信）：`codex proto`（JSONL 协议流）+ `-c approval_policy/-c sandbox_mode`
    非交互；输入 submission `{"op":{"type":"user_input",...}}`，完成 = event
    `msg.type=="task_complete"`。
  - **openclaw**（低置信）：ClawTeam 走一次性 `--message`（prompt 走 argv 非 stdin），
    `supports_stdin_injection=False`，由 ReinvokeCliRuntime 每轮 `openclaw --local
    --session-id <id> --message <prompt>` 驱动（session-id 提供跨轮连续性）。原 re-invoke-per-turn
    runtime 或确认交互模式。
  - **hermes**（NousResearch/hermes-agent，已调研官方 CLI）：`hermes -z "<prompt>" --yolo`
    一次性入口——prompt 走 argv（stdin 仅补充上下文），输出纯文本最终答案后退出，无多轮
    stdin 循环、无结构化轮次分隔（轮次完成 = 进程退出/EOF）；`--yolo` 跳过危险命令审批；
    跨轮用 `--continue`/`--resume <id>`；MCP 回调走 `hermes mcp add <name> --command
    openjiuwen-team-mcp`。与 openclaw 同属一次性模型 → `supports_stdin_injection=False`，
    由 ReinvokeCliRuntime 每轮跑一次 `hermes -z --yolo [--continue] <prompt>`、读 stdout 到 EOF。
  claude/codex 走持续 stdin 的 ExternalCliRuntime；openclaw/hermes 走一次性的
  ReinvokeCliRuntime（已实现）。两种 runtime 由 adapter `supports_stdin_injection` 在
  `build_cli_runtime` 处自动分流。
  四者均未对真实二进制验证，adapters.py 注释逐条标注置信度与待验证项。
- `external/runtime.py`：`_CliRuntimeBase`（共享 `MemberRuntime` 表面 + 共用 no-op 钩子）派生
  两种 runtime：`ExternalCliRuntime`（持续 stdin 流式——写 turn 输入并消费 stdout 至 adapter
  判定轮次完成，steer/follow_up 写 stdin，进程长存）与 `ReinvokeCliRuntime`（一次性——每轮
  `create_subprocess_exec` 新进程，prompt 走 argv、读 stdout 到 EOF；steer/follow_up 缓冲、
  本轮内以 follow-up 再调排空，不丢消息；进程退出即轮次完成）。两者 CLI stdout 均留作内部、
  不进 team 流；rail/memory/customizer 钩子为 no-op。

**spawn/configurator 接线（已实现并回归）：**

- `schema/team.py`：`TeamRuntimeContext.cli_agent: Optional[str]`——置位即表示该 teammate
  由外部 CLI 驱动（role 仍是 TEAMMATE，用成员级字段判别而非 role 判别）。
- `agent_configurator.setup_agent(spec, ctx, *, member_runtime=None)`：传入 runtime 时直接
  采用、跳过 DeepAgent/rail/memory/customizer；经 `configure` 从 `TeamAgent.configure` 透传。
- `resources.harness` / `TeamAgent.harness` / configurator `harness` 类型放宽到
  `MemberRuntime | None`（`TeamHarness` 结构上满足）。
- `external/cli_agent/spawn.py:build_cli_runtime`：按 `ctx` 建 descriptor、注入 env，按
  adapter `supports_stdin_injection` 分流——流式则拉起长存子进程包成 `ExternalCliRuntime`，
  一次性则返回 `ReinvokeCliRuntime`（每轮自拉子进程）。runtime 自持子进程、`aclose` 收尾。
- `spawn/external_cli_spawn.py`：镜像 `inprocess_spawn`——先（async）拉起 CLI 建 runtime，
  再 `teammate.configure(spec, ctx, member_runtime=runtime)`，TeamAgent shell + coordination
  在本进程跑，finally 关 injector + terminate 子进程。
- `spawn_manager.spawn_teammate`：`if ctx.cli_agent` 分支走 `external_cli_spawn`。
- 回归：全 `agent_teams` 套件 1143 passed（gated 改动不碰既有路径）；新增集成测试用真实
  echo 子进程驱动一轮 + descriptor-from-ctx。

**operator 触发入口（已实现并回归）：**

- `TeamBackend._external_cli_specs`（member→cli_agent 内存注册表，镜像 bridge 的
  `_bridge_member_specs`，**无需 DB 加列**）+ `spawn_external_cli_agent(...)` /
  `is_external_cli_agent` / `get_external_cli_agent` / `external_cli_agent_names`。
  `spawn_external_cli_agent` 先登记注册表、再 `spawn_member`（role=TEAMMATE,
  UNSTARTED），因 `spawn_member` 只落库、由后续 `startup` 触发拉起，注册表早于拉起就位。
- `SpawnManager.build_context_from_db` 读 `backend.get_external_cli_agent(member)` 置
  `ctx.cli_agent`，于是 `startup → _on_teammate_created` 重建的 ctx 自动路由到
  `external_cli_spawn`。
- 局限同 bridge Phase-1：注册表 per-process，跨进程冷恢复需 predefined 声明（本期未做
  predefined `ExternalCliMemberSpec`——role_type=TEAMMATE 与基类判别冲突，留待后续）。
- 回归：全 `agent_teams` 套件 1149 passed；新增 backend 注册单测。

**剩余：**

- **真实 CLI 端到端验证**：adapter 的 stdin 输入格式与轮次完成检测依赖各 CLI（及版本）
  的真实 stdout 协议，需在能跑 claude/codex 的环境实测调参。本仓库用 fake/echo 子进程
  验证了 runtime + spawn + backend 注册的契约行为，未连真实 CLI。
- **predefined 外部 CLI 成员**：需解决 role_type=TEAMMATE 的判别union冲突（成员级字段
  而非 role 判别），用于跨进程冷恢复。
- **leader 工具**：把 `spawn_external_cli_agent` 暴露为 LLM 可调工具是可选糖（当前为
  SDK/operator 方法）。**已在 F_22 解决**：`spawn_member(role_type='external_cli',
  cli_agent=...)`。
- **MCP 配置注入**：`launch_external_cli` 已注入 `OPENJIUWEN_TEAM_JOIN` env（CLI 衍生的
  MCP server 进程继承之）；写各 CLI 专属的 `.mcp.json` 指向 `openjiuwen-team-mcp` 是
  per-CLI best-effort，未做。**已在 F_22 解决**：claude `--mcp-config` / codex
  `-c mcp_servers...` 由 spawn 路径按 adapter 自动注入（流式 CLI）。

**其它遗留：**

- **handler 侧格式化去重**：见上（P1 只做了 external 侧 `format.py`）。
- **create_task 等 leader-only ops**：当前外部 client 未暴露 create，按 teammate 默认
  能力集；leader 角色的外部成员如需 create/assign，后续按 role 扩展。
