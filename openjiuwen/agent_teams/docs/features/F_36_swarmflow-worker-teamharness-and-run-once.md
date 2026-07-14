# F_36 swarmflow worker 改用 TeamHarness + TeamHarness.run_once 非流式接口

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-06-05 |
| 范围 | openjiuwen/agent_teams/{harness,workflow,tools,rails,agent}、pyproject.toml |
| 测试基线 | `tests/unit_tests/agent_teams/` 1384 passed, 16 skipped |
| Refs | #751 |

## 背景

swarmflow 每个 `agent()` 调用经 `TeamWorkerBackend` 映射成一个 worker，原实现是**裸
`create_deep_agent` + `Runner.run_agent`**——能力上与 teammate 不一致（缺 teammate 的
model 配置 / tools / skills / workspace / sys_operation）。现已有 `DeepAgentSpec` +
`TeamHarness` 装配能力，应让每个 worker 都是一个 `TeamHarness`，能力等同「**没有团队工具的
teammate**」。

同时 `TeamHarness` 只有流式交互接口（`start`/`send`/`outputs`/`stop`/`subscribe`），缺一个
返回值与 `Runner.run_agent` 一致的**非流式单次执行**入口供 worker 使用。

## 决策

1. **`TeamHarness.run_once` / `NativeHarness.run_once`**：非流式单次执行。`NativeHarness` 未重写
   `invoke`（继承 `DeepAgent.invoke`），`run_once` 直接 `self.invoke({"query": content}, session)`
   ——**不开 supervisor**，因此天然无 steer / 无 outputs 流。spec 的 `enable_task_loop` 决定单轮
   还是自驱 task-loop（**保留 DeepAgent 的 todo 规划**）。返回值与 `Runner.run_agent` 100% 一致。
   `run_once` 负责 ensure_initialized、session 创建/复用、结束 `teardown_tools`。`TeamHarness.run_once`
   转发 + 管 child session（注入 `session=child` 使 native `owns=False` 不重复 post_run）。头部
   `supervisor_task is not None` 守卫，禁止与流式 start 混用。

2. **worker = 没有团队工具的 teammate**：worker base spec = `TeamAgentSpec.agents["teammate"]`，
   缺失用 `agents["leader"]`。原始 spec 不含 team rail（team rail 是 `agent_configurator` 装配期
   注入），所以直接派生即「无团队工具」。`model_copy` 覆盖 card（worker 身份）、model（per-call
   config 或继承）、system_prompt（swarmflow worker 指令）、追加 `StructuredOutputTool` 实例；
   **不覆盖** `enable_task_loop` / `enable_task_planning`（保留 todo 规划）、**不动**
   workspace / sys_operation（保留 teammate 能力）。

3. **`SubmitResultTool` → `StructuredOutputTool`**：更名（文件 `structured_output_tool.py`，
   tool name `structured_output`），描述/参数走 `tools/locales` i18n（与团队其它工具一致：
   `descs/<lang>/structured_output.md` + STRINGS）。**保持对象**（不 spec 化）：backend 构造实例，
   追加进 worker spec.tools，由 `DeepAgentSpec._resolve_tools` 原样透传、harness ability_manager
   注册。`SwarmflowTool` 同步迁移到 `tools/locales`（`descs/<lang>/swarmflow.md` + `swarmflow.*`
   STRINGS），不再用内联 `_DESC` dict。

4. **工具生命周期归 harness**：`StructuredOutputTool` 的 id 由 ability_manager 按 owner re-qualify
   为 `structured_output_{owner_id}`（owner = worker card id，并发 worker 不撞），无需 backend 指定
   per-call id；清理由 `run_once` 的 `teardown_tools` 负责，backend **不手动** `resource_mgr.add/remove`。
   backend 仅 `harness.dispose()` 释放 sys_operation。

5. **model 语义：实例 → config**。worker 走 spec build 路径需 `TeamModelConfig`，`swarmflow_model_resolver`
   去掉末尾 `.build()` 返回 config；`_resolve_model` 返回 config 或 None（None → 继承 base spec model）。

6. **接线**：worker base spec 经 `agent_configurator → inject_team_handles
   (SWARMFLOW_WORKER_BASE_SPEC) → TeamToolRail → create_team_tools → SwarmflowTool →
   run_swarmflow → TeamWorkerBackend` 一路传到 backend。

7. **附带修复 pyproject**：`greenlet` 是 async SQLAlchemy 运行时通用必需，却被放在 optional extra
   `sqlite`，`uv sync` 默认不装 → db 用例失败。改 `sqlalchemy[asyncio]>=2.0.41`（greenlet 随核心
   装上）；`aiosqlite` 加入 `test` dependency-group（db 单测默认 sqlite 后端）。

## 拒绝的方案

- **run_once 走 supervisor 聚合**（start→send→等 IDLE→收 round result→stop）：worker 单轮无协作，
  不需要交互模型；复用原生 invoke 最简、返回天然一致、无 supervisor 并发开销。
- **StructuredOutputTool spec 化**（`BuiltinToolSpec` + holder via extras）：曾计划走 manifest 统一
  创建，但 per-call schema + 结果回传需要 holder/derive context，反而更绕；用户定为保持对象。
- **保留裸 `create_deep_agent` fallback**：统一走 spec 路径（leader 必填兜底），不维护两套实现。
- **per-call 唯一 tool_id（含 member_name）**：ability_manager 已按 owner re-qualify，per-call id 被
  覆盖、多此一举。

## 验证

```bash
uv sync
uv run python -m pytest tests/unit_tests/agent_teams/harness/ tests/unit_tests/agent_teams/workflow/ -o addopts="" -p no:cacheprovider -q
uv run python -m pytest tests/unit_tests/agent_teams/ -o addopts="" -p no:cacheprovider -q \
  --ignore=tests/unit_tests/agent_teams/cli --ignore=tests/unit_tests/agent_teams/observability
```
新增 `harness/test_run_once.py`（3 例）+ `workflow/test_worker_backend.py` 派生用例；
`test_submit_result_tool.py` → `test_structured_output_tool.py`。

## 已知遗留

- `docs/specs/` 与 `docs/features/` 存在历史编号碰撞（两个 `S_18`、两个 `F_27`），系并行开发所致，
  本次未重编号。
- worker 默认 model 依赖 base spec.model 非空（或 pool 命中）；teammate spec 无 model 且无 pool 的
  误配团队会让 worker 缺 model——属配置错误，未加额外兜底。
