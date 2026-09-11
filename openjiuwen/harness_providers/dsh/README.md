# DeepSeek Harness external adapter

本包使用 DeepSeek Harness（DSH）Python SDK 实现
`openjiuwen.harness_protocol` 1.0。它负责 provider session 与 DSH notification 归一化；输入排队、
lifecycle 状态机和有界事件流来自 `harness_providers.base.SerializedTurnHarness`；
`harness_providers.io_adapter.HarnessIOAdapter` 把公共协议投影成 DeepAgent 风格的输入输出，
`agent_teams.external.member_runtime.ExternalHarnessMemberRuntime` 再在其上叠加团队行为。

## 当前边界

```text
DshHarnessProvider / create_harness(manifest, provider="dsh")
       |
       v
   DshHarness  -- HarnessProtocol 1.0（SerializedTurnHarness 子类）
       |
       v
HarnessIOAdapter  -- DeepAgent OutputSchema / InteractiveInput 契约
       |
       v
ExternalHarnessMemberRuntime  -- AgentTeam MemberRuntime / StreamController
```

DSH 支持程序化构造与 manifest 工厂（`create_harness(..., provider="dsh")`），尚未注册到
`ExternalCliAgentSpec` / `build_cli_runtime` 的声明式 member spawn。Claude Code / Codex 已作为同级
provider 迁入 `harness_providers.claudecode` / `harness_providers.codex`。

## Turn 与事件映射

一个 OpenJiuwen Turn 是一次串行 `Session.run()` activity interval：adapter 开始派发已接受输入，
DSH 以 prompt durable receipt 作为通知收集边界，并持续到整个 agent（含子 agent）进入 idle。
DSH native turn/step 不改变该边界：

- native turn 作为 `ProviderEvent` 保留；
- native step 映射为 `item_type="step"` 的 `ItemLifecycleEvent`；
- assistant text/reasoning chunk 映射为 `OutputEvent` DELTA；assistant message 提供 FINAL；
- tool call/result 映射为 tool item lifecycle；
- usage 映射为 `UsageUpdatedEvent`；
- 未识别 notification 作为 namespaced `ProviderEvent` 保留。

adapter 会在接受输入时立即返回外部 `message_id`/`turn_id`。如果已有 Turn 正在运行，后续 AUTO
输入会作为 FOLLOW_UP 排队；整个 follow-up 链保持 Harness RUNNING，只在队列耗尽后进入 IDLE，
不会通过并发 `Session.run()` 模拟 steer。SDK 在 idle 时没有任何 native `turn/end` 属于协议失败，
不会被当作空成功；`turn/end` error 的原始 message 也会在 ProviderEvent 中脱敏。

## 能力矩阵

`DshHarness.card.capabilities` 当前为 `{MCP_TOOLS}`。

| 行为 | 首版支持 | 说明 |
|---|---|---|
| 多轮 session | 是 | 同一 live DSH runtime/session 内串行运行多个外部 Turn |
| 持续/单 Turn event cursor | 是 | 同一逻辑单消费者流，有界 BLOCK backpressure |
| steer | 否 | DSH Python SDK 当前没有运行中插入 API |
| abort | 否 | graceful/force abort 均不声明 |
| pause/resume | 否 | 不声明 warm/cold resume |
| checkpoint | 否 | `export_checkpoint()` 返回 `None`；拒绝 checkpoint/REQUIRE_RESUME |
| MCP 启动装配 | 是 | STDIO/HTTP 通过临时 overlay 挂载原生 mcp-client，不支持 IN_PROCESS 或运行中热更新 |
| system prompt | 是 | 标准 profile 启动时配置原生 system-prompt；保留显式环境变量模式 |

DSH SDK server 0.1.5rc1 仍只提供 initialize、session/prompt、shutdown。跨 runtime 复用 session ID
实际返回 `session already exists`（server 只调用 agents.create，没有 resume 路径）。因此不声明
persistent session/checkpoint，也不把 shutdown/restart 当作 turn abort 或暂停恢复。

`ExternalHarnessMemberRuntime(..., stop_on_unsupported_force_abort=True)` 可在 AgentTeam 请求
hard-cancel 时停止整个 Harness cycle。这只是 MemberRuntime 兼容策略，不是 DSH turn force-abort，
也不增加 FORCE_ABORT capability；默认 `False` 会严格拒绝不支持的请求。

公开 protocol event buffer 是有界 BLOCK 队列，但 DSH SDK 内部 subscription queue 当前仍无界。
因此本 adapter 只能保证自身公开事件缓冲有界，无法消除上游 SDK 在慢消费者场景中的潜在积压。
BLOCK 同样约束停机：调用方应在发起 `stop()` 前启动唯一的 `events()` consumer，并让它持续读取到
EOF；先等待 `stop()`、再回头读取事件，在缓冲区已满时会形成符合背压语义的等待。通用
`ExternalHarnessMemberRuntime` 已按这个顺序持续消费。event callback 不得在 event pump 自身直接
`await runtime.stop()`；需要停机时应设置信号或创建独立 control task。

## Optional SDK

`deepseek-harness-sdk` 是 optional dependency，声明在 pyproject 的 `dsh` extra 里。导入
OpenJiuwen、本包的 config/provider 或协议不会导入 SDK；只有 `DshHarness.start()` 才会
lazy import `deepseek_harness`（发布包名是 `deepseek-harness-sdk`，import 名不同）。

```bash
uv pip install 'openjiuwen[dsh]'
```

该 SDK 迄今只发布过预发布版本，因此 extra 的下限写成 `>=0.1.5rc1`——PEP 440 要求约束里出现
预发布标识，resolver 才会考虑预发布。它会连带装上同版本的 `deepseek-harness-runtime-bin`
平台 wheel（覆盖 macOS arm64 / x86_64 / linux x86_64 / linux aarch64 / win amd64）。

在 DSH 源码 checkout 中开发时改用可编辑安装：

```bash
uv pip install -e /path/to/deepseek-harness/python/sdk
```

缺少 SDK 时，start 会抛出 `HarnessError`，不会让公共 package import 失败——单元测试
因此不需要装 SDK，它们注入的是 fake `deepseek_harness` 模块。

## System prompt

DSH Python SDK 没有独立 system-prompt 参数。适配器把 `HarnessContext.system_prompt` 通过
临时 Cordis plugin 支持 `system_prompt_mode`：

- `replace`（默认）：在原生 assembly hook 只替换 `deployment:persona-prefix` 的文本，保留 suffix、
  identity 及其它 sections。新文本遵循 DSH 的 `{{variable}}` 模板语法。
- `append`：注册独立的 `openjiuwen:host-instructions` section，排在原生 sections 之后，不覆盖 prefix
  或 suffix；通过单次变量替换承载文本，宿主内容中的 `{{...}}` 保持字面量。

不能通过修改 overlay 的整个 system-prompt.config 对象实现 prefix-only 替换：Cordis 会替换整个配置。
`append` 与显式 `system_prompt_env_var` 不可同时设置，避免由两条路径重复或含混注入。

同一 overlay 将 `HarnessContext.mcp_servers` 转为 DSH `mcp-client` 配置。stdio 的 argv/env/cwd 和
HTTP 的 url/headers 均保留；server name 必须唯一且匹配 `[A-Za-z0-9_-]{1,32}`。
SDK initialize 等待 Loader 全部就绪，MCP 首次连接失败时启动失败，不能悄悄少挂工具。

用户 prompt 和 MCP 凭据仅经子进程环境 JSON 传入；临时 patch 文件只包含固定代码表达式和生成的
环境变量名，不将用户字符串拼成可执行表达式。临时目录权限隔离，stop/启动失败均清理；不改用户
home 的模型或 profile 配置。`launch_args_override` 绕过标准 --patch 启动入口，因此不支持自动 overlay。

显式设置 `system_prompt_env_var` 时保留原有 custom Cordis 模式：宿主自己的 composition 负责读取
该变量，适配器不再覆盖 personaPrefix。

完整程序化示例见
[`docs/dev/harness_protocol_integration.md`](../../../docs/dev/harness_protocol_integration.md)。
