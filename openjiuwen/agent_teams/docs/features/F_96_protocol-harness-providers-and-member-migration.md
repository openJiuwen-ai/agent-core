# Protocol Harness Providers 与 Claude Code / Codex 成员迁移

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-09 |
| 范围 | `openjiuwen/harness_providers/`（新包）、`openjiuwen/agent_teams/external/member_runtime.py`、`openjiuwen/agent_teams/external/cli_agent/{spawn,claude,codex}/`、`openjiuwen/agent_teams/spawn/external_cli_spawn.py` |
| 协议版本 | `1.0` |
| 关联 feature | `F_94_external-harness-protocol.md`、`F_95_dsh-external-harness-adapter.md` |
| 测试基线 | `tests/unit_tests/agent_teams`（2879 通过）、`tests/unit_tests/harness`（4126 通过）、`tests/unit_tests/harness_providers` + `tests/unit_tests/agent_teams/external`（211 通过，含 fallback ratification 用例）、`tests/unit_tests/harness_protocol`（27 通过）、`tests/unit_tests/agent_teams/external/test_codex_observability_wiring.py`（5 通过）；e2e：Claude Code 8/8、Codex 8/8（codex-cli 0.153.4，含 `request_user_input`）、DSH 5/5、native 7/7（DeepSeek `deepseek-v4-flash` 端点）通过 |
| Refs | #751 |

## 背景

F_94 定义了 provider-neutral 的 `HarnessProtocol`，F_95 只给 DSH 落了一个 adapter。Claude Code 与
Codex 仍走 `CliRuntimeBase` 子类（`ClaudeSdkRuntime` / `CodexSdkRuntime`）——各自把 SDK 消息直接
转成内部 `OutputSchema`、各自维护 turn 状态机、可靠性分类、认证 fallback、session 持久化，
`external_cli_spawn` 用 `isinstance` 分别接线。三份实现互不复用，协议层无法覆盖真实成员。

同时 DeepAgent 的交互接口已经收敛成与 NativeHarness 相近的 `start` / `attach_output` /
`send_input` / `cancel_round` / `stop`，但没有任何东西把协议输出翻译回 DeepAgent 的输入输出
契约，宿主必须自己解析事件。harness 专家定义的 AgentTemplate manifest 也只能构造 DeepAgent。

## 决策

### 1. 新增顶层包 `openjiuwen/harness_providers`

协议实现从 `agent_teams/external` 上提到与 `harness_protocol` 同级的独立包：`native`（DeepAgent）、
`claudecode`、`codex`、`dsh`（`git mv` 自 `agent_teams/external/dsh`，旧路径保留 re-export shim）。
理由：manifest 工厂要同时引用四个 provider，放在 `agent_teams` 会让 harness 依赖 team 包；放在
`harness` 会让 harness 依赖 vendor adapter。`harness_providers` 依赖 `harness_protocol` 与 `harness`
（仅 native / factory），不依赖 `agent_teams`。

### 2. 一个串行 Turn 骨架

`SerializedTurnHarness` 抽取 F_95 DshHarness 的状态机：lifecycle lock、pending deque、单 supervisor、
每个已接受输入恰好一对 STARTED/terminal、有界 BLOCK 事件流、pending interaction 记账与
abort/stop 时取消、checkpoint 发布（`_publish_checkpoint` / `_restored_checkpoint_data`）。
provider 只实现 `_open_session` / `_close_session` / `_execute_turn`（可选 `_steer` /
`_interrupt_turn`）。DshHarness 迁移到该骨架后原有 12 条单测不改断言全部通过。

### 3. Claude Code / Codex 作为协议 provider 重写

- Claude：一个外部 Turn = `query()` + `receive_response()` 到 `ResultMessage`；`StreamEvent` 文本/思考
  delta → `OutputEvent` DELTA，`AssistantMessage` 块 → FINAL / tool STARTED，`UserMessage` 工具结果 →
  tool COMPLETED，`ResultMessage` → usage / cost / `TurnResult`。STEER 复用 `query()`，abort 走
  `interrupt()`。session id 由 `host_session_id + agent_name` uuid5 派生（与旧 team 命名一致），
  checkpoint 记录 `session_id`；`REQUIRE_RESUME` 用 checkpoint 或派生 id resume。
  `AskUserQuestion` 经 `can_use_tool` 变成 `UserInputRequest`，宿主答案回填 `updated_input.answers`；
  宿主声明 USER_INPUT / TOOL_APPROVAL 时 `permission_mode` 强制为 `default`（`bypassPermissions`
  永不回调）。
- Codex：一个外部 Turn = `thread.turn()` 流到 `turn/completed`；`item/*` 与 `thread/tokenUsage`
  映射为 output / tool item / usage；`error(will_retry)` → WARNING 诊断（`kind=retrying`）并计入
  重试预算，`turn/completed(failed)` → FAILED。thread id 进 checkpoint，`REQUIRE_RESUME` 走
  `thread_resume`。审批请求经低层 client 的 `_approval_handler` 桥到 `ToolApprovalRequest`
  （宿主声明 TOOL_APPROVAL 或 USER_INPUT 时安装）。
- Codex ask-user：SDK 0.144.4 没有 `item/tool/requestUserInput` 的生成类型，但底层 client 把所有带
  id 的服务端请求都交给同一个 `_approval_handler(method, params)`，harness 直接解析该 method：
  `questions[]`（`id / header / question / options[] / isOther / isSecret`）渲染为一条
  `UserInputRequest.prompt`（首题选项作 `choices`，原始 questions 与 `is_blocking` 进 `provider_data`），
  宿主答案按 question id / 题面 / 位置归一化为 `{"answers": {id: {"answers": [...]}}}`。该工具是 CLI 的
  实验特性（`features.default_mode_request_user_input`，默认关闭，模型拿不到工具时会把问题当文本输出
  然后反复 `sleep` 等待），所以宿主声明 USER_INPUT 时 `build_codex_config` 自动追加该 feature override。
  等待人类应答不设总超时：reader 线程按 30s 片轮询 future，只在事件循环关闭时放弃。
- 失败分类器搬入各 provider，输出 `TurnError(category, code, provider_data{sdk_error_type,
  http_status})`；类别集合与 `schema/external_runtime_reliability.py` 一一对应。认证 fallback 保留在
  harness 内（第一次输出前的 `auth_required` 一次性切换到 `fallback_model`），并发布
  `ProviderEvent("auth_fallback_activated")`。
- 认证 fallback 在**提交前**先向宿主 ratify：连上 fallback 端点后，harness 经
  `SerializedTurnHarness._confirm_provider_extension` 发 `ProviderInteractionRequest(request_type=
  "auth_fallback", payload={model, api_base[, provider]})`；宿主未声明 `PROVIDER_INTERACTION` 视为默认
  同意，声明了但应答不是 `COMPLETED` 则 harness 断开 fallback client、以原 session / thread 重新连回
  原生端点并让本 Turn 按 `auth_required` 失败。Claude Code / Codex 两张 card 把 `PROVIDER_INTERACTION`
  列为 optional host capability。

### 4. `HarnessIOAdapter`：协议 ⇄ DeepAgent 输入输出

`harness_providers/io_adapter.py` 是协议对外的 DeepAgent 风格外壳：输入接受用户文本、
`HarnessInput` 与 `InteractiveInput`；输出为 `llm_output` / `llm_reasoning` / `tool_call` /
`tool_result` / `__interaction__`。adapter 自身是 `HarnessInteractionHandler`：`UserInputRequest`
→ `__interaction__` chunk（`InteractionOutput(id=request_id, value={prompt, choices, ...})`），
`send(InteractiveInput)` 按 id 应答；未匹配的 `InteractiveInput` 以 `metadata.kind=interactive_input`
转发给 provider（native 用它恢复中断）。工具审批默认自动放行，`auto_approve_tools=False` 时同样
走 `__interaction__`。原 `ExternalHarnessMemberRuntime` 的投影逻辑整体下沉到这里。

### 5. `ExternalHarnessMemberRuntime` 只做团队层

member runtime 组合 IO adapter，负责：成员 child AgentSession（checkpoint sink 把 provider 信封写进
`external_runtime` state；`TeamContextTracker` 基线）、legacy `harness.state` / `harness.round`
回调、可靠性（`bind_reliability_context`：STARTED → `begin_attempt`，FAILED → `finalize_failure`，
`retrying` 诊断 → `publish_retrying`，启动失败 → `mark_member_error`）、观测桥接
（`bind_span_bridge`：`start_turn` / `finish_turn`，Claude 桥接另外消费投影 chunk，Codex 桥接经
provider-private `notification_observer`）、认证 fallback 持久化（`bind_fallback_promotion`：runtime 以
`HarnessIOAdapter(provider_interaction_handler=...)` 应答 `auth_fallback` 请求，`promote()` 返回 `True`
才 `COMPLETED`，返回 `False` 或抛异常都 `DECLINED`，未绑定 promotion 时直接同意；其它 request type 一律
`DECLINED`）、MCP 挂载（`bind_mcp_servers`）与 teardown hook（Codex OTel receiver / rollout reader）。
`resume_external_backend=True` 要求成员 checkpoint 存在并以 `REQUIRE_RESUME` 启动。

`build_cli_runtime` 的 claude / codex 分支改为构造 provider + `HarnessContext` + member runtime；
`external_cli_spawn` 用 `isinstance(runtime, ExternalHarnessMemberRuntime)` 统一绑定团队工具
（Claude 进程内 SDK MCP 作为 `McpServerConfig(IN_PROCESS)`）与可靠性上下文。
`ClaudeSdkRuntime` / `CodexSdkRuntime` 及其 runtime 模块、测试全部删除。

### 6. manifest 工厂

`harness_providers.create_harness(manifest, provider=..., config=..., language=...)` 接受
`AgentTemplateSpec` 或 `manifest.json` 包路径。`native` 把整份 template 热加载到 DeepAgent；
`claudecode` / `codex` / `dsh` 只取 `model`（端点 → provider 配置），manifest 携带 `tools` /
`rails` / `subagents` 时直接 `ValueError`——这些是 DeepAgent 框架扩展项，不能静默丢弃。
`build_harness_context` 把 persona prompt sections 渲染成 `system_prompt`、manifest MCP 变
`mcp_servers`；native 由 harness 自行加载 template，context 只带额外 prompt。

## 拒绝的方案

- **在 `CliRuntimeBase` 上继续演进 Claude / Codex**：与协议并行维护两套 turn 状态机、两套映射，
  F_94 的 interaction / checkpoint 平面永远进不了真实成员。
- **让 `ExternalHarnessMemberRuntime` 直接投影事件**：投影是 provider-neutral 的宿主需求，团队之外
  的宿主（CLI / 平台）同样要用；放团队层会被复制。
- **把 team 可靠性与观测搬进 provider**：`RuntimeReliabilityContext` / span bridge 依赖 team
  messager、DB 与 OTel 团队 span；provider 只输出结构化 `TurnError` / `DiagnosticEvent` /
  `ProviderEvent`，由团队层消费。
- **factory 对三方 provider 静默忽略 manifest 的 tools / rails / subagents**：会让同一份 manifest 在
  不同 provider 下"看似成功"却行为不同；显式拒绝。
- **factory 放进 `openjiuwen/harness/manifest`**：需要 import 四个 vendor provider，造成 harness →
  vendor adapter 依赖。
- **fallback 先切换、宿主事后经 `ProviderEvent` 持久化**（首版做法）：持久化失败时 harness 已经跑在
  一个团队 DB 里没有记录的端点上，成员重启后又会回到原端点，行为与记录漂移；改为 ratify-before-commit
  的 provider interaction，让"能不能切"由宿主决定、harness 只负责切与回退。
- **等 Codex SDK 出 `requestUserInput` 生成类型再接 ask-user**：底层 client 已把 method 与原始 params
  原样交给 handler，类型只是便利；等类型意味着团队里的 Codex 成员在 Turn 中途提问会被静默丢答案
  （未知 method 返回 `{}`）。
- **让宿主自己在 `config_overrides` 里开 `default_mode_request_user_input`**：能力声明与工具可用性
  会脱节——card 声明 USER_INPUT、宿主也装了 handler，模型却没有工具；由 harness 按 host capability
  自动开关才是真实的能力声明。
- **为 fallback 新增专用协议接口**（如 `HarnessContext.on_fallback`）：这是 provider 私有语义，协议
  已有 namespaced `ProviderInteractionRequest` 正是为此类扩展预留的；专用接口会把 vendor 语义漏进
  provider-neutral 协议。

## 验证

- 单测：`tests/unit_tests/harness_providers/`（base 骨架、IO adapter、Claude / Codex fake SDK、
  factory、DSH）、`tests/unit_tests/agent_teams/external/`（member runtime、spawn 分派到
  provider）、`tests/unit_tests/harness_protocol/`、`tests/unit_tests/agent_teams -m level0`。
- e2e：`tests/system_tests/harness_providers/`——共享 `_contract.py` 校验 STARTED/terminal 配对、
  sequence 单调、tool item 配对、follow-up 串行、abort / steer / ask-user / checkpoint resume /
  manifest prompt。本机结果：Claude Code 8/8、Codex 7/7（codex-cli 0.153.4；0.152.1 对已配置默认
  模型 `gpt-6-astra` 会被服务端以"requires a newer version of Codex"拒绝）、DSH 5/5 通过。
  Codex e2e 暴露并修复了一个竞态：external STARTED 发出时 `thread.turn()` 尚未返回 handle，此窗口内
  的 STEER 现在先排队，handle 建立后立即 `steer`。native e2e（`API_BASE/API_KEY/MODEL_NAME` 指向
  DeepSeek）暴露并修复了 `DeepAgentHarness` 未在 `start` 前 `ensure_initialized()` 的问题：交互循环
  不会自行初始化 agent，pending rails（含观测 rail）从未注册、cwd ContextVar 也未按 `cwd` 初始化。
  manifest 若要文件/shell 工具需显式声明 `core.sys_operation` rail（spec build 不默认挂载）。
- Codex 观测接线（`observer.py` 的 notification → `CodexSpanBridge` 映射、`_start_codex_observability`
  的 receiver / rollout reader 启动与 config/env 注入、`build_cli_runtime` 的绑定与 teardown hook）由
  `test_codex_observability_wiring.py` 以假桥接覆盖。

## 已知遗留

- Codex `request_user_input` 仍是 CLI 实验特性（`default_mode_request_user_input` 标记 under
  development）；协议名 `item/tool/requestUserInput` 与应答形状若随 CLI 变动，`USER_INPUT_METHOD` /
  `_answers_from_response` 需要跟进。`isSecret` 题目当前与普通题同路投影，宿主侧未做遮蔽。
- `dsh` / `native` 尚未接入 `ExternalCliAgentSpec` 声明式 spawn；provider entry point discovery 未做。
- Codex e2e 依赖本机 CLI 支持已配置默认模型；可用 `CODEX_E2E_MODEL` 指定其它模型。
- fallback 回退重连失败后的恢复已由 F_98 补齐：后续输入先尝试一次原端点重连，成功后发送新输入，
  失败则返回结构化错误。没有后台无限重试或失败输入重放。

Portable skills 后续由 F_101 补齐：三方不再拒绝 manifest.skills，改为启动前完整复制到项目扫描目录，
skip 默认保留同名已有技能，replace 完整替换；tools/rails/subagents 仍是原生框架扩展。
