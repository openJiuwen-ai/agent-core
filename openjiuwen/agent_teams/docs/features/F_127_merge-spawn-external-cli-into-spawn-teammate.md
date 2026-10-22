# `spawn_external_cli` 并入 `spawn_teammate`

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-22 |
| 范围 | `tools/tool_member.py`（`SpawnTeammateTool` 增加外部 CLI 分支，删除 `SpawnExternalCliTool`）、`tools/tool_factory.py` / `tool_permissions.py` / `team_tools.py`（去掉 `spawn_external_cli` 的构造、门控、权限与重导出）、`agent/agent_configurator.py`（predefined 排除列表）、`tools/locales/`（`spawn_teammate.*` 新增外部 CLI 参数文案，删除 `spawn_external_cli.*` 与 `spawn_expert.external_cli.fallback_model_name` / `effort`；新片段 `external_cli_usage`，`builtin_model_*` 片段改写为嵌套字段名；删除 `descs/*/member/spawn_external_cli.md`） |
| 关联 spec | S_05、S_08、S_27 |
| 测试基线 | `tests/unit_tests/agent_teams` + `tests/unit_tests/harness/test_team_skill_create_rail.py` 全量 3952 passed / 36 skipped / 3 xfailed |
| Refs | #1435 |

## 背景

外部 CLI 成员（claudecode / codex / 三方 harness provider）对 leader 来说和本地 LLM 队友是同一种成员：
都有 `member_name` / `display_name` / `desc` / `prompt`，都进同一份名册、按同一套任务与消息协议工作，
区别只在运行时。工具却按"怎么运行"拆成了 `spawn_teammate` 与 `spawn_external_cli` 两个，带来三个问题：

- leader 选工具时先要回答一个与任务无关的问题（这个成员跑在哪），两份描述大段重复；
- 同一件事已经有第三种形态：调度模式 `create_task` 的内联 `member`（F_125 决策 11）用 `cli_agent` +
  `external_cli` 对象在同一个成员对象里表达两种运行时，`spawn_expert` 也用 `external_cli` 对象——只有
  `spawn_external_cli` 把模型参数平铺在顶层；
- `desc` 在 `spawn_external_cli` 可选、在其余两处必填，同一个公开名册字段的要求不一致。

## 决策

1. **按"成员是什么"拆工具，不按"怎么运行"拆**。spawn 工具一种成员一个：AI 队友（`spawn_teammate`，本地
   LLM 或第三方 CLI）/ 人类（`spawn_human_agent` / `spawn_passive_human`）/ bridge（`spawn_bridge_agent`）。
   `spawn_expert` 是"按模板创建 AI 队友"，不受影响。S_08 不变量 12 与 `tool_factory` 的注释同步改写。
2. **字段结构与内联 `member` 一致**。`spawn_teammate` 增加两个可选属性：`cli_agent`（含义同原
   `spawn_external_cli.cli_agent`）与 `external_cli` 对象（属性由 `tool_member.external_cli_option_properties`
   生成：`fallback_model_name`，有内置模型目录时再加 `builtin_model` / `effort`）。`model_name` 仍在顶层：
   外部 CLI 成员上它是与该 CLI 协议兼容的团队模型池端点，门控打开时其描述追加这条规则。
3. **门控是属性级的**，信号 `external_cli_kinds()` 非空（与内联成员同源）：同时门控两个属性、`model_name`
   描述的追加句与描述槽 `{{external_cli_usage}}`。`builtin_models_enabled()` 照旧门控
   `external_cli.builtin_model` / `effort` 与 `{{builtin_model_param_rows}}` / `{{builtin_model_usage}}`；内置模型目录
   来自已声明的 CLI 类型，所以两层门控天然嵌套。`spawn_teammate` 照旧始终注册；`set_member_model` 的门控不变。
4. **写库前的校验**：门关时偷传 `cli_agent` / `external_cli` → 报错；`external_cli` 不是对象、或非空却没有
   `cli_agent` → 报错（与 `_check_inline_external_cli` 同义）；`cli_agent` 与本地专属字段（`isolation` /
   `permissions` / `fork` / `fork_source` / `fork_mode`，判 `is not None`，同 `spawn_expert._spawn_external`）
   同时出现 → 报错。全部早于任何写库，被拒的调用不留成员行。
5. **登记不新写逻辑**：外部 CLI 分支一律 `tool_member.register_external_cli_member(tool_name="spawn_teammate")`
   （声明检查 + 模型规则 + 登记，与 `spawn_expert` / 内联成员同一实现），本地分支照旧 `register_teammate`。
   结果 `role_type` 分别为 `external_cli`（带 `cli_agent`）/ `teammate`，`render_for_llm` 不变。
6. **`desc` 对外部 CLI 成员也必填，`prompt` 两种成员都可选**——`required` 仍是
   `member_name` / `display_name` / `desc`，与内联 `member` 一致。
7. **描述合并**：`spawn_teammate.md` 开头改为中性的"AI 队友"；外部 CLI 的运行方式、模型 / 回退选择规则、
   与本地专属字段互斥、`cli_agent` / `external_cli.fallback_model_name` 两行参数表全部收进片段
   `external_cli_usage`（节级 capability 槽，本身以参数表结尾）；`{{builtin_model_param_rows}}` 紧跟其后、
   续接同一张表，门关时两者一起消失，不会在表格中间留下空行。回退模型与推理强度的参数文案改由
   `spawn_teammate.external_cli.fallback_model_name` / `.effort` 统一承载，`spawn_expert` 与内联成员经同一个
   `external_cli_option_properties` 读取；其余文案里"同 `spawn_external_cli`"的说法都改指 `spawn_teammate`。

## 拒绝的方案

- **保留 `spawn_external_cli` 作为兼容别名**：合并的目的就是 leader 只看到一个入口，别名会让工具列表里继续出现
  两个；而唯一的调用方是 leader 的 LLM——MCP 的 member / operator scope 都不暴露它，swarm 也没有导入这个类。
  旧会话历史里的 `spawn_external_cli` 调用照常回放，恢复后的 leader 拿到的是新工具集。因此连同
  `SpawnExternalCliTool` 与 `team_tools.py` 的重导出一起删除。
- **把 `_check_inline_external_cli` 抽成共用函数**：内联成员的检查和 `expert` 维度纠缠在一起（外部运行时专家
  可以不带 `cli_agent` 而带 `external_cli`），硬抽反而更绕；两处一致性由测试守住。
- **模型参数平铺在顶层（沿用 `spawn_external_cli` 的形态）**：与内联 `member`、`spawn_expert` 的 `external_cli`
  对象不一致，且会让本地队友的 schema 里出现一堆只对外部 CLI 有效的属性。

## 验证

- `test_team_tools.py`：原 `spawn_external_cli` 段改写为 `spawn_teammate(cli_agent=...)`；新增门控（无声明
  类型时属性与描述都不出现、偷传在写库前拒掉）、本地专属字段互斥（5 个参数化）、`external_cli` 形态校验；
  工厂断言任何配置下都没有 `spawn_external_cli`、声明外部 CLI 时 `spawn_teammate` 带 `cli_agent`。
- `test_builtin_model_selection.py`：内置模型改走 `external_cli.builtin_model` / `effort`，嵌套属性随目录门控。
- `test_tool_expert.py`：外部运行时专家与内联成员的模型规则一致性对照改为 `spawn_teammate`；新增
  `spawn_teammate` 与内联 `member` 的 `cli_agent` / `external_cli` 结构一致性用例。

## 已知遗留

- jiuwenswarm 侧已在单独的提交里清理（分支 `refactor/drop-spawn-external-cli`）：`_ROSTER_MUTATING_TOOLS` 去掉
  `spawn_external_cli`（`spawn_teammate` 本就在名册刷新集合里），`common/config.py` 的 docstring 改指
  `spawn_teammate.external_cli`。聊天面板的工具分类（`toolCategory.ts`）有意保留旧名，让旧会话里的调用照常显示为
  "创建成员"。`group_service.py` 调用的是后端 `spawn_external_cli_agent`，不受影响。
- `agent_evolving/signal/skill_creation.py` 与 `harness/rails/skills/team_skill_create_rail.py` 扫描的是历史轨迹，
  保留 `spawn_external_cli` 以识别旧会话（与其中更早的 `spawn_member` 同理）。
- 工作区里演进过的工具描述（`prompts/tool/spawn_external_cli.md`）不再被读取，成为孤儿文件，无害。
