# S_19 Harness Providers（协议实现、IO Adapter 与 Manifest 工厂）

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness_providers/`（`base.py` / `stream.py` / `io_adapter.py` / `factory.py` / `inputs.py` / `jsonsafe.py` / `native/` / `claudecode/` / `codex/` / `dsh/`） |
| 最近一次修订日期 | 2026-10-13 |
| 关联 feature | F_03_harness-providers-and-manifest-factory.md、F_07_third-party-harness-provider-packages.md、F_08_provider-owned-model-config.md、F_09_auth-fallback-as-a-protocol-capability.md、F_10_hot-replacing-installed-providers.md、agent_teams F_113_external-harness-builtin-model-selection.md |

## 范围 / 边界

本规约定义 `openjiuwen.harness_protocol` 的内置实现包：共享的串行 Turn 骨架、五个 provider 的能力
声明、DeepAgent 风格 IO adapter，以及从 AgentTemplate manifest 创建 harness 的工厂。协议契约本身
以 `openjiuwen/harness_protocol/SPEC.md` 为准；团队成员接线见 agent_teams `S_27`。

## 不变量

1. **骨架唯一**：provider 继承 `SerializedTurnHarness`，只实现 `_open_session` / `_close_session` /
   `_execute_turn`（+ `_steer` / `_interrupt_turn`）。每个已接受输入恰好一个 STARTED 与一个 terminal
   `TurnLifecycleEvent`；stop 时排队中的 Turn 以 `HARNESS_STOP` ABORTED 收口；terminal 后队列为空才
   进入 IDLE。`_steer` 收到回执将要上报的 `message_id`，便于 provider 给出站消息打上同一个标识。
   **一个 Turn 覆盖它投递的每一条消息**：steer 被厂商折叠进当前周期、或另起一个周期作答，都算同一个
   Turn，不能在 SDK 给出的第一个 terminal 处收口——否则第二个周期的产出会漏进下一个 Turn。Claude Code
   据 CLI 的 `command_lifecycle` 投递回执判定（`claudecode/lifecycle.py`），细节见
   `harness_providers/AGENTS.md` 不变量 11。
2. **能力声明真实**：

   | provider | card | capabilities | optional host capabilities |
   |---|---|---|---|
   | `native` | `deepagent` | STEER, FORCE_ABORT | USER_INPUT |
   | `native_v2` | `native_v2` | STEER, GRACEFUL_ABORT, FORCE_ABORT, PAUSE_RESUME, CHECKPOINT, PERSISTENT_SESSION | USER_INPUT, CHECKPOINT_SINK |
   | `claudecode` | `claude-code` | STEER, GRACEFUL_ABORT, PERSISTENT_SESSION, CHECKPOINT, MCP_TOOLS | TOOL_APPROVAL, USER_INPUT, CHECKPOINT_SINK, MCP_SERVERS, PROVIDER_INTERACTION, MODEL_REQUEST_OBSERVATION |
   | `codex` | `codex` | 同 claudecode | TOOL_APPROVAL, USER_INPUT, CHECKPOINT_SINK, MCP_SERVERS, PROVIDER_INTERACTION, MODEL_REQUEST_OBSERVATION |
   | `dsh` | `deepseek-harness` | MCP_TOOLS | MCP_SERVERS |

   未声明的命令抛 `UnsupportedHarnessCapabilityError`；`_validate_context` 在 `start` 里 fail-fast。
3. **SDK 惰性加载**：config / provider / 包 import 不导入 vendor SDK；缺 SDK 在 `start` 抛
   `HarnessError`；SDK 启动失败抛 `ProviderStartupError(error: TurnError)`。
4. **失败词汇统一**：`TurnError.category ∈ {auth_required, quota_exceeded, rate_limited,
   server_unavailable, network_timeout, process_start_failed, sdk_error, unknown}`；
   `provider_data` 可带 `sdk_error_type` / `http_status`；`retryable` 由类别推导。
5. **JSON 边界**：进入事件的 vendor 对象一律先 `to_json_safe`；原始 SDK 对象不流向宿主，唯一的
   provider-private 构造参数是 `ClaudeCodeHarness(transport_factory)`（宿主提供 SDK transport）。
   模型请求观测由 provider 内部完成（Claude 请求日志 / Codex rollout trace），以
   `ModelRequestEvent` 交付，见 [[F_112_harness-protocol-trajectory-observation]]。
6. **用户输入是 interaction**：Claude `AskUserQuestion`、Codex `request_user_input`
   （App Server 请求 `item/tool/requestUserInput`）与 DeepAgent `ask_user` 中断映射为
   `UserInputRequest`，Turn 在应答前保持 RUNNING；宿主未提供 handler 时 Claude 拒绝该工具、Codex 回
   空 `answers`、DeepAgent 以 `stop_reason="interrupt"` 结束 Turn 并保留 `pending_interrupt_ids`。
   Codex 的该工具是 CLI 实验特性，宿主声明 USER_INPUT 时 harness 自动追加
   `features.default_mode_request_user_input=true`；多题请求渲染为一条 prompt，首题选项作 `choices`，
   原始 `questions` 进 `provider_data`，宿主答案按题 id / 题面 / 位置归一化回
   `{"answers": {id: {"answers": [...]}}}`。
   `DeepAgentHarness._open_session` 必须在 `agent.start` 之前 `ensure_initialized()`：DeepAgent 的
   交互循环不会自行初始化 agent，pending rails（含观测 rail）与 cwd ContextVar 都在此时落定，
   随后创建的 supervisor / scheduler task 才能继承。
7. **IO adapter 是唯一 DeepAgent 投影**：`HarnessIOAdapter` 输出 `llm_output` / `llm_reasoning` /
   `tool_call` / `tool_result` / `__interaction__`（`InteractionOutput(id=request_id, value=...)`）；
   `tool_result` 的 `result` 是结构化值，provider 提供模型可见文本时另带独立字段 `rendered_result`
   （DeepAgent：`_ObservationRail` 写入流式块，`_consume_chunk` 放进 `ItemLifecycleEvent.data` 与
   `ContentBlock.data`，见 `S_05` 不变量 11）；
   DELTA 直出，FINAL/SNAPSHOT 只补前缀增量；`send(InteractiveInput)` 先应答 pending interaction，
   未匹配时以 `metadata.kind="interactive_input"` 转发；`delivery_mode(immediate)` 按状态与 STEER
   能力选 AUTO / STEER / FOLLOW_UP。
8. **认证回退由骨架执行、先 ratify 再提交**（F_09）：card 声明 `AUTH_FALLBACK` 的 provider 只实现
   `_auth_fallback_payload` / `_reconnect_for_auth(fallback=)` / `_on_auth_endpoint_changed(fallback=)`
   三个钩子；`SerializedTurnHarness` 在回合 `FAILED` 且类别为 `auth_required` 时执行
   `_activate_auth_fallback`：supervisor 每回合至多调用一次、每 cycle 至多激活一次，先在回退模型上恢复同一会话重连，再经
   `_confirm_provider_extension` 发 `ProviderInteractionRequest`（`AUTH_FALLBACK_REQUEST_TYPE`，payload
   `{model, api_base[, provider]}`）；宿主未声明 `PROVIDER_INTERACTION` 或未装 interactions 时视为同意。
   确认后置 `fallback_activated`、发 `AUTH_FALLBACK_EVENT_TYPE` 并重放本回合；应答非 `COMPLETED` 时重连回主模型、
   当前 Turn 按原 `auth_required` 失败，不发布激活事件。确认处理器抛异常或应答不合协议等同拒绝；回退流程的任何
   异常都不得逃出 supervisor，回合保留原失败照常终结；宿主确认之后 `_on_auth_endpoint_changed` 失败只记日志，
   照常发激活事件并重放（切换已落库、会话已在回退上）。回退重启失败时 provider 把活动端点复位为主模型，
   下一回合恢复的永远是宿主知道的模型。Claude Code / Codex / 三方 ACP / codex-app-server
   走同一流程，provider 内部不再各自循环重试。
9. **manifest 是 DeepAgent-first**：`create_harness` 对 `native` 传整份 template
   （`NativeHarnessProvider.create({"deep_agent", "agent_template", "session_id", "language",
   "event_buffer_capacity"})`）；对其余 provider 只把 manifest 的模型映射进 provider 配置（显式
   `config` 优先，逐键 `setdefault`），manifest 的 `tools` / `rails` / `subagents` 非空时
   `ValueError`。`build_harness_context` 对三方 provider 渲染 prompt sections 与 MCP，对 `native`
   只放 `extra_system_prompt`。模型映射一律经 `provider_model_config` 调 provider 的
   `ModelConfigProvider.model_config(ModelChoice)`（F_08）：manifest 模型有 `api_base` 即端点模型，
   card 未声明 `ENDPOINT_MODELS` 时 `ValueError`；provider 没有 `model_config` 时 manifest 不能带模型。
   claudecode / codex / dsh 都实现 `model_config` 并声明 `ENDPOINT_MODELS`；团队成员的模型走同一入口。
   `fallback=` 只交给声明 `AUTH_FALLBACK` 的 provider（claudecode / codex），否则 `ValueError`。
10. **模型控制由骨架承担**（F_113）：`SerializedTurnHarness` 实现 `HarnessModelControl`——`list_models`
    / `set_model` 先按 card 门控；`set_model` 在 `_command_lock` 内判空闲则立即经
    `_apply_model_selection` 应用，否则按字段合并进暂存选择，由 supervisor 在 STARTED 之后、
    `_execute_turn` 之前应用，失败发 WARNING `DiagnosticEvent` 且 Turn 照常执行。Claude Code / Codex
    声明 `MODEL_SELECTION` / `MODEL_DISCOVERY`，把选择并入 `_active_model` 与 `_primary_model`（认证
    fallback 被拒后回退的原生端点），所以重连不丢选择；Claude 用 `set_model()` +
    `apply_flag_settings {"effortLevel"}`（SDK 缺该通道则断开、下个 Turn 以新 `--effort` resume），Codex
    只在下一次 `thread.turn(model=, effort=)` 携带覆盖（App Server 粘性）。探测：Claude 读 `initialize`
    的 `models`，Codex 调 `model/list`（过滤 hidden），未启动时用临时 client，只握手不请求模型。
    Codex 仅 `CodexModelConfig.is_external`（有 `provider` 或 `api_base`）时强制
    `deny_all + full_access`；官方内置模型保留 auto-review reviewer。
11. **provider 名的解析顺序固定**（F_07）：内置 `PROVIDER_NAMES` 是封闭集合且永远优先；其次
    `register_harness_provider` 的进程内注册（内置名不可注册）；最后是已安装包在 entry point 组
    `HARNESS_PROVIDER_ENTRY_POINT_GROUP = "openjiuwen.harness_providers"` 中的同名入口，值为
    `HarnessProvider` 类（无参实例化）或实例，加载 / 实例化失败或不满足 SPI 都抛带原因的
    `ValueError`，不静默跳过。组名是对外稳定 API。每次解析都重新读取安装元数据，所以运行中
    `pip install` 的 wheel 下一次解析即可见。**已导入的已安装包被原地替换时下一次解析重新导入**（F_10）：
    以发行包 `RECORD` 的哈希为指纹，指纹变化就把该发行包的顶层包（`top_level.txt`，缺省时取 RECORD 的
    顶层路径）从 `sys.modules` 移除再加载；已创建的 harness 继续用旧模块，新 harness 用新代码。内置与
    进程内注册的 provider 不参与；openjiuwen 自身的改动仍需重启进程。

## 接口契约

```python
def create_harness(manifest: AgentTemplateSpec | str | Path, *, provider: str,
                   config: Mapping[str, Any] | None = None, language: str | None = None) -> HarnessProtocol
def build_harness_context(manifest, *, provider, host_session_id, agent_id=None, agent_name=None,
                          language="cn", cwd=None, env=None, extra_system_prompt=None,
                          host_capabilities=frozenset(), resume_policy=ResumePolicy.NEW,
                          checkpoint=None, checkpoint_sink=None, interactions=None, metadata=None) -> HarnessContext
def resolve_provider(provider: str) -> HarnessProvider          # 内置 > 注册 > entry point
def available_providers() -> tuple[str, ...]
def register_harness_provider(name: str, factory: Callable[[], HarnessProvider]) -> None
def unregister_harness_provider(name: str) -> None
PROVIDER_NAMES == ("native", "native_v2", "claudecode", "codex", "dsh")
HARNESS_PROVIDER_ENTRY_POINT_GROUP == "openjiuwen.harness_providers"
def provider_model_config(provider: str, choice: ModelChoice, *, fallback: ModelChoice | None = None) -> dict
# provider 可选 SPI（harness_protocol.ModelConfigProvider）：
#   model_config(choice: ModelChoice, *, fallback: ModelChoice | None = None) -> JsonObject
def install_skills(sources, *, provider, cwd, conflict="skip", scan_dir=None) -> tuple[Path, ...]

class HarnessIOAdapter:
    def __init__(self, harness, *, event_observer=None, auto_approve_tools=True,
                 stop_on_unsupported_force_abort=False, provider_interaction_handler=None)
    # provider_interaction_handler 非空时 prepare_context 追加 HostCapability.PROVIDER_INTERACTION，
    # handle(ProviderInteractionRequest) 转交该 handler；为空时一律 DECLINED
    async start(context) / stop(); outputs() -> AsyncIterator[OutputSchema]
    async send(content, *, immediate=False) -> SendReceipt | None
    async abort(*, immediate=False) / pause() / resume(*, query=None)
    has_pending_interrupt(); is_pending_interrupt_resume_valid(user_input); pending_interrupt_ids
    async handle(request) / cancel(request_id, *, reason)   # HarnessInteractionHandler
```

provider 配置模型：`ClaudeCodeHarnessConfig`（`cwd` / `add_dirs` / `env` / `inherit_process_env` /
`cli_path` / `model` / `fallback_model` / `session_id` / `permission_mode` / `system_prompt_mode` /
`include_partial_messages` / `max_turns` / `settings` / `event_buffer_capacity`）、
`CodexHarnessConfig`（`cwd` / `env` / `inherit_process_env` / `codex_bin` / `model` / `fallback_model` /
`config_overrides` / `thread_config` / `bypass_approvals_and_sandbox` / `turn_idle_timeout_s` /
`turn_idle_retries` / `max_will_retry_count` / `mcp_*` / `client_*` / `experimental_raw_events` /
`event_buffer_capacity`）、`DshHarnessConfig`（镜像 `DeepSeekHarnessConfig` + `launch_args_override` /
`system_prompt_env_var` / `event_buffer_capacity`）。`from_mapping` 拒绝未知字段。
模型配置：`ClaudeModelConfig`（`model` / `api_base` / `api_key` / `effort`）、`CodexModelConfig`（`model` /
`provider` / `api_base` / `api_key` / `effort`，`is_external` = 有 `provider` 或 `api_base`）；不带端点的
`model` 即 CLI 自身登录（订阅）上的内置模型。

## 数据结构

| 项 | 说明 |
|---|---|
| `PendingTurn` | `content` / `message_id` / `turn_id` / `accepted_mode` / `abort_requested` / `abort_mode` / `stop_requested` |
| checkpoint data | claudecode `{session_id, resumed}`；codex `{thread_id, resumed[, fallback]}`；dsh / native 不发布 |
| `ProviderEvent` | claudecode `system/<subtype>`、`auth_fallback_activated`；codex 未识别 notification、`auth_fallback_activated`；native 未识别 chunk 类型 |
| `DiagnosticEvent` | codex `data.kind == "retrying"`（WARNING）与 pending error（ERROR）；claudecode assistant error（ERROR） |

## 与其它 spec 的关系

- `S_01` / `S_02`：`native` provider 通过 `DeepAgentSpec.build` 与 DeepAgent 交互循环
  （`start` / `attach_output` / `send_input` / `cancel_round` / `stop`）驱动。
- `S_12` / `S_13`：manifest（`AgentTemplateSpec`、`load_agent_template_package`）是工厂输入。
- agent_teams `S_27`：团队成员如何组合本包的 adapter 与 provider。

`native_v2` 实现在 `agent_teams/harness/protocol_adapter.py`，统一工厂只在显式选择时惰性加载。
它复用 NativeHarness 的 manifest snapshot 装配和边界停止，支持父上下文/任务状态 checkpoint 冷恢复。
`native` 的 DeepAgent 实现不变。详情见 team F_97/F_98。

## 系统提示词模式

Codex 和 DSH provider config 提供 `system_prompt_mode: append | replace`。**Codex 默认 append**（与 Claude 一致：宿主提示词加在 CLI 既有配置之上，而不是顶掉它），DSH 默认 replace。
Codex append 读取 app-server config/read 的生效 developer_instructions，并优先采用显式
thread_config.developer_instructions，再追加宿主提示词；每次连接重新从原始配置构造，避免 resume
或 fallback 重复追加。读取失败则启动失败，不静默降级为替换。replace 直接设置字段；均不修改
base_instructions。空宿主提示词不覆盖现有字段。

DSH append 注册独立末尾 section，不改变 prefix/suffix，宿主文本按字面量处理；replace 通过
assembly hook 仅替换 prefix 文本，保留其它 sections（新 prefix 仍遵循原生模板语法）。
显式 system_prompt_env_var 和 append 冲突时校验失败。见 team F_100。

## Portable skills

三方 provider 接受 manifest.skills；每个 SkillSpec.dir 可指向单个含 SKILL.md 的 bundle 或包含多个
bundle 的 library。包路径按现有 manifest loader 解析为绝对路径，内存配置也建议传绝对源路径。
同名的 config.skills 显式覆盖 manifest 声明；skill_conflict 为 skip（Provider 默认）、replace 或 append。

start 在 SDK 启动前复制完整目录到 cwd/.claude/skills（claudecode）、cwd/.agents/skills（codex）、
cwd/.dsh/skills（dsh）；第三方 provider 没有内置条目，必须传 `scan_dir`（项目内相对路径，越界拒绝），
缺省时 `install_skills` 抛 `ValueError`。cwd 优先取 HarnessContext，再取 provider config，再取当前进程目录。
名称取 SKILL.md front matter.name，缺省取目录名；同名按不区分大小写比较，同时识别已有目录里的
声明名。enabled_skills 非空时筛选声明名；mode 仍被解析校验，但原生 CLI 决定加载/调用方式，
不仿造 DeepAgent 的 auto_list 工具。skip 保留已有目录全部内容，replace 完整替换（清除旧文件），
append 新增带序号的独立目录和技能名；专家团 manifest 装配的外部成员默认使用 append。
多源重名按声明顺序处理。临时完整副本切换失败会恢复原目录。复制结果跨 stop 保留。

复制保留普通文件、子目录、隐藏资源和可执行位；内部链接物化成文件，越界/循环链接拒绝。
Claude 指定 portable skills 时显式启用 SDK skills=all，并保留 user/project/local settings 来源；
DSH sdk-minimal 自动挂载原生 skill/skill-filesystem/tool-skill 插件。SSH/custom Claude transport
不自动上传本地技能，显式报错而非复制到错误主机。见 team F_101。
