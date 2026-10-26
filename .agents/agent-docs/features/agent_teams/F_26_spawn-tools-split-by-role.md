# Split `spawn_member` into Four Role-Specific Tools

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-01 |
| 范围 | `tools/team_tools.py`、`tools/locales/{cn,en}.py`、`tools/locales/descs/{cn,en}/`、`agent/agent_configurator.py`、`prompts/sections.py`、`prompts/{cn,en}/*.md`、`tests/unit_tests/agent_teams/` |
| 测试基线 | `pytest -m level0 tests/unit_tests/agent_teams/` → 594 passed, 2 skipped；`test_team_tools.py` / `tools/test_bridge_spawn_tool.py` / `test_team_agent_tools.py` 全绿 |
| Refs | `#751` |

## 背景

旧 `spawn_member` 是单一工具，把 10 个参数平铺在 schema 第一层，但这些参数分属四个**互斥**的
`role_type`（`teammate` / `human_agent` / `bridge_agent` / `external_cli`）：

| 参数 | teammate | human_agent | bridge_agent | external_cli |
|---|---|---|---|---|
| member_name / display_name / desc | ✓ | ✓ | ✓ | ✓ |
| prompt / model_name | ✓ | 运行时拒绝 | ✓ | 忽略 |
| cli_agent | 忽略 | 忽略 | 忽略 | 必填 |
| mailbox_inject_mode / protocol / adapter_config | 忽略 | 忽略 | ✓ | 忽略 |

问题：(1) LLM 无法从 schema 判断哪些参数组合合法，只能试错（选 teammate 填 cli_agent、选
human_agent 填 model_name 会被运行时拒绝）；(2) `invoke` 内堆着 role_type 分支
（`_spawn_bridge` / `_spawn_external_cli` / human_agent / teammate），是典型"特殊情况"坏味道；
(3) 每加一个 role_type，第一层参数继续膨胀。底层数据结构其实已经做对了
（`TeamMemberSpec` 基类 + `BridgeMemberSpec` 子类、`ExternalCliAgentSpec`，backend 四个方法
`spawn_member` / `spawn_human_agent` / `spawn_bridge_agent` / `spawn_external_cli_agent` 早已分好），
只是工具层没对齐。

## 决策

按 `role_type` 拆成四个独立工具，命名与 role_type 值对齐：

- `spawn_teammate`(member_name, display_name, desc, prompt?, model_name?)
- `spawn_human_agent`(member_name, display_name, desc) —— schema **不含** model_name / prompt
- `spawn_bridge_agent`(member_name, display_name, desc, mailbox_inject_mode?, protocol?, adapter_config?, model_name?)
- `spawn_external_cli`(member_name, display_name, desc, cli_agent)

实现要点：

1. **共享基类 `_SpawnToolBase(TeamTool, ABC)`**（`team_tools.py`）封装公共逻辑：member_name 校验
   （复用 `_MEMBER_NAME_PATTERN`）、persona fallback（`desc or prompt`）、ToolOutput 构造
   （`_ok` / `_fail` / `_from_result`）、`map_result`。四个子类各自只声明扁平 schema + 单一路径
   `invoke`，**基类内零 role 分支**。`SpawnMemberTool` 整类删除。
2. **能力门控动态注册**（`create_team_tools`）：与既有 plan_mode / persistent 门控同款写法，按
   `hitt_enabled()` / `bridge_enabled()` / `external_cli_kinds()` 决定是否注册
   `spawn_human_agent` / `spawn_bridge_agent` / `spawn_external_cli`；`spawn_teammate` 始终注册。
   能力关闭的工具**根本不向 LLM 注册**——看不到就不会误调，`invoke` 内同名检查降为运行时降级兜底。
   `LEADER_ONLY_TOOLS` 用四个新名替换 `spawn_member`。
3. **predefined 模式门控**（`agent_configurator.py`）：predefined 团队从 leader 工具集 exclude
   四个 `spawn_*`（原先只 exclude `spawn_member`）。
4. **i18n / 系统提示词**：locale 参数 key 与 `descs/<lang>/*.md` 按四工具拆分；leader prompts
   模板（`leader_workflow*.md` / `leader_policy.md` / `team_plan_mode.md` / `sections.py` HITT 段）
   把 `spawn_member` 更新为对应新工具名。

## 拒绝的方案

- **保留 `spawn_member` 兼容别名**：拒绝。这是 LLM-facing 工具、非 `__init__.py` public API；保留别名
  会让"10 参数平铺 + role 分支"的坏味道复活，且 LLM 同时看到 5 个工具更迷惑。
- **单工具 + discriminated union（oneOf + role_type discriminator）**：拒绝。框架透传支持 oneOf，
  但模型对 oneOf 的解析不稳定，且 `invoke` 仍需按 role dispatch；独立工具是 100% 可靠的扁平 schema。
- **引入 tool search / deferred loading 或 `$ref` 跨工具去重**：拒绝。leader 仅 ~12 个工具，远未到
  tool-search 甜区；Anthropic/OpenAI 的 tool `input_schema` 必须自包含，`$ref` 不能跨工具复用。
  公共参数（member_name 等）的描述重复仅几百 token，不值得引入复杂度——context 优化靠文案分层即可。

## 反转 F_22 的"拒绝独立工具"决策

F_22 当时基于"复用 spawn_member + role_type、外部 CLI 团队语义即 teammate"明确**拒绝**新建独立
`spawn_external_cli` 工具。本特性反转该决策，理由是 F_22 当时未充分权衡 **LLM 正确调用工具的负担**：
随着 role_type 增多，单工具的互斥参数平铺让模型选择面臃肿、易错。拆分后每个工具 schema 扁平、无非法
组合，调用正确率显著优于"省下的几百 token schema 重复"。F_07（bridge）的动态 spawn 同步迁移到
`spawn_bridge_agent`。F_07 / F_22 顶部已加指向本特性的变更注记。

## 验证

```bash
source .venv/bin/activate && export PYTHONPATH=.:$PYTHONPATH
make test TESTFLAGS="tests/unit_tests/agent_teams/test_team_tools.py tests/unit_tests/agent_teams/tools/test_bridge_spawn_tool.py"
make test TESTFLAGS="-m level0 tests/unit_tests/agent_teams/"   # 594 passed, 2 skipped
```

测试覆盖：四工具各自的 happy path + member_name 校验 + 能力门控未注册场景
（`TestSpawnTools` / `TestSpawnToolCapabilityGate` in `test_team_tools.py`，bridge 在
`tools/test_bridge_spawn_tool.py`）。

## 已知遗留

- 部分**历史**设计文档（`docs/specs/S_05` / `S_07` / `S_12`、`docs/features/F_04` / `F_08` / `F_13`
  / `F_15` / `F_17` / `F_20` / `F_21`、`architecture_cn.md` 的散落叙述）仍含 `spawn_member` 字样。
  其中作为 **backend 方法名**（`TeamBackend.spawn_member` / `TeamAgent.spawn_member` 仍存在）的引用是
  正确的、保留；作为**工具名**的历史叙述属术语级遗留，活契约以 `S_08` 与本文件为准，后续读到时按双向
  同步约束顺手刷新。
- 源码层文案去重（locale include/partial 让 member_name 规则等公共片段共享一份源）未做，优先级低
  （只省维护、不省运行时 context）。
