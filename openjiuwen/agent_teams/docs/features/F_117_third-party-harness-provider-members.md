# F_117 三方 harness provider 作为团队外部成员

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-12 |
| 范围 | `openjiuwen/agent_teams/external/cli_agent/{backends,spawn}.py`、`schema/team.py`、`schema/external_runtime_reliability.py`、`spawn/external_cli_spawn.py` |
| 关联 spec | S_27（不变量 13，接口契约） |
| 测试基线 | `tests/unit_tests/agent_teams/external` + `harness_providers` + `harness_protocol` 428 通过；`tests/unit_tests/agent_teams -m level0` 1331 通过 |
| Refs | #2006 |

## 背景

harness F_07 让 openjiuwen 能经 entry point 组 `openjiuwen.harness_providers` 发现安装进来的三方
provider 包（opencode、kimi-code 等由 `harness-provider-integration` skill 生成），`create_harness`
可以直接建出它们。但团队的外部 CLI 成员仍按名字写死：`build_cli_runtime` 只把 `claude` / `codex`
接到协议 harness，其余名字一律落到 gemini / openclaw / hermes / generic 子进程适配器；
`ExternalCliAgentSpec` 也只允许 claude / codex 带 `cli_path`。三方 provider 装上了，团队却用不上。

jiuwenswarm 的设置页要让用户上传 provider 包（可附 CLI 可执行文件）并像 Claude / Codex 一样开关，
需要团队这一侧先能按名字把成员分派给三方 provider。

## 决策

1. **backend 注册表新增第三类 `provider`**：`backend_for(name)` 依次查 SDK backend（claude / codex）、
   子进程适配器、三方 provider。三方 provider = `harness_providers.available_providers()` 里不属于内置
   `PROVIDER_NAMES` 的名字（进程内 `register_harness_provider` 注册的，或经 entry point 安装的）。
   内置名单与适配器优先，同名的三方包永远够不着；jiuwenswarm 上传时据此拒绝保留名。
2. **`build_cli_runtime` 用 `resolve_provider(name).create(config)` 建 harness**，套上与 claude / codex
   相同的 `ExternalHarnessMemberRuntime`，因此 checkpoint、可靠性、轨迹、团队上下文搭车全部复用。
   传给 provider 的配置只取生成包共有的键：`cwd` / `env`（继承进程环境 + 团队 join descriptor）/
   `inherit_process_env=False` / `skills` / `skill_conflict`，团队配了才加 `cli_path` 与
   `system_prompt_mode`。系统提示词走 `HarnessContext.system_prompt`，由 provider 自己决定怎么送达。
3. **团队工具走 stdio 团队 MCP**：provider card 声明 `MCP_TOOLS` 时，把 `mcp_server_command`
   （默认 `openjiuwen-team-mcp`）作为 `McpServerConfig(STDIO)` 绑定，env 只带 `MCP_SERVER_ENV_VARS`
   （`OPENJIUWEN_TEAM_JOIN` / `OPENJIUWEN_HOME`），MCP 子进程据此绑定成员身份——与适配器型 CLI
   的团队工具同一条通道。未声明 `MCP_TOOLS` 的 provider 只告警，成员没有团队工具。
4. **成员模型经 provider 的 `model_config` 协商**（同日两次修订：首版一律丢弃团队模型，自验证中
   opencode / kimi 成员只能跑在本机 CLI 配置上；中间一版用启动后 `set_model` 加探针判断端点，被
   harness [[F_08_provider-owned-model-config]] 的统一方案取代）。团队只表达 `ModelChoice`：
   - **内置模型**（leader 从 `builtin_models` 挑的）与**端点模型**（池分配或静态 `model_config`）都经
     `provider_model_config` 映射进 provider 配置，claude / codex 也走这条路，团队层不再有按 provider
     名的模型分支；provider 不能跑的选择（如 ACP 带 effort）是 `AGENT_TEAM_CONFIG_INVALID`。
   - 端点模型只交给声明 `ENDPOINT_MODELS` 的三方 provider；未声明时 spawn 丢弃并记日志，
     `_provider_filter_for_cli` 也不向 leader 列出池里的模型。
   - 认证 fallback 按能力协商（harness [[F_09_auth-fallback-as-a-protocol-capability]]，修订于
     2026-10-13：此前写死为 Claude / Codex 专属，导致 kimi 成员因必填的 `fallback_model_name` 无法创建）：
     card 声明 `AUTH_FALLBACK` 的三方成员拿到池里的回退模型并绑定持久化，其余不带；`fallback_model_name`
     改为可选。运行中切换不变，仍是 `set_model_selection`（card 声明 `MODEL_SELECTION` 才可用）。
   `ExternalCliAgentSpec` 的 `model_config` / `builtin_models` 改为只拒绝适配器型 CLI。
5. **只拒绝真正不适用的选项**：`command_override` / `codex_bin` / `ssh_transport` / claude 与 codex
   专属超时、缓冲参数配在三方成员上时直接 `AGENT_TEAM_CONFIG_INVALID`，而不是静默忽略。
   `ExternalCliAgentSpec.cli_path` 改为只拒绝适配器型 CLI（它们用 `command` 改整条 argv）。
6. **可靠性 agent kind 放宽为 `str`**：`ExternalRuntimeAgentKind` 从 `Literal["claude", "codex"]` 改为
   `str`，三方成员的失败 / 重试消息带 provider 名字。读方只是展示与路由，不枚举取值。

## 拒绝的方案

- **启动后用 `set_model` 应用成员模型**：只覆盖声明 `MODEL_SELECTION` 的 provider，还给成员 runtime
  多加一个概念；配置期映射对所有 provider 都成立（见 harness F_08）。
- **探针判断 provider 能否接收端点**：能力应由 card 声明（`ENDPOINT_MODELS`），不该从钩子行为推断。

- **为三方 provider 写一个通用 `CliRuntimeBase` 适配器**：等于在协议之外再维护一套 turn 状态机，
  F_96 刚把 claude / codex 迁出这条路。
- **把团队模型池映射到 provider 的配置**：各 provider 的模型字段名、endpoint 语义都不同，团队层猜
  不对；需要时应由 provider 声明映射钩子，而不是团队层硬编码。
- **三方 provider 也走进程内团队工具（Claude 式 in-process MCP / Codex 式 Dynamic Tools）**：两条
  通道都依赖具体 SDK 的进程内接口，三方 provider 大多是 CLI 子进程，stdio MCP 是唯一通用的形状。
- **允许三方包覆盖适配器或内置名字**：同名覆盖让同一份团队配置在不同机器上落到不同实现。

## 验证

- 单测：`tests/unit_tests/agent_teams/external/test_external_cli_spawn.py` 新增 4 项（进程内注册假
  provider：分派、配置透传、stdio 团队 MCP 绑定、无 MCP 时不绑定、拒绝不适用选项、backend 识别与
  `cli_path` 校验）；`test_cli_backends.py` 更新适配器 `cli_path` 断言。
  `tests/unit_tests/agent_teams/external` + `harness_providers` + `harness_protocol` 428 通过；
  `tests/unit_tests/agent_teams -m level0` 1331 通过。
- 真实链路（jiuwenswarm 同批改动）：自带 opencode 的 provider wheel 经 jiuwenswarm 上传接口安装、
  附带 opencode 1.18.31 可执行文件，配置开关产生 `cli_agent: opencode` 条目，openjiuwen
  `backend_for("opencode").kind == "provider"`，`build_cli_runtime` 建出成员 runtime：PATH 上没有
  opencode 的进程里跑通真实 Turn（回复 `pong`）；打开 `inject_mcp` 后成员能列出
  `openjiuwen-team_view_task` / `openjiuwen-team_send_message`，stdio 团队 MCP 注入生效。
- 模型协商（同日）：harness-expert 的 opencode 包源码构建 wheel、经上传接口安装（附 opencode 2.0.27），
  jiuwenswarm 探测出 opencode 免费模型目录，按 `opencode/big-pickle` 启动成员后会话当前模型即为它、
  真实 Turn 回复 `pong`；运行中 `set_model_selection(opencode/nemotron-3-ultra-free)` 后当前模型切换、
  真实 Turn 回复 `ping`。opencode 服务端已下线的模型（如 `exo-free`）在 Turn 里失败，属厂商侧。

## 已知遗留

- 未在完整团队（leader LLM + `spawn_external_cli`）里跑过三方成员；团队层只验证到成员 runtime 与
  团队 MCP 注入。
- 三方成员的 `resume_external_backend` 依赖 provider 是否声明 CHECKPOINT；未声明时冷恢复会以
  `HarnessStateError` 失败，与 claude / codex 行为一致但未单独验证。
