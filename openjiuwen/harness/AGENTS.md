# Harness

`openjiuwen.harness` 编码智能体框架。构建在 core 原语（`ReActAgent` / `AgentRail` / `Runner` /
session）之上，为编码场景提供**深度代理**：模型调用、工具执行、rails 行为约束、子代理、
任务循环、工作空间、权限引擎的完整闭环。

本文件是本模块的**入口索引**。深入细节时跳转到：
- `rails/AGENTS.md` — rail 三层基类、回调双命名空间路由、priority 梯队
- `docs/AGENTS.md` — 设计归档结构与命名规约（specs / features / 方法论）
- `docs/specs/S_*` — 各子系统的设计规约（跨子模块契约、协议、边界、不变量）

## 公开入口（public API）

公开符号仅限 `__init__.py` 导出。`__all__` 是**完整集合**（8 个符号），导出使用懒加载
`__getattr__`——重模块按需 import，避免启动即拉全量。

| 入口 | 用途 |
|---|---|
| `create_deep_agent(model, *, card, tool_owner_id, system_prompt, tools, mcps, subagents, rails, enable_task_loop, ...)` | 从零组装 `DeepAgent` 的**唯一**公共构造路径。纯装配 + 配置回填，rails 排队后 async 注册（首次 `invoke()` 生效）；`**config_kwargs` 透传进 `DeepAgentConfig` |
| `DeepAgent` | 深度代理主体：`invoke()` / `stream()` / `follow_up()` / `steer()` / `abort()` / `run_one_round()` + task-loop 单轮入口；`load_plugin*` / `load_agent_template*` / `load_harness_config` 扩展加载；`save_state()` / `load_state()` / `switch_mode()` 会话状态 |
| `DeepAgentConfig` / `AudioModelConfig` / `VisionModelConfig` | 配置模型。`configure(config)` 支持热重载（`_hot_reload_rails` / `_hot_reload_model` / `_hot_reload_tools` / `_hot_reload_system_prompt`） |
| `TaskLoopEventHandler` / `TaskLoopEventExecutor` | 任务循环的事件消费侧：`event_handler` 把事件路由进队列 / 执行器驱动器 |
| `Workspace` | 工作空间抽象（`workspace/workspace.py` + `directory_builder.py`），限制代理读写范围 |

**新增配置项一律走 `DeepAgentConfig` / Spec 拆分**，不是给 `create_deep_agent` 堆参数——
扩参数列表永远是 hack，扩 config/装配才是设计（`S_01_public-api-and-construction`）。

## 模块地图

```
harness/
├── __init__.py            # 公开 API 聚合导出（懒加载 __getattr__）
├── deep_agent.py          # DeepAgent 主类：ReAct 外层 + rails 注册 + task loop + 状态/模式
├── factory.py             # create_deep_agent 装配（DeepAgentParts / resolve / apply）
├── extension_binder.py    # 扩展绑定：plugin / agent-template → BuildContext（S_13）
├── subagent_lifecycle.py  # 子代理生命周期（S_18）
├── image_modality_probe.py# 图像模态探测（多模态开关）
├── cli/                   # 命令行表面：agent/ prompts/ rails/ storage/ ui/ + __main__
├── docs/                  # 设计归档：specs/ + features/ + spec-design-methodology.md
├── goal/                  # 目标与评估：manager / evaluation / schema / store（S_11）
├── kv_cache/              # KV 缓存 + 日志 hooks（S_16）
├── lsp/                   # LSP 集成：core/ servers/ types.py（S_14）
├── manifest/              # 声明式装配：catalog / registration / @harness_element（S_12）
├── personal_context/      # 个人化上下文：fetch/ + context_graph / source_metadata（S_17）
├── prompts/               # 提示词构建：builder / sections/ / tools/ / l10n（S_06）
├── rails/                 # 行为约束层：三层基类 + 事件路由 + priority（见 rails/AGENTS.md，S_04）
├── resources/             # 内置资源：builtin_rules.yaml / extension_loader（S_13）
├── schema/                # 数据模型：config / state / task / stop_condition / interaction（S_07）
├── security/              # 工具权限引擎：tiered_policy / file_guard / shell_ast（S_08）
├── skills/                # skill 库状态（library_state）（S_16）
├── subagent_runtime/      # 子代理运行时：registry / instance / control / persistence（S_10）
├── subagents/             # 内置子代理：browser / code / explore / plan / research / verification（S_18）
├── task_loop/             # 任务循环：controller / event_handler / event_executor（S_03）
├── tools/                 # 工具集合：shell / web / worktree / subagent / skills ...（S_05）
└── workspace/             # 工作空间：workspace.py / directory_builder.py（S_09）
```

### 与 spec 的对应关系

每份 spec 锚定一个子系统，构成"一个子系统一份规约"的切分：

| 子系统 | 核心文件 | spec |
|---|---|---|
| 公开 API / 构造 | `__init__.py` · `factory.py` · `schema/config.py` | `S_01` |
| DeepAgent 架构 | `deep_agent.py` · `schema/interaction.py` | `S_02` |
| 任务循环 | `task_loop/` | `S_03` |
| Rails 契约 | `rails/`（基类、事件路由、priority） | `S_04` |
| 工具契约 | `tools/`（注册 / 描述 / lifecycle） | `S_05` |
| 提示词与 i18n | `prompts/` | `S_06` |
| 数据模型 | `schema/`（state / loop_event / task） | `S_07` |
| 安全引擎 | `security/` + `rails/security/` | `S_08` |
| 工作空间 | `workspace/` | `S_09` |
| 子代理运行时 | `subagent_runtime/` | `S_10` |
| 目标与评估 | `goal/` | `S_11` |
| Manifest 声明式装配 | `manifest/` | `S_12` |
| 资源 / 扩展包 | `resources/` + `extension_binder.py` | `S_13` |
| LSP 集成 | `lsp/` | `S_14` |
| CLI 与配置 | `cli/` | `S_15` |
| KV 缓存 / skill 状态 | `kv_cache/` + `skills/` | `S_16` |
| 个人上下文 | `personal_context/` | `S_17` |
| 子代理与生命周期 | `subagents/` + `subagent_lifecycle.py` | `S_18` |

## 架构铁律

1. **装配路径唯一**：构造只走 `create_deep_agent` + `DeepAgent.configure()`。
   `factory.resolve_deep_agent_parts` 纯装配、`apply_deep_agent_parts` 落运行时；rails 一律排队
   async 注册（`_queue_pending_rails`），**首次 `invoke()` 前全部生效**。绕过装配直接 new
   `DeepAgent(card=...)` 只适合低层测试。

2. **Rail 事件路由必须进集合**：`DeepAgent._register_rail_selective` 按事件把 rail 回调路由到
   内层 `ReActAgent`（`_BRIDGE_EVENTS`）或外层 `DeepAgent`（`_OUTER_ONLY_EVENTS` /
   `_DEEP_EVENTS`）。**新增 `AgentCallbackEvent` 成员不放进某个集合 = rail 静默失效**（只有一条
   logger.warning，回调一次不跑）。兜底警告存在但不可依赖——判据是"这个事件谁 `ctx.fire` 的"。
   详规在 `rails/AGENTS.md`，由
   `tests/unit_tests/harness/test_deep_agent_rail_event_routing.py` 强制。

3. **工具必须过权限引擎**：工具描述 / 注册归 `tools/`，执行安全归 `security/`（tiered policy、
   file_guard、shell AST 校验）。`PermissionInterruptRail` 拦截全量工具、`BaseSecurityRail` 多事件
   安全检查。安全 rail 忘了声明 `supported_events` 的子类一个回调都不会注册——这是坑不是特性。

4. **扩展走 Manifest 声明式装配**：`@harness_element` 注册的元素（rail / tool / prompt section /
   subagent）由 `manifest/` 装配，扩展加载（`load_plugin` / `load_agent_template` /
   `load_harness_config`）一律生成 `BuildContext` 后进同一条 apply 路径。热重载是状态快照
   diff（`DeepAgentConfig` 换新实例），不是原地改字段。

5. **懒加载 + 无循环 import**：`__init__.py` 用 `__getattr__` 懒加载；`deep_agent.py` 对
   task-loop / subagent 相关模块同理。新增跨包 import 前先查 `rails/AGENTS.md`「已知的坑」——
   `evolution/review/__init__.py` 的 `__getattr__` 懒加载必须保留（review subagent 会 import
   tool registry，tool registry 又 import rails 包，直接 import 成环）。

## 测试

- 单测镜像路径：`tests/unit_tests/harness/`，按子系统分子目录（`rails/`、`prompts/`、`tools/`…）。
- 分层：`level0`（happy path，PR gate） / `level1`（错误路径、生命周期边界）。
- rail 行为契约测试必须存在（如 `test_deep_agent_rail_event_routing.py`）——它锁的是"文档里
  写了但编译器拦不住"的静默失效。

## 代码风格

- PEP 585 内置泛型（`list[X]` / `dict[K, V]`）与 PEP 604 联合类型（`X | Y`），禁止从 `typing`
  导入 `List` / `Dict` / `Optional` 等别名；`Callable` / `Any` / `TYPE_CHECKING` 等仍从 `typing`。
- 配置模型字段有默认值；枚举 / 常量命名大小写按模块既有约定，变更前先看相邻文件。

## 设计文档归档与双向同步（仅本模块强制）

前置铁律——**文档先是设计的输入，才是提交的产出**。动手改任何代码设计**之前**，先 `grep` +
读相关的 `docs/specs/S_NN_*`（现有契约 / 不变量 / 边界）与 `docs/features/F_NN_*`（为什么长
这样、当初拒绝了哪些方案），拿它们校准方案；方案定了**当场**同步改掉对应 S / F。先码后档、
拖到提交前才补文档是把流程做反了。

三条强制约束，提交时必查：

1. **每次特性更新必须归档 feature 文档，且特性代码、测试代码、文档拆成三个连续提交**：在
   `docs/features/` 下新增 `F_NN_<slug>.md`，记录决策、拒绝的方案、验证基线、已知遗留。落地
   顺序固定为三个紧邻的提交——提交 1 特性代码（`feat(harness): ...` 或对应 type），提交 2 单测
   （`test(harness): ...`），提交 3 文档（`docs(harness): ...`，含 `F_NN_*.md` 新增 +
   `S_NN_*.md` 修订）。
2. **所有模块设计规约变动必须更新 specs 文档**：模块契约、跨子模块的公共协议、不变量、公共
   API 形态变化时同步修订 `docs/specs/S_NN_<slug>.md`；新规约 = 新 spec 文件（编号 = 目录内
   现有最大号 + 1）。
3. **双向同步：读到与代码不一致的描述必须当场修文档**。在本模块任何一份 `AGENTS.md` /
   `docs/specs/S_*` / `docs/features/F_*` 里读到的接口名、枚举值、truth table 行数、文件路径、
   不变量只要与当前代码不符，**不要**把过时表述当作新约束执行、也不要原样转述给用户；先
   `grep` 代码、以代码为准刷新文档，在同一次改动里落地。`AGENTS.md` 里每条点名了"X 个分支 /
   Y 路 dispatch / Z 方法"的句子都是契约的一部分。**更新目标是 `AGENTS.md`，不是 `CLAUDE.md`**。

收尾规范：

- spec 头部"最近一次修订日期"字段在每次修订时填当天日期（`YYYY-MM-DD`）；feature 文档用头部
  "日期"字段记录归档当天；**不要在元信息里写 commit hash**。
- 子模块自身的本地约定放各 `<subdir>/AGENTS.md`（现在只有 `rails/` 有，新增时按需补）；跨子模块
  的设计规约一律落 `docs/specs/`，不塞进单一子目录的 AGENTS.md。
- **`CLAUDE.md` 是只读壳，不要编辑它**：`CLAUDE.md` 仅含 `@AGENTS.md` 一行，编辑它的修改不会
  保留在任何有效文档里。需要更新文档时，直接编辑 `AGENTS.md`。
- 拿不准某次改动算 feature-grade（要 `F_NN_*.md`）还是普通修复（不必归档）时，先问用户。歧义
  情况默认**归档**。

## 提交约定

本模块改动的 commit message scope 固定用 `harness`（如 `feat(harness): ...`，
`docs(harness): ...`）。

footer 用 `Refs: #<issue>` 格式关联 issue。issue 号若无法从当前上下文明确，必须先询问用户，
不要臆造或留空。

涉及 `docs/features/F_*` / `docs/specs/S_*` 文档更新的特性改动，**特性代码、测试代码、文档拆
成三个连续提交**（`feat(harness)` → `test(harness)` → `docs(harness)`），细则见上文「设计文档
归档与双向同步」约束 #1。
