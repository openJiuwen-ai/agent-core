# Team 专家目录与按模板拉起专家成员

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-20 |
| 范围 | `experts/`（新：`catalog.py` / `agent_group.py` / `template_member.py` / `bound_group.py`）、`tools/tool_expert.py`（新）、`tools/tool_member.py`（外部 CLI 模型规则抽成共用实现、`register_teammate(agent_template=)`）、`tools/tool_factory.py` / `tool_permissions.py`（`EXPERT_TOOLS` 门控）、`tools/team.py`（`spawn_member(agent_template=)`、`spawn_external_cli_agent(member_config=, agent_template=, lifetime=)`、`delete_unstarted_member` 清 CLI 类型映射）、`tools/tool_task.py`（调度模式内联成员的 `expert` / `group` / `cli_agent` / `external_cli`）、`tools/member_options.py`（`MemberAgentTemplate`）、`schema/blueprint.py`（`TeamAgentSpec.agent_group_name`）、`schema/team.py`（`TeamRuntimeContext.agent_template_spec` / `template_external_cli`）、`agent/agent_configurator.py`（`resolve_agent_spec` 快照分支、leader 注入目录）、`agent/spawn_manager.py`（冷恢复读回）、`spawn/external_cli_spawn.py`（成员行配置来源）、`external/manifest.py`（`merge_external_cli_config`）、`rails/team_context.py` / `rails/team_tool_rail.py` / `rails/elements.py`、`runtime/manager.py`（`set_expert_catalog`）、`tools/locales/`（`descs/*/expert/` + 参数文案）、`__init__.py` |
| 关联 spec | S_05、S_08、S_12、S_27；harness S_13 |
| 关联设计 | swarm-design-docs SDD-0032；harness `F_22_agent-template-card-and-async-loader.md` |
| 测试基线 | 新增 `tests/unit_tests/agent_teams/experts/`（40）与 `tools/test_tool_expert.py`（40）全部通过；`tests/unit_tests/agent_teams` + `tests/unit_tests/harness` + `tests/unit_tests/harness_providers` 全量 10720 passed / 126 skipped / 3 xfailed；决策 11（内联外部 CLI 成员）落地后 `tests/unit_tests/agent_teams` 3886 passed / 36 skipped / 3 xfailed |
| Refs | 待定（提交前补） |

## 背景

用户在 Team 里说「拉一个理财专家进来」时 leader 做不到：它既看不到本机装了哪些专家（单个专家
`agent_template` 包、专家团 `agent_group` 包），也没有按专家定义拉起成员的工具——`spawn_teammate`
只能现写 `desc` / `prompt`，拉起的成员共用 `teammate` 基线，带不上专家的人设和能力。专家能力
此前只有一条生效路径：宿主在建队前把绑定的专家团写成预定义成员，运行中无法再拉起，也无法给
同一个专家多开几个实例。

另外两件事挡着"core 定义工具、宿主只做定位"：

- 专家团的包格式与加载器只存在于 jiuwenswarm（`agents/swarm/agent_group.py`），core 拉起团内成员
  时得请宿主代为加载；
- "模板 → Team 成员"（人设渲染 + 能力快照 + 技能并集）只在 jiuwenswarm 的私有函数里，core 再写一份
  就会让同一个专家作为预定义成员和动态成员由两份代码组装。

## 决策

1. **宿主只定位，core 解析**。新协议 `ExpertCatalog`（`experts/catalog.py`）只有四个 `async` 方法：
   列出 / 定位已安装的单个专家包和专家团包，返回 `PackageLocation(name, path, source)`，不读 manifest。
   manifest 全部由 core 解析：单个专家的卡片与加载器在 harness（F_22），专家团在 `experts/agent_group.py`。
2. **专家团包格式搬进 core，规则逐行保留**。`load_agent_group_package(_bundle)` /
   `LoadedAgentGroupPackage` / 成员改写规则（id 改目录名、追加 `agent_group_instruction`、合并共享技能、
   leader `AGENT.md`）原样搬入，保持同步实现；新增只读 manifest 的 `load_agent_group_card`（成员按 `agents`
   顺序、成员名取目录名），以及在工作线程里跑它们的异步入口 `aload_agent_group_package` /
   `read_agent_group_card`。`capabilities` 的回退规则提成纯函数 `group_capabilities`。
3. **"模板 → 成员"只有一份**（`experts/template_member.py`）：`render_template_persona`（按 priority 排序、
   按语言选文、渲染占位符、逐段 `strip()` 去空段——jiuwenswarm 现有口径，底层用 harness 的
   `render_prompt_content`）、`template_snapshot`、`apply_template_snapshot`、`compose_template_member`。
   预定义成员一次调用 `compose_template_member`；动态成员在登记时写 persona 与快照、构建时
   `apply_template_snapshot`，两条路径每一步都是同一个函数。动态成员的人设按 teammate 基线 spec 的语言渲染（`ExpertBinding.persona_language`），
   与 `compose_template_member` 取的语言相同，而不是 leader 工具的语言。
4. **三个 leader 工具，经 `TeamToolRail` / `create_team_tools` 挂载**：`view_experts`、`view_expert_groups`
   （`get` 带成员表，可选 `member` 只展开一个成员）、`spawn_expert`。三者同属 `EXPERT_TOOLS`、进入
   `LEADER_ONLY_TOOLS`，**只按一个信号门控**：宿主注入了目录才构造、才不被减掉，三者同生同灭。
   查看工具只读卡片（读不出的包跳过并记 warning），只有 `spawn_expert` 调用完整加载器。成员表的 `spawnable`：
   native 成员恒为真；外部运行时成员要求其 provider 映射出的 CLI 类型（`cli_agent_for_provider`，与
   `external_cli_agent_spec_from_template` 同一张映射）已在本 Team 声明——与 `spawn_expert` 的声明检查一致。
5. **`spawn_expert` 只登记成员行**（`UNSTARTED`），拉起仍走 `autostart_unstarted`。成员行：
   `prompt = 人设 + 补充 prompt`、`desc = 模板描述 + 补充 desc`（只追加不覆盖）、`display_name` 缺省取
   卡片显示名、与名册重名时追加「（member_name）」，`options.agent_template = {ref, version, snapshot}`。
   之后的构建、子进程拉起、冷恢复只读成员行：`build_context_from_db` 把快照读进
   `TeamRuntimeContext.agent_template_spec`，`resolve_agent_spec` 在成员没有自己的 `agents[成员名]`
   时对 `teammate` 基线 `apply_template_snapshot`。
6. **绑定团多实例从本 Team 的 spec 取模板**。`TeamAgentSpec.agent_group_name` 成为正式字段；
   `group` 等于它时，模板取 `agents[expert].agent_template_spec` 与预定义成员的 `prompt` / `display_name` /
   `desc`（`experts/bound_group.py`），不调用目录、不读磁盘，与预定义成员逐字节同源。
7. **外部运行时专家与 `spawn_external_cli` 同一套规则**。模板侧配置复用
   `external_cli_agent_spec_from_template`（绑定团取预定义成员的 `external_cli`）；CLI 类型必须已在
   `external_cli_agents` 声明（`check_external_cli_declared`，与 `spawn_external_cli` 共用）；模型 / 认证回退 /
   内置模型校验抽成 `ExternalCliModelRules`，两个工具调用同一实现、同一报错（报错里的工具名不同）；
   启动配置 `merge_external_cli_config(声明配置, 模板配置)`：以声明配置为底，叠加模板的技能（追加在声明
   技能之后）、`skill_conflict` 与非默认的 `runtime.config` 键。`spawn_external_cli_agent` 新增
   `member_config` 把合并结果写进 `options.agent_template.external_cli`；`_external_cli_config_for_member`
   的查找顺序变为：预定义外部成员 → 成员行配置（`TeamRuntimeContext.template_external_cli`）→ 声明配置。
   只对外部 CLI 有意义的 `fallback_model_name` / `builtin_model` / `effort` 收在可选对象 `external_cli` 里，
   `builtin_model` / `effort` 属性与 `spawn_external_cli` 同一门控（`builtin_models_enabled()`）；本地专家
   传入 `external_cli` 报错。
8. **注入路径**：宿主调 `TeamRuntimeManager.set_expert_catalog(catalog)`（静态方法）→ 进程级持有者
   （`experts.catalog.set_process_expert_catalog`）→ `AgentConfigurator.setup_agent` 仅为 leader 读取，连同
   `ExpertBinding(bound_group, persona_workspace)` 经 `inject_team_handles` 写进
   `TeamHandleKey.EXPERT_CATALOG` / `EXPERT_BINDING` → `build_team_tool_rail` → `TeamToolRail` →
   `create_team_tools(expert_catalog=, expert_binding=)`。目录对象不挂在 `TeamAgentSpec` 上（spec 必须可序列化）。
9. **不在事件循环上读盘**：卡片读取与两个异步加载器经 `asyncio.to_thread` 在工作线程里跑同步实现（整个
   加载只有一次线程往返，比逐操作走 `aiofiles` 快一个数量级，见 harness F_22）；工具路径上事件循环线程不做
   文件 IO。定位协议的实现由宿主决定，同样不得阻塞事件循环。
10. **调度模式 `create_task` 的内联成员也能按专家创建**。注入了目录时，内联 `member` 对象多出可选
    `expert` / `group`（与 `EXPERT_TOOLS` 同一门控，未注入时 schema 不出现、偷传在写库前报错）；给了 `expert`
    时 `display_name` / `desc` 可省略，`desc` / `prompt` 只追加，成员行同样写 `options.agent_template`。模板解析、
    人设渲染、显示名去重（同一次调用内拉起的同类成员也参与去重）与 `spawn_expert` 共用 `ExpertTemplateResolver` /
    `expert_member_texts` / `expert_member_record`，同一份实现。所有 `expert` 在写任何成员行之前解析完，失败时不写行。
11. **内联成员也可以运行在外部 CLI 上**，两种来源：`expert` 是外部运行时专家，或成员直接写 `cli_agent`
    （含义同 `spawn_external_cli`，`cli_agent` 与 `expert` 互斥）。只在团队声明了外部 CLI 类型时
    （`spawn_external_cli` 的同一门控）schema 才出现 `cli_agent` 与 `external_cli` 对象；`external_cli` 的属性与
    `spawn_expert.external_cli` 相同（`fallback_model_name`，以及仅在有内置模型目录时出现的 `builtin_model` / `effort`），
    两处由 `tool_member.external_cli_option_properties` 生成。三条外部 CLI 登记路径（`spawn_external_cli`、
    `spawn_expert` 的外部分支、内联成员）共用 `tool_member.register_external_cli_member`：声明检查、
    `ExternalCliModelRules`、`spawn_external_cli_agent`，同样的输入得到同样的成员行或同样的报错，只有报错里的工具名不同。
    外部专家的启动配置由 `tool_expert.expert_member_cli_config` 合并（声明配置为底），两条专家路径共用。
    `spawn_external_cli_agent` 新增 `lifetime` 透传给 `spawn_member`；`delete_unstarted_member` 回滚时顺带清掉
    `_external_cli_specs` 里的成员 → CLI 类型映射。退场与唤醒不需要新代码：外部 CLI 成员同样由 TeamAgent 外壳托管，
    调度器的退场扫描、成员自落 SHUTDOWN / DORMANT、点名经 `recover_member` 唤醒都按成员行工作；唤醒后
    `restart_teammate` 以 `resume_external_backend=True` 重建，没有 CLI 检查点时按新会话启动（只有 swarmflow 传
    `require_checkpoint=True`），不会因还没跑完一轮就休眠而起不来。
12. **外部 CLI 成员的 `prompt` 改为可选，与本地队友一致**（`spawn_external_cli`、`spawn_external_cli_agent`、
    内联 `cli_agent` 成员）。必填来自 #751 拆分 `desc` / `prompt` 时：那时 CLI 靠人设拼出的首次启动消息知道自己是谁；
    首次启动消息取消后，系统提示词只放团队准则，私有 `prompt` 由 `TeamContextTracker` 写进对话历史，两种成员都把空值
    当"没有私有约定"处理，必填只剩规则不一致。`spawn_bridge_agent` 的 `prompt` 仍必填：它是经 `adapter.connect`
    下发给远程 agent 的唯一角色说明。

## 拒绝的方案

- **内联成员把 `fallback_model_name` / `builtin_model` / `effort` 平铺在 `member` 上**（`spawn_external_cli` 的写法）：
  外部专家与 `cli_agent` 成员要两套写法，且与 `spawn_expert` 不一致；统一放进 `member.external_cli`。
- **内联外部成员先为整批算好"登记计划"再统一写行**：与本地内联成员现有的"逐个登记、后一个失败回滚前面已写的行"
  机制重复；回滚后不留成员行也不留 CLI 类型映射，结果相同。

- **工具做成 jiuwenswarm 的 harness element**：工具 schema、标识语义写在宿主，换宿主就要重写；成员行与 spec
  选择又在 core，两半被拆到两个仓。
- **一个查看工具四个 action**：跨单个专家 / 专家团 / 团内成员三种实体，参数随 action 变，core 不用 `anyOf`
  表达不了；拆成两个按实体的工具，各自只有 list / get。
- **给 `spawn_teammate` 加 `agent_template` 参数**：`desc` 变成条件必填、`prompt` 在一个工具里有两种含义。
- **`"designer/clip"` 字符串标识**：与 swarmflow 只认裸成员名的约定冲突；改为 `expert` + 可选 `group`。
- **启动时再查目录**：子进程模式下成员进程没有目录；包升级后同一成员恢复前后会悄悄变化。快照随成员行
  持久化（KB 级）。
- **扩展组织功能的 `ExpertGroupCatalog`**：属于组织子系统、在实验开关后面；Team 工具不应依赖组织功能。
  两套目录并存，收编列为遗留。
- **绑定团成员也一律从磁盘重新加载**：包在建队后升级或被卸载时，`clip-2` 会与预定义的 `clip` 不同源，甚至
  拉不起来。
- **外部专家完全用模板自带配置、跳过声明检查**：绕过 Team 的能力上限，模板里也没有命令路径、环境变量等
  部署信息。
- **目录对象挂到 `TeamRuntimeManager` 实例上、由 manager 传给 leader**：leader 在 `TeamAgentSpec.build()`
  里就完成装配（`create_team_tools` 的输入在那时确定），manager 拿到 agent 时已经太晚；目录因此是进程级的，
  与共享 DB / messager 同一层次，`set_expert_catalog` 仍是 manager 上的入口。

## 数据结构

```
TeamMember.options.agent_template = {
    "ref":          {"name": "clip", "group": "designer"},   # ExpertRef.to_dict()
    "version":      "1.0.0",                                   # 卡片 version；绑定团来源为空
    "snapshot":     {...AgentTemplateSpec(prompt_sections=[])...},   # 本地成员
    "external_cli": {...合并后的 ExternalCliAgentSpec...},           # 外部 CLI 成员
}
```

## 验证

- `experts/test_agent_group.py`：jiuwenswarm 原有 11 个加载器测试函数（22 个用例）随代码迁入并通过；9 个包（含 5 个错误用例）
  同步与异步加载产出或异常逐字节相等；卡片成员顺序、成员名取目录名、成员卡片保留 manifest `name`、只读
  manifest、成员 manifest 缺失报错。重构前后对照（不入库）：用 jiuwenswarm 原加载器跑同一批 9 个包，0 差异。
- `experts/test_template_member.py`：人设口径（排序、语言回退、占位符、逐段 strip、去空段）；
  `compose_template_member` 与分步调用逐字节相等；`apply_template_snapshot` 技能并集保序去重、不改基线。
- `tools/test_tool_expert.py`：未注入目录时 leader / teammate × autonomous / scheduled 工具集不含专家工具，
  注入后只给 leader 多出三个；查看工具无参可用、跳过坏包、不调用完整加载器；`member` 展开与报错；单个专家 /
  团内成员登记的成员行；9 条拒绝路径与重名、CLI 依赖缺失均不写成员行；包升级再卸载后仍按成员行快照构建；
  预定义与动态拉起的 `agent_template_spec` / `skills` / 人设相同；把 `open` / `Path.read_text` / `is_file` /
  `resolve` 等打桩为在事件循环线程上抛错后三个工具仍能完成 list / get / spawn；绑定团多实例不调用目录、
  包删除后仍可拉起、显示名自动追加成员名；外部专家登记为 `external_cli`、合并配置保留宿主字段并带上模板技能与
  `runtime.config`、成员行配置可读回；6 组模型参数在 `spawn_expert` 与 `spawn_external_cli` 上报错一致；
  `external_cli` schema 的内置模型门控；内联成员的 `cli_agent` / `external_cli` 门控，外部专家内联登记的成员行与
  `spawn_expert` 一致（含 `lifetime`、退场扫描可见），`cli_agent` 内联成员登记为 `external_cli`，5 组模型参数在内联成员与
  `spawn_external_cli` 上报错一致，拒绝路径与整批回滚不留成员行与 CLI 类型映射；`set_expert_catalog` → leader 构建 → 工具注册的端到端注入；运行时
  上下文经 JSON 往返（子进程拉起）保留快照与外部配置。

## 已知遗留

- `options.agent_template.ref` 尚未透传到名册事件与前端（★ 角标），SDD-0032 §10。
- 绑定团来源的 `version` 为空：建队时宿主没有把包版本写进 spec。
- 预定义专家团的外部成员不经声明检查、也不叠加声明的宿主级配置，与动态拉起的同类成员不一致（刻意保留）。
- 经 entry point 安装的三方 harness provider 专家不支持（`external_cli_agent_spec_from_template` 只认
  `claudecode` / `codex`）。
- 包目录被物理删除后，快照中的绝对路径在 `_prepare` 热挂时报错、成员进入 `ERROR`——沿用 F_81 的既有行为，
  本次没有新增专门用例。
- 组织功能的 `ExpertGroupCatalog` 与本目录并存，收编见 SDD-0032 §12。
