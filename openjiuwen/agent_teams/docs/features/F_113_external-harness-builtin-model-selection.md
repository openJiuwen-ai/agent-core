# F_113 三方 harness 成员的内置模型切换与 effort 调整

日期：2026-09-18

## 背景

Claude Code / Codex 成员使用官方订阅（CLI 自身登录）时，模型只有两个来源：CLI 内部配置的默认模型，
或经团队 model pool / `ExternalCliAgentSpec.model_config` 传入的端点配置。两个缺口：

1. **不能选订阅内置模型**：订阅本身提供多档模型（Claude `opus` / `sonnet` / `haiku`，Codex
   `gpt-5.6-sol` / `gpt-5.5` 等），但 leader 没有入口按成员挑选；`spawn_external_cli.model_name`
   只在 pool（端点 + 凭证）里解析。
2. **不能调推理强度（effort）**：两家 SDK 都支持 effort（Claude `--effort`，Codex
   `model_reasoning_effort` / turn 级 `effort`），provider 层没有透传，更没有运行中调整的通道。

另外发现一个既有问题：Codex `build_thread_options` 在「配置了任何 model」时就强制
`deny_all + full_access`，理由是外部端点上 auto-review 必然失败——但选官方内置模型仍在官方端点，
reviewer 可用，不该被关掉。

## SDK 能力实测（claude-agent-sdk 0.2.115 / CLI 2.1.206；openai-codex App Server）

| 能力 | Claude Code | Codex |
|---|---|---|
| 模型目录 | `initialize` 响应 `models`：`value` / `displayName` / `supportedEffortLevels` / `resolvedModel`…，`value="default"` 为缺省模型 | `model/list`：`id` / `displayName` / `supportedReasoningEfforts` / `defaultReasoningEffort` / `isDefault` / `hidden` |
| 启动时 effort | `ClaudeAgentOptions.effort`（`--effort`） | thread config `model_reasoning_effort` |
| 运行时切模型 | `ClaudeSDKClient.set_model()`（公开） | `thread.turn(model=)`，**粘性**：本 turn 及之后都生效 |
| 运行时切 effort | SDK 无公开 API；CLI 私有 control request `apply_flag_settings {"effortLevel"}` 可热更新 | `thread.turn(effort=)`，同样粘性 |

探测只做握手（Claude `connect` 读 `initialize`，Codex 起 App Server 调 `model/list`），不发模型请求。

## 决策

### 1. 协议：可选的 `HarnessModelControl`（`harness_protocol`，增量 minor）

- 值对象 `ModelSelection(model, effort)`（`None` = 该字段保持不变，至少设一个）与
  `ModelOption(model_id, display_name, description, efforts, default_effort, is_default, extensions)`。
- capability：`HarnessCapability.MODEL_SELECTION`（`set_model`）与 `MODEL_DISCOVERY`（`list_models`）。
- **独立的 `@runtime_checkable HarnessModelControl` Protocol**，不往 `HarnessProtocol` 加方法：往
  runtime_checkable Protocol 加必需成员会让已有三方实现 `isinstance` 失败，按协议 AGENTS.md 属于
  major 变更。宿主先查 card capability，再调用。
- `list_models()`：已启动读活会话，未启动用 provider 配置做一次临时握手——团队在 spawn 前、宿主在
  配置阶段都可用。
- `set_model(selection)`：空闲时立即应用；有 turn 在跑时合并暂存，**下一个 turn 开始前**应用，
  正在跑的 turn 不换模型；选择在本 cycle 的 provider 重连中保持。
- effort 是字符串，协议层不枚举——两家集合不同（Codex 有 `none` / `minimal` / `ultra`），由 provider
  / 宿主按目录校验。

### 2. provider（`harness_providers`）

- `SerializedTurnHarness` 统一实现两个公开方法：能力门控、`_command_lock` 下判空闲立即应用、否则
  `merge_model_selection` 暂存；supervisor 取出 turn 时一并取走暂存选择，在 STARTED 之后、
  `_execute_turn` 之前经 `_apply_model_selection` 应用。**延后应用失败发 WARNING `DiagnosticEvent`**
  （调用方已经返回，无法再抛给它），turn 仍用原模型继续——不静默吞掉。
- `ClaudeModelConfig` / `CodexModelConfig` 加 `effort`；`CodexModelConfig.is_external`
  （`provider` 或 `api_base` 存在）。
- 两个 harness 引入 `_primary_model`：原生端点 + 运行时选择。认证 fallback 被宿主拒绝后回原生端点时
  用它，而不是 `config.model`，否则运行时切换会在一次 fallback 往返后丢失。
- Claude：`set_model` + `apply_claude_flag_settings`（私有 seam 隔离在 `options.py`）；SDK 没暴露
  control channel 时断开 client，下个 turn 以新 `--effort` resume 同一 session。
- Codex：选择存进 `_turn_overrides`，下一次 `thread.turn(model=, effort=)` 带上后清空（App Server
  粘性）；重连经 thread options / thread config 继承。
- 两者切换后统一发 `ProviderEvent("session/model_changed", {model, effort})`，
  `ExternalHarnessMemberRuntime` 已据此刷新可靠性上下文的模型名。
- **修正 Codex bypass**：只有 `model.is_external` 才强制 `deny_all + full_access`。

### 3. 团队（`agent_teams`）

- `ExternalCliAgentSpec.builtin_models: list[ExternalCliBuiltinModel]`（`name` / `description` /
  `efforts` / `default_effort`），只对 `claude` / `codex` 合法、名字唯一。**未声明则不开放内置模型
  选择**，行为与此前完全一致。目录可以借助 `list_models()` 生成，但以 spec 为准——可控、可测、不依赖
  网络与登录态。
- `ExternalCliModelConfig.effort`；`TeamMemberOptions.builtin_model: MemberBuiltinModel(model, effort)`
  持久化；`TeamRuntimeContext.builtin_model` 由 `build_context_from_db` 填入；`external_cli_spawn`
  优先级 **内置模型 > pool 分配 > 静态 `model_config`**。
- `spawn_external_cli`：属性级门控加 `builtin_model` / `effort`（参数描述里附
  `<builtin_model_catalog>` JSON），描述散文走 capability 槽 `builtin_model_param_rows` /
  `builtin_model_usage`，与 schema 同源门控；与 `model_name` 互斥；`effort` 须伴随 `builtin_model`；
  省略 effort 取 `default_effort`。
- 新 leader 工具 `set_member_model(member_name, model?, effort?)`：仅当某 kind 声明了目录时接线。
  `TeamBackend.set_member_model` **先落库再推活成员**：经 `set_member_model_fn`（`TeamAgent`
  `_apply_member_model` → `SpawnManager.lookup_inprocess_agent` → `ExternalHarnessMemberRuntime.set_model_selection`）。
  成员不在跑则下次启动生效，返回值 `applied_live` 如实告诉 leader。只改 effort 保持模型；只改模型取
  新模型的 `default_effort`。
- 只允许 CLI 自身登录上的成员切换：`model_ref` 非空（用 `model_name` 拉起，或认证 fallback 已持久化）
  的成员被拒绝——内置模型不能跨端点。`promote_member_fallback_model` 同时清掉 `builtin_model`。
- 认证 fallback 的挂载条件从「`external_model_config is None`」改成「原生端点」
  （`api_base` 与 `provider` 都为空）：选了内置模型的成员仍在订阅上，fallback 继续守护它；
  pool 分配（总带 provider / api_base）行为不变。

## 拒绝的方案

- **把 `list_models` / `set_model` 加进 `HarnessProtocol`**：破坏既有三方实现的结构一致性，需要协议
  major 版本。
- **只做启动时静态指定**：用户明确要求会话中途可切；两家 SDK 都有运行时通道。
- **框架硬编码模型别名**：模型迭代要改代码；且订阅档位因账号而异。
- **spec build 时自动探测填充目录**：启动变慢，依赖 CLI 可用与登录态，测试不可控。
- **切换时总是重启成员**：Claude 重连有秒级开销，Codex 本来就是 turn 级参数；只在 Claude SDK 缺
  control channel 时才退化为重连。
- **把运行时选择写进 provider checkpoint**：团队侧 member options 已是单一事实来源，重启由它恢复；
  checkpoint 再存一份会出现两处不一致。

## 验证基线

- `tests/unit_tests/harness_protocol/test_protocol.py`：值对象校验与冻结、`HarnessModelControl` 结构判定。
- `tests/unit_tests/harness_providers/test_base.py`：能力门控、空闲立即应用、运行中合并延后到下一 turn、
  延后失败发 WARNING 且 turn 照常完成、`merge_model_selection`。
- `tests/unit_tests/harness_providers/test_claudecode.py` / `test_codex.py`（fake SDK）：effort 进 options /
  thread config、内置模型不 bypass、目录映射（活会话 / 临时握手、隐藏模型过滤）、运行时切换与
  `session/model_changed`、Claude 无 control channel 时重连、Codex 覆盖只随下一 turn 且重连后保持。
- `tests/unit_tests/agent_teams/tools/test_builtin_model_selection.py`：spec 目录校验、options 往返与
  fallback 提升清除、目录校验与默认 effort、spawn 持久化与各类拒绝、schema / 散文 / 工具集同源门控、
  `set_member_model` 先落库后推活成员、不在跑成员、不可切成员的拒绝。
- `tests/unit_tests/agent_teams/external/test_external_cli_spawn.py`：内置模型与 effort 传到 provider 并
  保留认证 fallback；端点成员不挂 fallback；未运行 harness 的 `set_model_selection` 返回 False。
- 实机握手核对了两家目录字段（见上表），未发模型请求。

## 行为变化

- 静态 `model_config` 只写 `model`（无 `api_base` / `provider`）的 Claude / Codex 成员，此前不挂认证
  fallback，现在会挂——它本就运行在 CLI 自身登录上，fallback 只在明确的认证失败时触发。
- Codex 成员配置了不带 `provider` / `api_base` 的 model 时，不再强制关闭 approval reviewer 与 sandbox。

## 已知遗留

- 目录由 spec 手写，不与账号实际可用模型自动同步；`list_models()` 只提供生成手段。
- `set_member_model` 与成员正在启动（harness 尚未 start）竞争时，活切换抛 `HarnessStateError` 被吞为
  `applied_live=False`；若启动已读取旧配置，本次运行仍用旧模型，直到下次启动。
- 子进程 spawn 模式下成员不在 leader 进程内，`lookup_inprocess_agent` 取不到，只能下次启动生效
  （外部 CLI 成员当前总是 in-process spawn）。
- Claude effort 热更新依赖 CLI 私有 control request，SDK 升级需复核。
