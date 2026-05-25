# External-CLI as a spawn_member Role + Static Spec Config + MCP Auto-Injection

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-25 |
| 范围 | `openjiuwen/agent_teams/schema/team.py`、`schema/blueprint.py`、`tools/team.py`、`tools/team_tools.py`、`tools/locales/`（cn/en + descs）、`agent/agent_configurator.py`、`external/cli_agent/adapters.py`、`external/cli_agent/spawn.py`、`spawn/external_cli_spawn.py`、`tests/unit_tests/agent_teams/external/`、`tests/unit_tests/agent_teams/test_team_tools.py`、`tests/system_tests/agent_swarm/agent_team_external_cli_e2e.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/`：1190 passed, 16 skipped |
| Refs | `#751` |

## 背景

[[F_21_external-agent-access]] 落地了外部 CLI 成员的接入面与 spawn/配置接线，但把两件事
列为"剩余"：

1. **leader 工具**：`spawn_external_cli_agent` 只是 `TeamBackend` 的 SDK/operator 方法，
   leader LLM 无法自主拉起外部 CLI 成员。
2. **MCP 配置注入**：spawn 路径只注入了 `OPENJIUWEN_TEAM_JOIN` env，未把团队 MCP server
   （`openjiuwen-team-mcp`）注册进各 CLI——外部 CLI 实际拿不到协同工具，只能靠用户全局配置。

本特性把这两项补齐，并按"配置静态预置在 team spec、spawn 调用只传标识"的取向收口：外部 CLI
的全部启动知识（命令、cwd、MCP 注入、env）静态声明在 `TeamAgentSpec.external_cli_agents`，
运行时 `spawn_member(role_type='external_cli', cli_agent=<name>)` 只按名引用。

## 数据结构

- `schema/team.py:ExternalCliAgentSpec`：一条 CLI 种类的静态启动配置——`cli_agent`（种类标识，
  如 `claude`/`codex`）、`command`（可选 argv 覆盖）、`cwd`、`inject_mcp`、`mcp_server_command`
  （默认 `["openjiuwen-team-mcp"]`）、`env`。一条配置服务该种类下拉起的所有成员；每个成员仍各自
  起子进程、各自带 team-join 身份。
- `schema/blueprint.py:TeamAgentSpec.external_cli_agents: list[ExternalCliAgentSpec]`：非空声明集
  即外部 CLI 成员的**能力上限**（capability ceiling，镜像 `enable_bridge` 的语义，但配置而非布尔）。
  `model_validator` 拒绝重复 `cli_agent` 名。
- `TeamBackend._external_cli_configs: dict[cli_agent, ExternalCliAgentSpec]`：由 spec 传入，
  `external_cli_config()` / `external_cli_kinds()` 查询。与既有的 `_external_cli_specs`
  （member→cli_agent 注册表，见 F_21）分工：configs 是"种类怎么启动"，specs 是"哪个成员是外部 CLI"。

## 决策

- **复用 `spawn_member` 工具、用 `role_type` 区分**（用户定）：`role_type` enum 增加
  `external_cli`，新增 `cli_agent` 参数。`SpawnMemberTool._spawn_external_cli` 校验 `cli_agent`
  与 `desc` 非空后转调 `backend.spawn_external_cli_agent`。不新建独立工具——外部 CLI 成员的团队
  角色仍是 TEAMMATE，`external_cli` 只是工具层的装配分支（与 bridge_agent 同构）。
- **能力上限 = 静态配置集**（用户定）：`spawn_external_cli_agent` 先校验 `cli_agent ∈
  _external_cli_configs`（未声明直接拒绝），再校验是已知 adapter。配置不在 spawn 调用里传，
  避免在工具参数上堆 CLI 启动细节。
- **MCP 注入下沉到 adapter + spawn 路径**（用户定）：`CliAgentAdapter.mcp_inject` 字段 +
  `mcp_launch_args(...)` 方法按 CLI 产出注册 argv——claude 用 `--mcp-config <inline-json>`，
  codex 用 `-c mcp_servers.<key>.command=...`（dotted key 把 `-` 归一为 `_`）。
  `build_cli_runtime` 新增 `inject_mcp`/`mcp_server_name`/`mcp_server_command`/`extra_env`，
  在流式路径把 mcp_args 追加进启动命令。**descriptor env 不写进 MCP 配置**：MCP server 是 CLI
  的子进程，继承 CLI 进程 env（含 `OPENJIUWEN_TEAM_JOIN`），每个成员的 server 自动绑定到该成员
  身份。一次性 CLI（openclaw/hermes）的 MCP 走带外注册（`hermes mcp add` 等），`mcp_inject=none`。
- **静态配置在 spawn 时取用**：`external_cli_spawn` 按 `ctx.cli_agent` 在 `team_agent.spec.
  external_cli_agents` 里查到配置，把 `command`/`cwd`/`inject_mcp`/`mcp_server_command`/`env`
  透传给 `build_cli_runtime`；查不到则回退默认（成员注册时已校验过存在）。
- **descriptor env 优先级**：`build_cli_runtime` 里 `env = {**os.environ, **extra_env,
  **descriptor.to_env()}`——descriptor 最后应用，`extra_env` 配错也盖不掉团队身份。

## 拒绝的方案

- **新建独立的 `spawn_external_cli` 工具**：拒绝。用户明确要求复用 `spawn_member` + role_type
  区分；外部 CLI 成员的团队语义就是 teammate，独立工具会割裂角色模型、重复权限/描述。
- **把 CLI 启动参数（命令、cwd、MCP 配置）放进 spawn 调用**：拒绝。用户要求静态预置在 team spec、
  spawn 只传种类标识。把启动细节堆到工具参数上会让 LLM 调用面臃肿且易错。
- **在 MCP 配置里内联 descriptor env**：拒绝。会把（较大的）descriptor JSON 灌进命令行；
  env 继承本就能让子进程 MCP server 拿到 per-member descriptor，更干净。
- **额外加一个 `enable_external_cli` 布尔开关**：拒绝。`external_cli_agents` 非空集本身就是能力
  上限，再加布尔是冗余的第二真相源。

## 验证

- 新增/更新单测：
  - `external/test_cli_agent.py`：claude/codex `mcp_launch_args` 形态、一次性 CLI 无注入。
  - `external/test_external_backend.py`：能力上限（未声明 cli_agent 被拒）、未知 adapter、persona 必填、
    重构为 backend 工厂 fixture 以支持不同声明集。
  - `test_team_tools.py`：role_type enum 含 `external_cli` + `cli_agent` 参数；工具层 external_cli
    分支（cli_agent 必填 / 未声明拒绝 / 声明后成功注册）。
- `pytest tests/unit_tests/agent_teams/`：1190 passed, 16 skipped。
- 系统测试 `tests/system_tests/agent_swarm/agent_team_external_cli_e2e.py`：组建 4 人临时团队
  （2 claude + 2 codex），各在团队 workspace 写一个文件、leader 校验存在、`clean_team` 解散。
  需真实 claude/codex 二进制 + leader LLM endpoint（`API_BASE`/`LEADER_API_KEY`/`MODEL_NAME`）+
  PATH 上的 `openjiuwen-team-mcp`，故仅手动运行、不进 CI；脚本以退出码 0/1 自校验。本次按需求**只
  编写、未实跑**（凭据由用户自验）。

## 已知遗留

- **真实 CLI 端到端验证**：adapter 的输入 framing / 轮次完成 / MCP 注入 argv 仍依赖各 CLI（及
  版本）真实行为，需在能跑 claude/codex 的环境实测调参（adapters.py 注释逐条标注置信度）。
- **codex MCP 注册的 `-c` 值解析**：`-c mcp_servers.<key>.command="..."` 的引号/嵌套键解析按
  codex 文档假设，待真实版本核对。
- **一次性 CLI 的 MCP 注入**：openclaw/hermes 走带外注册，未在 spawn 路径自动化。
- **predefined 外部 CLI 成员**：跨进程冷恢复仍需 predefined 声明（role_type=TEAMMATE 判别 union
  冲突），同 F_21 遗留。
