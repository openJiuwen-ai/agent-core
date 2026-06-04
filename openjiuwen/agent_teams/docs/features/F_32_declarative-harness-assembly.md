# DeepAgent 装配体系归一到纯声明式 manifest

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-04 |
| 范围 | `harness/factory.py`、`agent_teams/harness/{native_harness,team_harness}.py`、`agent_teams/harness/manifest/{introspect,registration}.py`、`agent_teams/schema/deep_agent_spec.py`、`agent_teams/rails/{builtin_elements,elements,registration,team_context}.py`（新增）、`agent_teams/agent/{agent_configurator,member_runtime,team_agent}.py`、`agent_teams/schema/blueprint.py`、`agent_teams/external/runtime.py`、`core/runner/team_runner.py` |
| 测试基线 | `make test TESTFLAGS="tests/unit_tests/agent_teams/ tests/unit_tests/harness/"` → 3149 passed / 31 skipped |
| Refs | #751 |

## 背景

DeepAgent 的装配体系混着四套机制，与 `jiuwenswarm/agents/swarm/DESIGN.md` 三原则
（①纯声明式装配，禁 customizer / `rail.init` 后处理挂载；②成员共享 config 源，不从预构造父
DeepAgent 继承；③跨序列化靠 seed 重建）背离：

1. **两套 rail/tool 注册表并存**：`_RAIL_PROVIDER_REGISTRY`（manifest provider，已就绪但
   openjiuwen 内零元素用）+ `_RAIL_TYPE_REGISTRY` / `_TOOL_TYPE_REGISTRY`（class registry，
   内置 rail/tool 走它）。`RailSpec.build` 是 "provider 优先 → fallback class" 的双路径。
2. **6 个内置 team rail 手搓装配**：`AgentConfigurator.setup_agent` 手动 `new` 6 个 team rail
   + `TeamHarness.build` 命名参数 + `_deep_provider` 闭包 `add_rail` + 对 TeamToolRail 手动
   `set_sys_operation`/`set_workspace`/`init(native)`——后三步与 `DeepAgent._ensure_initialized`
   对每个 `DeepAgentRail` 的自动 init **逐字重复**。
3. **customizer 后处理挂载**：`spec.agent_customizer` + `TeamHarness.run_agent_customizer`，
   build 后对 DeepAgent 做后处理。DESIGN 明确禁止，且生产零实质使用（唯一真实赋值是 cold
   recover 的传递性复制，测试全 `lambda *_: None`）。
4. **NativeHarness 绕路构造**：`NativeHarness(deep_agent_provider)` → `prepare_config` 先 build
   一个 **template DeepAgent 实例**，再 `configure(template.deep_config)` + 搬
   `template.configured_rails()` 到 self（NativeHarness IS-A DeepAgent）。template 随即丢弃——
   正是原则②反对的"先造单 agent 再继承"，且 template 的 ability registry 不被 config 复制，
   逼出了 #2 的手动 eager init 补偿。

`openjiuwen.agent_teams.harness.manifest`（descriptor + catalog + ConstructionInput + 反射 +
catalog 驱动注册）已下沉为通用框架，swarm 用它声明 36 个平台元素。本次把上述四套机制全部
归一到这套声明式装配。

## 数据结构 / 状态机

**① `DeepAgentParts`（`factory.py`）**：解耦"组装"与"应用"。
```
resolve_deep_agent_parts(model, ...) -> DeepAgentParts(config, rails, tool_cards, tool_instances)
apply_deep_agent_parts(agent, parts)            # agent 可以是 new DeepAgent 或 NativeHarness 自己
create_deep_agent(...) = resolve + DeepAgent(card) + apply
DeepAgentSpec.resolve_parts(context) -> DeepAgentParts   # 在 resolve 内做 RailSpec/tool/subagent provider 解析
DeepAgentSpec.build(context) = resolve_parts + new + apply
NativeHarness.prepare_config = spec.resolve_parts(ctx) + apply_deep_agent_parts(self, parts)   # 无 template
```

**② manifest catalog**：`get_catalog()` 现含 16 个 openjiuwen 内置元素——10 个 builtin
（`task_planning`/`skill_use`/`subagent`/`filesystem` + 可选 `token_tracking`/`tool_tracking`/
`ask_user`/`confirm_interrupt`/`web_search`/`web_fetch`）+ 6 个 team（`team.tool`/`team.policy`/
`team.workspace`/`team.tool_approval`/`team.plan_mode`/`team.reliability`），与 swarm 的
`swarm.*` 命名空间共存。

**③ team live handle 经 `BuildContext.extras`**：`TeamHandleKey` 命名空间下的 `team_backend` /
`workspace_manager` / `model_allocator` / `messager` / `on_teammate_created` / `swarmflow_launcher`
+ `RAIL_CACHE`。`AgentConfigurator` 是唯一写入者（`inject_team_handles`），team rail 工厂经
accessor 直读（运行时句柄，不进 ConstructionInput schema——对齐 swarm 把 `trajectory_registry`
塞 extras 的范式）。`team_rail_cache` 让一个 rail 实例跨 native 重建复用（保 ReliabilityRail
monitor 状态 / TeamPolicyRail mtime cache / TeamToolRail 已注册 tools）。

## 决策

四阶段顺序落地，每阶段独立可验证：

1. **NativeHarness 正向构造**：抽 `resolve_deep_agent_parts`/`apply_deep_agent_parts`，
   NativeHarness 从 spec 直接 configure 自己，消除 template 绕路（原则②）。`team_harness` 过渡期
   用 `extra_rails` 承载尚未归一的 team rail（阶段③清除）。

2. **class registry 彻底干掉**：10 个内置 rail/tool 用 `@harness_element` 声明（多数 `builder=cls`，
   `skill_use` 的 `skills_dir` 解析迁进工厂）；manifest 加对称的 `class_tool_adapter`（注 language）；
   删 `_RAIL_TYPE_REGISTRY`/`_TOOL_TYPE_REGISTRY`/`register_rail_type`/`register_tool_type`/
   `_ensure_builtin_*`；`RailSpec.build`/`BuiltinToolSpec.build` 只查 provider，context 为 None 时
   合成最小 `BuildContext(language, workspace)` 让 provider 仍观察到。引用名保持不变（`task_planning`
   等）→ 零 spec 迁移。

3. **team rail manifest 归一**：6 个 team rail 用 `ConstructionInput`（param=静态配置、
   context_field=环境值、live handle 走 extras accessor）+ 工厂声明；`AgentConfigurator` 改为
   构造携带 team handle 的 member context + 把 team `RailSpec` 注入 `build_spec.rails`（决策逻辑
   "该不该挂"留 configurator，"能不能挂"由工厂 `return None`）；`team_harness` 删 `_MountedRails`
   + 6 rail 参数 + 手动 set/init + `extra_rails`。team rail 现在和所有 rail 一样经
   `ensure_initialized` 自动 init。

4. **customizer 彻底干掉**：删 `spec.agent_customizer` 字段、`MemberRuntime.run_agent_customizer`
   protocol 方法 + `AgentCustomizer` 别名、`TeamHarness` 的 run/apply/缓存、`ExternalCliRuntime`
   no-op、`recover_from_session` 的 reinject。平台改用 provider（swarm 已落地）。

**关键安全性核对**：去掉 TeamToolRail eager init 安全——TeamPolicyRail static section 在
`__init__` 用构造参数生成、不读 `ability_manager`；LLM 看 tools 走 `ability_manager.list()`
（before_model_call），晚于 `ensure_initialized` 的自动 init。唯一受影响的是测试 `_tool_names`
helper：现在需先 `await native.ensure_initialized()` 再读 team tools（build 只 queue rails）。

## 拒绝的方案

- **手搓 team rail provider（第一版方案）**：直接 `register_rail_provider` + 自定义
  `TeamHandleKey`/accessor/`TeamBuildContext` 子类，绕过 manifest 框架。拒绝原因：manifest 已是
  下沉的通用装配框架，手搓等于另起一套；`@harness_element` + `ConstructionInput` 才是范式。
- **`TeamBuildContext(BuildContext)` typed 字段承载 team handle**：拒绝原因：平台可能传自己的
  `BuildContext` 子类（如 `SwarmBuildContext`），单继承下两个子类无法合并；team handle 走 `extras`
  才能与平台 context 共存（同一实例两边都满足）。
- **NativeHarness `create_deep_agent(target=self)` 参数**：拒绝原因：参数从 19→20、`if target`
  分支穿插逻辑；"抽 spec→parts 纯函数 + apply" 职责更清晰，且 `create_deep_agent` 对外零变更。
- **保留 TeamToolRail 手动 eager init（方案 B）**：拒绝原因：留半条特殊路径与"消除特殊路径"
  目标冲突；核对证明 eager init 对生产无约束，靠 `ensure_initialized` 接管即可。

## 验证

- `tests/unit_tests/agent_teams/` + `tests/unit_tests/harness/` → **3149 passed / 31 skipped**。
- 端到端：`TeamAgentSpec(...).build()` 后 `find_rails(TeamToolRail)`/`find_rails(TeamPolicyRail)`
  非空；`ensure_initialized` 后 team coordination tools 在 ability_manager 可见；
  `RailSpec(type=...)` 全走 provider（class registry 已删，unknown type 抛 `ValueError`）。
- `grep -rni customizer openjiuwen/` 生产代码清零。

## 已知遗留

- **跨项目 breaking change（swarm 必须适配）**：删除 `deep_agent_spec._RAIL_TYPE_REGISTRY` /
  `register_rail_type` / `register_tool_type` 是公共 API 变更。jiuwenswarm 的
  `tests/agents/swarm/test_manifest_catalog.py` 断言 `das._RAIL_TYPE_REGISTRY == set()`，需改为
  catalog parity（`get_catalog()`）。
- **`context_engineering` 死代码**：原 class registry 的 `context_engineering` 指向不存在的
  `harness.rails.context_engineering_rail`（import 恒失败）；归一时直接不声明，保持其不可用语义。
  未来若要启用，声明指向 `ContextProcessorRail`。
- **`harness/` / `schema/` / `rails/` 子目录无 CLAUDE.md**：本次只更新顶层
  `agent_teams/CLAUDE.md` + `agent/CLAUDE.md`，子目录设计细节落在本 feature 文档。
- **提交约定文档不一致**：`docs/CLAUDE.md` 写"代码+文档两提交"，`agent_teams/CLAUDE.md` +
  `CLAUDE.local.md` 写"feat/test/docs 三提交"。本次按后者（三提交）落地；两份约定应择一统一。
