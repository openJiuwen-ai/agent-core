---
id: F_20261123_1059_external-member-execution-environment
title: 三方 harness 成员的执行环境注入
type: F
author: alan_cheng
author_email: chengshuo5@huawei.com
modules: [agent_teams]
files:
  - openjiuwen/agent_teams/external/member_runtime.py
  - openjiuwen/agent_teams/agent/agent_configurator.py
designs: []
design_note: 无独立 D；把 F_20261104_1720 的成员执行环境扩展到三方 harness 成员，沿用既有 BuildContext 扩展点与 HarnessContext.env 协议字段。
---

# 三方 harness 成员的执行环境注入

## 关联依据

- 设计：无独立 D，见 frontmatter `design_note`。
- spec：S_27 新增不变量 20 与 `bind_execution_environment` 接口条目。
- 日期：2026-11-23。
- 实际实施范围：团队中由 `ExternalHarnessMemberRuntime` 驱动的成员（Claude Code、Codex、经 entry point
  接入的三方 provider，含外部 Leader）在每次 provider `start` 时拿到平台解析的成员环境。
  swarmflow workflow 成员与适配器型 `CliRuntimeBase` backend 未覆盖。

## 决策归属与关系

续写 F_20261104_1720_tool-execution-environment（工具执行环境作用域）与
F_20261104_1721_session-execution-settings（会话级执行设置）。两者的契约不变：原生成员仍在每次工具调用时
解析；本记录只为原生路径之外的三方 harness 成员补一个入口。F_20261104_1720 「边界」一节已注明修订。

## 上下文与跨边界影响

- 产生方：平台（如 jiuwenswarm）子类化 `BuildContext.resolve_execution_environment`，按成员名与团队数据库
  中的会话设置返回 `ExecutionEnvironment`（例如独立 Git 身份的 `GIT_AUTHOR_*` / `GIT_COMMITTER_*`）。
- 改动前（已证实）：只有 `NativeHarness` / `DeepAgentSpec.build` 把 resolver 挂到 `AbilityManager`。
  外部成员进入 `AgentConfigurator.setup_agent` 的 `member_runtime` 分支后直接返回，不派生成员 BuildContext；
  `external/cli_agent/spawn.py` 构造的进程环境只有 `os.environ`、静态 `ExternalCliAgentSpec.env` 和团队加入
  描述符。三方 CLI 自带的 Bash 工具执行 `git commit` 时使用宿主 git 身份。
- 载体：`HarnessContext.env`。claudecode / codex / dsh provider 都按「继承环境 < config.env < context.env」
  合并，三方 provider 按 harness_protocol 契约同样读取它。
- 消费者：provider 拉起的 CLI 进程及其所有子进程。

## 实际实现与开发决策

1. `ExternalHarnessMemberRuntime.bind_execution_environment(resolver)`：在 `_start` 中解析 start context
   之后、调用 provider 之前取一次快照，用 `ExecutionEnvironment.merge(context.env)` 合并，得到
   values < context.env < overrides 的优先级，与原生工具调用里「显式参数」的位置一致。
2. `AgentConfigurator._bind_external_execution_environment`：在外部 runtime 分支中按原生成员同样的字段
   （member_name / role / language / member_card_id）派生成员 BuildContext，extras 复制后注入成员
   `TeamBackend`，再把 `resolve_execution_environment` 绑定给 runtime。一处同时覆盖 spawn 出来的外部成员
   与 `LeaderSpec.cli_agent` 的外部 Leader。
3. 选择在 `start` 时解析，而不是在 `build_cli_runtime` 构造进程环境时解析：构造期成员的 `TeamBackend`
   尚未建立，平台 resolver 读不到会话设置；`start` 每个运行周期都会执行，冷恢复与重启时会重新读取最新设置。
4. 不改 provider：`HarnessContext.env` 已是协议字段，provider 均已消费，无需按厂商分别接线。
5. resolver 抛错直接让 `start` 失败并收尾成员 session（fail closed），与原生工具调用一致，避免以宿主身份
   悄悄提交。

why-not：
- 在 `spawn.py` 的 `_claude_process_env` / `_codex_process_env` 中拼接：只能覆盖内置两家，三方 provider 的
  构造路径不同，而且构造期拿不到成员后端。
- 为三方 CLI 包一层 shell 或 git wrapper 来逐次注入：侵入厂商工具，跨平台成本高，收益仅为「中途切换即时生效」。

## 与设计的偏差

无（无独立设计）。与原生路径的行为差异见「已知遗留」。

## 实际执行与证据

| 执行日期 / 验证范围 | 实现版本与环境 | 命令 / 复现步骤 / 记录链接 | 实际结果 | 未覆盖项 / 失败原因 |
| --- | --- | --- | --- | --- |
| 2026-11-23 runtime 单元 | 本分支工作区，fake harness，macOS | `pytest tests/unit_tests/agent_teams/external/test_member_runtime.py -k execution_environment` | 3 通过 | 无 |
| 2026-11-23 configurator 装配 | 本分支工作区，fake Codex harness + 内存存储 | `pytest tests/unit_tests/agent_teams/external/test_external_leader.py -k "execution_environment or policy_env"` | 2 通过 | 只覆盖外部 Leader 的 configure 路径；spawn 出来的外部成员走同一 `setup_agent` 分支，未单独起用例 |
| 2026-11-23 回归 | 同上 | `pytest tests/unit_tests/agent_teams/external/ tests/unit_tests/agent_teams/workflow/test_external_turns.py`；`pytest -m level0 tests/unit_tests/agent_teams/` | 291 通过；level0 1723 通过、11 跳过、1 xfail | 无 |
| 2026-11-23 端到端（真实 CLI） | 本分支工作区；jiuwenswarm 以 editable 依赖本地 core；Claude Code 2.1.327、codex-cli 0.171.0（`model_config.model=gpt-5.6-sol`）；macOS | 临时脚本：`SwarmBuildContext`（`execution_settings_managed=True`）+ sqlite 团队库写入 `independent_git_identity` → `Runner.run_agent_team_streaming` 驱动 `LeaderSpec.cli_agent` 外部 Leader，让 CLI 用自己的 shell 工具在临时仓库执行 `git commit --allow-empty`；宿主进程预置 `GIT_AUTHOR_*` / `GIT_COMMITTER_*` = `host` | claude 开：`team_leader / team_leader@jiuwenswarm.com`（author 与 committer）通过；claude 关：`host` 通过；codex 开、关同上通过；基线（stash 本次两处源码、开关打开）：仍为 `host`，复现原缺陷 | 未经 jiuwenswarm Web/WS 入口与 `prepare_session_execution`，会话设置由脚本直接写库 |
| 2026-11-23 端到端（真实 CLI，外部 teammate） | 同上；Leader 为 Claude Code | 同一脚本：Leader 依次调用 `build_team`、`spawn_teammate(member_name="dev-1", cli_agent=...)`、`send_message`，由 `external_cli_spawn` 拉起的外部 teammate 执行提交，Leader 自己不提交 | Claude teammate 开：`dev-1 / dev-1@jiuwenswarm.com` 通过；关：`host` 通过；Codex teammate 开、关同上通过；基线（stash 本次两处源码、Claude teammate、开关打开）：仍为 `host`，复现原缺陷 | Codex 关闭组在 teammate 提交之后、脚本拆团队时，Leader 的 Claude 轮次报 `ended the response stream without a result`，是测试脚本提前收尾导致的，不影响结论 |
| 2026-11-23 系统测试（入库） | 同上；core 内自包含，平台策略由脚本内最小 `BuildContext` 子类代替 `SwarmBuildContext`（同样读团队库 `session_settings`，按成员名返回 `GIT_*`） | `python tests/system_tests/agent_swarm/agent_team_external_cli_git_identity_e2e.py --cli claude`；`CODEX_MODEL=gpt-5.6-sol ... --cli codex`（需 claude / codex extras，本次用 jiuwenswarm 的 venv 运行） | claude 4/4、codex 4/4 通过（leader / teammate × 开 / 关） | 提前结束团队流时，日志里会出现 core 已有的 `ContextVar ... created in a different Context`（内层异步生成器在另一个 task 收尾），与本改动无关，不影响结果 |

### 可见行为、副作用与证据有效范围

用例断言 provider 收到的 `HarnessContext.env`：overrides 覆盖同名的 context env，两次 `start` 分别读到当时
的策略；删除项不进入 env；resolver 失败时 provider 未启动且成员 session 已收尾；平台 BuildContext 的基础
extras 未被成员后端污染；没有平台 BuildContext 时 env 为空。

端到端以独立副作用验证：读取临时仓库 `git log -1 --format=%an|%ae|%cn|%ce|%s`，不依赖 CLI 的自述。
真实 Claude Code / Codex 进程用自带 shell 工具提交，身份来自 jiuwenswarm 的 `SwarmBuildContext` 和
`member_environment`，并压过宿主进程继承的 `GIT_*` 变量；开关关闭时回落到宿主身份；去掉本次修复后，
开关打开也只能得到宿主身份。本机 codex 全局配置的默认模型 `gpt-6.2-sol` 在 ChatGPT 账号下不可用，
因此 codex 组显式指定了 `gpt-5.6-sol`，与身份注入无关。

## 验证结论

- 已证明范围：runtime 合并语义、fail closed、装配点派生成员 BuildContext 并注入团队后端；真实 Claude Code、
  Codex 作为外部 Leader 和作为 spawn 出来的外部 teammate 提交时，author / committer 都跟随会话开关，
  且各自使用成员名（`team_leader`、`dev-1`），修复前后对照成立。
- 未覆盖：经 jiuwenswarm Web 入口打开开关的完整链路；子进程模式（`spawn_mode="process"`）下的外部成员。
- 可支持的交付判断：core 侧契约满足，真实 CLI 行为已证实；swarm 把 core `rev` 升到包含本改动的提交后即可生效。

### 覆盖与资料同步

已同步 S_27（不变量 20、接口契约、修订日期、关联 feature）、`openjiuwen/agent_teams/harness/AGENTS.md`
「成员工具环境」、F_20261104_1720「边界」。

## 已知遗留

| 遗留项 | 未处理原因 / 性质 | 影响与现有处置 | 后续入口 / 条件及所需验证 |
| --- | --- | --- | --- |
| 策略变化从下一次 `start` 生效 | 有意取舍：provider 进程环境在启动时固定 | 会话中途切换开关后，已运行的外部成员仍沿用旧身份，直到被停止或重启 | 需要即时生效时，平台在设置变化后重启外部成员 |
| 删除项（null）无法下发 | `HarnessContext.env` 只能表达字符串值 | 被删除的继承变量仍然存在，运行时打 warning | 需要时扩展协议，支持 provider 侧删除 |
| swarmflow workflow 成员未覆盖 | 范围外：`build_cli_workflow_runtime` 没有成员 BuildContext 与团队后端 | workflow 里的三方 CLI 仍用宿主身份 | 给 workflow 成员装配传入 resolver 后，复用 `bind_execution_environment` |
| 适配器型 backend（gemini / openclaw / hermes / generic）未覆盖 | 范围外：走 `CliRuntimeBase` 子进程，不是 harness provider | 同上 | 在 `CliRuntimeBase` 启动时加同类注入 |
