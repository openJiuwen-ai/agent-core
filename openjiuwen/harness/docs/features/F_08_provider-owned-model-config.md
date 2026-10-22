# Provider 自有的模型映射：`ModelChoice` + `model_config`

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-12 |
| 范围 | `openjiuwen/harness_protocol/{models,protocol,__init__}.py`；`openjiuwen/harness_providers/{factory,__init__}.py`、`claudecode/` `codex/` `dsh/` 的 provider 与 card；`openjiuwen/agent_teams/external/cli_agent/{spawn,backends}.py`、`spawn/external_cli_spawn.py`、`tools/tool_member.py`；skill 模板 `common/provider` / `package/test_registration` / `sdk/harness` |
| 关联 spec | S_19（不变量 9、接口契约）；协议 SPEC「模型控制」；agent_teams S_27（不变量 13） |
| 关联 feature | F_07（决策 4 被本文取代）；agent_teams F_117（决策 4） |
| 测试基线 | `tests/unit_tests/harness_providers` + `harness_protocol` + `agent_teams/external` + `agent_teams/tools` 609 通过（含新增 `test_model_config.py`）；`tests/unit_tests/agent_teams -m level0` 1335 通过；skill 三种模式生成包各自全绿 |
| Refs | #2006 |

## 背景

宿主要给 harness 指定模型的地方有两个：manifest 工厂（`create_harness`）和团队成员（spawn）。两处都
按 provider 名分支：工厂里 `claudecode` / `codex` / `dsh` 各写一段字典，三方 provider 走 F_07 的
`manifest_config(endpoint)`；团队里 `_claude_model` / `_codex_model` 各转一种类型化配置，三方 provider
又是一条路。于是三方 Agent 接进团队后无法配置模型（自验证 session 里 opencode / kimi 成员只能跑在本机
CLI 配置上，配置坏了无从补救），而要支持它就得在团队层再加一个分支，并靠探针猜 provider 能否接收端点。

## 决策

1. **团队与工厂只表达"要哪个模型"**：协议新增两个只读值对象。`ModelChoice(model, effort, endpoint)`，
   `endpoint is None` 即内置模型（厂商运行时自身登录 / 配置提供的、`list_models` 报告的那类）；
   `ModelEndpoint(api_base, api_key, provider)` 是宿主提供的端点，`provider` 是协议标签，协议不枚举。
2. **映射归 provider**：可选 SPI `ModelConfigProvider.model_config(choice, *, fallback=None) -> JsonObject`，
   返回并入 `create(config)` 的配置片段；不能跑的组合（如 ACP 带 effort、没有 fallback 机制却给了
   fallback）抛 `ValueError`。claudecode / codex / dsh 的映射从工厂与团队层搬进各自 provider，行为平移。
3. **能否接收端点由 card 声明**：新能力 `HarnessCapability.ENDPOINT_MODELS`。claudecode / codex / dsh
   声明；`harness_providers.factory.provider_model_config` 统一门控（无 `model_config` → "does not
   configure models"，端点选择而 card 未声明 → "does not take endpoint models"）。
4. **认证 fallback 交给 provider**：`fallback=` 只有 Claude Code / Codex 接收；"只在内置模型上挂
   fallback"是团队可靠性策略，仍在团队层判断。（后续由 [[F_09_auth-fallback-as-a-protocol-capability]]
   改为 `AUTH_FALLBACK` 能力门控，三方 provider 也可接收。）
5. **manifest 的模型有 `api_base` 才算端点**：`ModelClientConfig` 本身要求 `api_base`，所以 manifest
   模型实际总是端点模型；只接内置模型的 provider（ACP / oneshot 模板）在 `create_harness` 时明确报错，
   不再像 F_07 那样只转发一个它多半不认识的模型名、拖到 start 才失败。
6. **不保留 `manifest_config`**：已安装的旧包（只有 `manifest_config`）在新版下拿不到模型，回落 CLI
   默认配置，需要用新模板重新生成。skill 模板的通用 `model_config` 只写配置声明了的字段，未声明的
   字段（endpoint、effort）直接报错；sdk 模板的配置有 `api_base` / `api_key`，card 声明 `ENDPOINT_MODELS`。

## 拒绝的方案

- **团队层按 provider 名写映射**（现状扩展）：每接一个 provider 改一次团队代码，且团队要知道各家配置
  字段名。
- **启动后用 `set_model` 应用模型**（本日早先的实现，`initial_model_selection`）：只覆盖声明
  `MODEL_SELECTION` 的 provider，启动配置与运行中切换混在一个入口，且多一个成员 runtime 概念；配置期
  映射对所有 provider 都成立，运行中切换仍走 `set_model`。
- **用探针端点调用钩子、看 `api_base` 有没有被带进配置来判断端点支持**：把能力声明变成了行为推断，
  card 才是能力的唯一来源。
- **保留 `manifest_config` 作兼容**：两个钩子表达同一件事，只会让后来者不知道该实现哪个。

## 验证

- `tests/unit_tests/harness_providers/test_model_config.py`：值对象校验；三个内置 provider 实现
  `ModelConfigProvider` 并声明 `ENDPOINT_MODELS`；Claude 主模型 + fallback、Codex 保留协议标签、DSH 拒绝
  effort 与 fallback，映射结果可被各自 `create` 接受。
- `test_provider_discovery.py`：无 `model_config` 的 provider 拒绝 manifest 模型；端点映射与显式
  config 优先；未声明 `ENDPOINT_MODELS` 时拒绝端点模型。
- 团队层：`test_external_cli_spawn.py` 三方成员模型走 `model_config`、拒绝不能跑的模型、spawn 与 leader
  模型池只把端点模型交给声明了 `ENDPOINT_MODELS` 的 provider；claude / codex 既有模型与 fallback 用例
  不改断言全部通过。
- harness-expert：opencode / kimi / codex-app-server 用新模板重新生成 `provider.py` 与注册测试，
  codex-app-server 把 effort 映射为 `reasoning_effort`；三包 harness + 注册测试全过。

## 已知遗留

- 已安装的旧版三方包需要重新生成并重新上传。
- Codex 的模型池标签转换（OpenAI 兼容网关改名 `jiuwen`）仍在团队层 `_external_cli_provider_name`，
  它决定的是池条目的协议标签，没有随本次搬进 provider。
