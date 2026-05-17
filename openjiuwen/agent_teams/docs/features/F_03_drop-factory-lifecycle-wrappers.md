# Drop factory.py: lifecycle wrappers fold into runtime/

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-09 |
| 范围 | 删除 `openjiuwen/agent_teams/factory.py`；改 `openjiuwen/agent_teams/runtime/manager.py`、`openjiuwen/agent_teams/__init__.py`；同步 `tests/unit_tests/agent_teams/test_team_agent.py` / `test_team_agent_tools.py` / `test_runner_team_runtime.py`；同步 `docs/zh|en/...AgentTeams.md` / `概述.md` / `Overview.md` / `API文档/agent_teams.md`；同步 `agent_teams/CLAUDE.md`、`runtime/CLAUDE.md`、`docs/specs/S_01` / `S_04` / `S_06` / `S_12` |
| 测试基线 | `tests/unit_tests/agent_teams/test_runner_team_runtime.py` 45 passed；`test_team_agent.py` 3 passed；`test_team_agent_tools.py` 4 passed |
| Refs | `#751` |

## 背景

`agent_teams/factory.py` 持有 4 个函数：`create_agent_team`、`resume_persistent_team`、`recover_agent_team`、`recover_for_existing_session`。仅前两个对外（写在 `__init__.py.__all__`），后两个虽然在 `factory.py.__all__` 但 package 顶层未导出。

事实层面：

- 4 个函数里 3 个 lifecycle 函数（`recover_*` / `resume_*`）的**唯一调用方**是 `runtime/manager.py:_apply_action`。其中 `resume_persistent_team` / `recover_for_existing_session` 是单行 wrap（仅 `await agent.<方法>(session)`）；`recover_agent_team` 多一步 `await agent.recover_team()`，但合起来只 2 行。
- `create_agent_team` 是 `TeamAgentSpec(...).build()` 的便利封装。CLAUDE.md 已经在引导用户走 spec 路径："新增配置项走 `TeamAgentSpec`，不要在 `create_agent_team` 上堆 `**kwargs` 或平铺参数。"
- 上一次 review 里加的 `runtime_spec` 透传参数（commit `9e3539fa`）是这个错误结构的副作用——manager 是参数的来源 + 唯一消费方，但被迫穿过 factory wrapper 才能到 `TeamAgent.recover_from_session`。wrapper 空、却得吸收上层细节，是教科书式的"封装病"。

命名也在说谎："factory" 字面是创建，但 4 个函数里 3 个是 lifecycle 转换；`runtime/manager.py` 是真正的 lifecycle 决策者却得反向 import factory。

## 决策

1. **删除 `factory.py`**。物理消失，不留 deprecation shim、不留 1 行 re-export。
2. **`__init__.py` 收紧公共 API**：移除 `create_agent_team` / `resume_persistent_team` 导出。`__all__` 不再列这两个名字。
3. **`runtime/manager._apply_action` 直接调 TeamAgent 方法**：
   - `COLD_RECOVER` → `TeamAgent.recover_from_session(team_session, team_name, runtime_spec=spec)` + `await agent.recover_team()`（lazy import TeamAgent，避免顶层 import 时拉重依赖）
   - `WARM_RECOVER` → `pool_entry.agent.recover_for_existing_session(team_session)`
   - `NEW_TEAM_IN_SESSION_WARM` → `pool_entry.agent.resume_for_new_session(team_session)`
   - `NEW_TEAM_IN_SESSION` / `CREATE` → 原样调 `spec.build()` + 必要的 `agent.resume_for_new_session`，不变
4. **`runtime_spec` 参数自然消失**：cold recover 的 spec 现在直接来自 `_apply_action` 的局部变量，不再需要在 `factory.recover_agent_team` 上扩签名。`TeamAgent.recover_from_session(runtime_spec=spec)` 上的参数保留——TeamAgent 这个类级能力允许独立调用，参数依然 `Optional[TeamAgentSpec]`。
5. **公共恢复 API 整体收敛到 Runner 入口**：`Runner.run_agent_team[_streaming](agent_team=spec, session=...)` 是用户面唯一入口；冷启动 / 热恢复 / 切 session 由 dispatch 表识别。doc 里删除独立的 `resume_persistent_team()` / `recover_agent_team()` 示范代码，改用 Runner 调用。
6. **低层入口仅供运维脚本**：`TeamAgent.recover_from_session(...)` + `await agent.recover_team()` 仍可直接调，但定位为非公共契约，docstring / spec 明文标注。

## 拒绝的方案

**A. 保留 factory.py 但搬运实现到 runtime/lifecycle.py，factory 仅 re-export**

中间状态。文件名仍说谎，公共 API 表面冗余暴露内部 helper（`recover_agent_team` 在 factory 的 `__all__` 里、但 `__init__.py` 不导出，定位本就模糊）。re-export 制造稳定性假象——下次重构得再处理一次。

**B. 把 `create_agent_team` 留下作为 1 行 wrap**

挂在哪里都尴尬：留在新文件里，名字是 `factory.py` 的回响；放进 `__init__.py` 里实现，违反"`__init__.py` 只 import 不实现"的约定；包装值只省 `.build()` 后缀，不值得为它制造一个新模块。CLAUDE.md 已经把 `TeamAgentSpec(...).build()` 当作"统一入口"宣传，删掉 wrapper 反而更一致。

**C. 把 `resume_persistent_team` 作为公共 API 在 runtime/lifecycle.py 实现并从 `__init__.py` 导出**

它在 docs 里被示范过，似乎有公共 API 承诺。但实际 codebase 内（包括所有 tests/examples）唯一调用方还是 manager 自己——所谓"公共"是文档标注、不是真实流量。Runner 入口已经能覆盖 doc 上的全部场景（"在新 session 里跑下一轮"= `Runner.run_agent_team_streaming(agent_team=spec, session=new_id)`）。保留它就要保留契约负担与字段透传通道，得不偿失。

**D. 把 4 个函数搬进 `agent_teams/runtime/lifecycle.py`，作为 internal helper**

新文件 = 新分层。但 3 个 wrapper 都是 1-2 行的薄壳，inline 进 `_apply_action` 后控制流反而更顺：每个分支自我描述，不再需要跳转到 wrapper 看是不是在做别的事。当函数体小到没有抽象价值时，直接 inline 是 Linus 的偏好。

## 数据结构 / 状态机

无变化。`TeamRuntimeContext` / pool entry / dispatch truth table 形态不变；`TeamAgent` 的 lifecycle 方法 (`recover_from_session` / `recover_team` / `resume_for_new_session` / `recover_for_existing_session`) 签名不变。

## 验证

- `tests/unit_tests/agent_teams/test_runner_team_runtime.py` 45 用例全过。其中：
  - `test_team_runtime_manager_cold_recover_reinjects_runtime_spec` 改 patch 目标为 `TeamAgent.recover_from_session`（旧路径 `runtime.manager.recover_agent_team` 已不存在）；新增 `assert agent.recover_calls == 1` 锁定 `recover_team` 也被触发。
  - `test_runner_team_runtime_manager_resumes_new_session_and_recovers_history` 删除 `resume_persistent_team` / `recover_for_existing_session` 的两个 patch helper—— `FakeTeamAgent` 自带 `resume_for_new_session` / `recover_for_existing_session` 实现，inline 后 `resume_calls` 仍能被记录。
- `tests/unit_tests/agent_teams/test_team_agent.py` 与 `test_team_agent_tools.py` 共 7 用例：`create_agent_team(...)` 全部替换为 `TeamAgentSpec(...).build()`，无新行为。
- `make check` 与重构无关的 pre-existing lint warning 保持原状（不在本次范围）。

## 已知遗留

- `TeamAgent.recover_from_session` 仍以 `runtime_spec: Optional[TeamAgentSpec] = None` 收 customizer reinjection。当前唯一调用方（manager）总传 spec；保留 `Optional` 是为了让 TeamAgent 单独可调（脚本场景）。如果未来明确不再支持脚本独立调用，可把它收紧为必传参数。
- 文档中的"低层入口"段落（`TeamAgent.recover_from_session` + `agent.recover_team`）目前只在 spec 与 README 提了一行。如果运维脚本团队要走这条路径，下次需要补一个最小可运行示例，而不是只声明"可用"。
