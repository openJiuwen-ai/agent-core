# 认证回退做成协议能力：`AUTH_FALLBACK` + 骨架统一流程

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-13 |
| 范围 | `openjiuwen/harness_protocol/{models,__init__}.py`；`openjiuwen/harness_providers/{base,factory}.py`、`claudecode/harness.py`、`codex/harness.py`、`dsh/provider.py`；`openjiuwen/agent_teams/external/{member_runtime,cli_agent/spawn,cli_agent/backends}.py`、`spawn/external_cli_spawn.py`、`tools/tool_member.py` 与 `spawn_external_cli` 描述；skill 模板 `acp/{config,harness,fake_agent,test_harness}` / `common/provider` / `package/test_registration` |
| 关联 spec | S_19（不变量 8、9）；协议 SPEC「模型控制」；agent_teams S_27（不变量 10、13、14） |
| 关联 feature | F_08（`model_config` 的 `fallback=` 由本文接上能力门控）；agent_teams F_117（决策 4） |
| 测试基线 | `tests/unit_tests/harness_providers` + `harness_protocol` + `agent_teams/external` + `agent_teams/tools` + `agent_teams/test_team_tools.py` 774 通过；`tests/unit_tests/agent_teams -m level0` 1338 通过；skill 三种模式生成包全绿 |
| Refs | #2006 |

## 背景

自验证会话 `web_1a13305dc39_12d361a11829` 里，leader 给 kimi 成员调 `spawn_external_cli` 试了 13 次全部失败：
`fallback_model_name` 对所有 CLI 必填，而 kimi 既不接收端点模型（兼容列表为空），也没有认证回退，唯一合法值
只剩 JSON `null`，leader 发出来的却是空串、`"null"`、省略或真实模型名。

根子在于"认证失败回退到团队模型"被写死成 Claude Code / Codex 的私有行为：两家各在 harness 里实现了一份
几乎一样的流程（约 100 行），团队层再按 `kind == "provider"` 把三方成员的回退丢掉。三方 CLI 想要同样的可靠性，
只能再复制一份。

## 决策

1. **能力由 card 声明**：协议新增 `HarnessCapability.AUTH_FALLBACK`，以及宿主与 provider 之间的两个约定名
   `AUTH_FALLBACK_REQUEST_TYPE = "auth_fallback"`（宿主确认请求）、`AUTH_FALLBACK_EVENT_TYPE =
   "auth_fallback_activated"`（激活事件）。回退模型仍经 `model_config(choice, fallback=...)` 交给 provider；
   `provider_model_config` 在 card 未声明时拒绝（"has no authentication fallback"）。回退目标来自团队模型池，
   总是端点模型，所以实际还要 `ENDPOINT_MODELS`。
2. **流程只写一遍，放在骨架**：`SerializedTurnHarness` 在 `_execute_turn` 返回 `FAILED` 且类别是
   `auth_required` 时调用 `_activate_auth_fallback`：重连到回退模型 → 宿主确认 → 记激活并发事件 → 重放本回合；
   宿主拒绝则重连回主模型并保留原失败。每个回合至多尝试一次、每个 cycle 至多激活一次（`fallback_activated`
   属性上移到骨架）。provider 只实现三个钩子：`_auth_fallback_payload()`（确认与事件的载荷，不含密钥）、
   `_reconnect_for_auth(fallback=)`（在指定模型上恢复同一会话重连，只连接）、`_on_auth_endpoint_changed(fallback=)`
   （切换落定后更新自身状态与事件）。
3. **Claude / Codex 删掉各自的回退循环**：`_run_turn` / `_drive_turn` 变成单次尝试，原 `_fallback_applies` /
   `_maybe_activate_fallback` / `_reconnect_native_endpoint` 删除；Codex 在 `_on_auth_endpoint_changed` 里保留
   原有的 `session/model_changed` 事件与回退 checkpoint。既有回退测试不改断言全部通过。
4. **三方 CLI 按端点能力接入**：ACP 模板配置新增 `endpoint_model` / `fallback_model`（与 `model` 互斥），vendor
   只需提供 `ENDPOINT_ENV(model) -> env`，把端点模型变成会话默认模型；有它即声明 `ENDPOINT_MODELS` +
   `AUTH_FALLBACK`。回退重连 = 带回退端点的环境重启 ACP 进程 + `session/load` 同一会话（agent 不支持
   `loadSession` 时重连报错、回合按原失败）。重启失败也保留会话 id，供"拒绝后切回主模型"与下一回合使用。
5. **团队层看能力，不看名字**：`backends.takes_auth_fallback(cli_agent)` 按 backend 的 `provider` 解析
   （claude → claudecode、codex → codex、三方即自身），要求 `ModelConfigProvider` 且 card 同时声明
   `AUTH_FALLBACK` 与 `ENDPOINT_MODELS`，adapter 恒假。spawn 只把回退交给有能力的三方成员（`_negotiate_provider_fallback`），并给三方成员
   绑定 `bind_fallback_promotion`，与 Claude / Codex 一样先持久化再生效。`spawn_external_cli` 的
   `fallback_model_name` 改为可选：省略、`null`、空白都表示"不回退"；对没有回退的 CLI 给出模型名直接报错并提示
   省略；"存在兼容模型时必须填写"只对有回退的 CLI 生效；模型目录 `compatible_fallback_models` 只列有回退的 CLI。

6. **回退永不打断回合配对**（评审补充）：宿主确认处理器抛异常或返回不合协议的应答，按"拒绝"处理并切回主模型；
   整个回退流程再包一层 `_try_auth_fallback`，任何异常只记日志、保留原失败，保证 STARTED / terminal 配对与
   回合后回到 IDLE。
7. **重启失败可在下一回合恢复**（评审补充）：ACP 与 codex-app-server 的"在某个模型上重启并恢复会话"收敛为
   `_restore_session` / `_restore_thread`，失败时清掉半启动的进程、保留会话 id 并标记待恢复；下一回合开始前先在
   当前模型上恢复同一会话，而不是整个 cycle 都返回连接已关闭。进程崩溃不走这条路，行为不变。
8. **重启恢复运行中选定的模型**（评审补充）：ACP 记录 `_primary_model`，codex-app-server 记录 `_primary_model` /
   `_primary_effort`，运行中切换只在主模型（CLI 登录）上更新它们；拒绝回退后的重启用它们恢复，不回到初始配置。
   codex-app-server 只在真正切到回退时清掉每回合的模型覆盖。
9. **未确认的回退不会被恢复**（二轮评审）：回退重启失败时，ACP / codex-app-server 把活动端点复位为主模型再抛出，
   下一回合的恢复落在宿主知道的模型上，而不是从未确认的回退端点。
10. **确认后的记账失败不吞掉重放**（二轮评审）：宿主确认即已落库、会话已在回退上；此后
    `_on_auth_endpoint_changed` 抛异常只记日志，照常发激活事件并重放。删除骨架里从不触发的
    `_fallback_attempted_turn`：supervisor 本身每回合只调一次回退。
11. **能力声明与能否激活一致**（二轮评审）：ACP 模板经 `_with_auth_fallback` 只在同时有 `ENDPOINT_MODELS` 与
    `PERSISTENT_SESSION`（`loadSession`）时声明 `AUTH_FALLBACK`；团队层同理要求 `ENDPOINT_MODELS`（决策 5），
    否则 leader 会被迫给成员配一个永远无法生效的回退。

## 拒绝的方案

- **在团队成员 runtime 里统一兜底**（认证失败后用回退模型重建 harness、从 checkpoint 恢复再重放）：provider 一行
  不写，但依赖每个 provider 的 checkpoint 恢复，且与 Claude / Codex 的 harness 内回退并存成两套机制。
- **保持回退为 Claude / Codex 专属、只把字段改成可选**：修掉了这次的卡死，但三方 CLI 认证失败时仍直接失败，
  可靠性与 Claude / Codex 不对等。
- **骨架只提供工具函数、各 provider 自己在回合循环里调用**：Claude / Codex 的循环各有细节（Codex 的重试预算），
  调用点分散在 provider 里，三方 provider 还得自己找对位置；放进 supervisor 后"失败 → 回退 → 重放"成为所有
  provider 的正常路径。
- **把字符串 `"null"` 当作 null**：迁就 leader 的一种错误写法，治标；省略即不回退才是表达"没有"的自然方式。

## 验证

- `test_base.py`：骨架流程三条——确认后切换并重放、拒绝后切回主模型保留失败、重连失败每回合只试一次。
- Claude / Codex 既有回退测试（激活、未登录文本、部分输出后、拒绝后切回、Codex 重试预算）不改断言通过；
  `test_model_config.py` 断言 claude / codex 声明、dsh 不声明，dsh 带回退被 factory 拒绝。
- 团队层：`test_external_cli_spawn.py` 有能力的三方成员拿到回退并绑定持久化、无能力的拒绝 / 被 spawn 丢弃；
  `test_team_tools.py` 无回退 CLI 只接受省略（`None` / `""` 成功，`"null"` / 模型名报错并提示省略）。
- skill：ACP 模板的假 agent 只拒绝 CLI 登录，模板测试覆盖"回退后恢复同一会话仍记得上下文"与"拒绝后回到登录"；
  三种模式生成包全绿。
- harness-expert 三个包：opencode（`OPENCODE_CONFIG_CONTENT` 自定义 provider）、kimi（`KIMI_MODEL_*`）、
  codex-app-server（`-c model_providers.openjiuwen.*` + `thread/resume`）都声明两项能力；真实 CLI 上用无效密钥的
  主端点 + 有效回退端点验证：opencode / kimi 走 deepseek、codex-app-server 走本地 Responses 代理，三者都在
  第一回合识别 `auth_required`、激活回退、恢复同一会话重放成功，第二回合仍答出上一回合记住的口令。

- 评审回归：`test_base.py` 确认处理器抛异常 / 应答不匹配时回合正常结束、切回主模型、回到 IDLE，钩子抛异常同样
  结束回合；ACP 模板与两包、codex-app-server 各补"拒绝后重启失败、下一回合恢复同一会话"与"拒绝后保留运行中
  选定的模型"，撤回修复后这两条用例确实失败。
- 二轮评审回归：`test_base.py` 确认后记账钩子抛异常仍重放成功；ACP 模板与两包、codex-app-server 补"回退重启失败、
  下一回合回到登录且不经确认"；模板与两包补 `_with_auth_fallback` 能力门控；`test_external_cli_spawn.py` 补
  "只声明 `AUTH_FALLBACK` 不声明 `ENDPOINT_MODELS` 的 provider 拿不到回退"。

## 已知遗留

- 已安装的旧版三方包没有这两项能力，需重新构建上传；没有能力的 CLI 行为与以前一致（不回退）。
- ACP 端点映射依赖各 CLI 的环境变量约定（opencode 2.0.27、Kimi Code 2.1.1 实测），CLI 升级改名时需同步。
- 只覆盖认证失败；配额耗尽（`quota_exceeded`）不触发回退，与 Claude / Codex 一致。
- 回退激活后宿主再 `set_model` 会在回退会话上切模型，ACP 与 Claude / Codex 行为一致（都照常下发）；暂不限制。
- 工具目录渲染时按 CLI 种类解析 provider 判定能力，与既有 `_provider_filter_for_cli` 同频，未做缓存。
