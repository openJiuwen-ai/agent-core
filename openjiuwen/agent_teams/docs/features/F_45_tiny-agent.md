# Tiny Agent：随时唤起的极简 native harness

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-06-24 |
| 范围 | 新增 `openjiuwen/agent_teams/tiny_agent.py`；`schema/blueprint.py`（`TinyAgentSpec` + `TeamAgentSpec.tiny_agents`）；`agent/infra.py`、`agent/agent_configurator.py`、`agent/team_agent.py`；`__init__.py`；前置重构：`tools/structured_output_tool.py`（从 `workflow/backends/` 移入） |
| 测试基线 | `tests/unit_tests/agent_teams/test_tiny_agent.py` 17 passed；`workflow/` + `tools/test_structured_output_tool.py` 92 passed；`test_team_agent.py` 15 passed |
| Refs | #1047 |

## 背景

团队运行中常需临时跑一个轻量 LLM 任务（摘要、标题生成、短对话）。在此之前唯一现成路径是
swarmflow 的 `TeamWorkerBackend`——但那是绑定 swarmflow 引擎、派生 teammate 能力、走
worktree + workspace 的重型路径，不适合"随手唤起一个只有系统提示词 + 模型的极简 agent"。

本特性新增 **tiny agent**：在团队运行的任何时期，用极简函数封装快速创建并调用一个 native
harness，执行一次性任务（单轮）或多轮对话任务。tiny agent **除结构化输出工具外不带任何其他
工具**（无 workspace / sys_operation / skills / subagents / 团队协作工具）。

## 核心设计

**tiny agent 本质 = 一个极简 `DeepAgentSpec` 喂出来的 `NativeHarness`**。所有维度差异都只是
"持有 / 配置方式"，不是不同的类：

- 交互维度：`run()` 单轮无状态（`NativeHarness.run_once`，每次新建临时 harness + dispose）/
  `chat()` 多轮有状态（一个持久 `NativeHarness`，start 一次、每轮 `send` + 等回到 IDLE，照
  `AvatarSessionManager._drive_round` 范式）。
- 生命周期维度：ephemeral（调用方持有，`async with` 自动清理）/ team-scoped（`TeamInfra` 按名
  持有多个，team 停止时 dispose）。
- 预定义维度：标题 / 摘要只是"预填了 system_prompt + default_schema 的 `create_tiny_agent`"。

底层都是同一个 `TinyAgent` + 同一个 `create_tiny_agent` 工厂。

### 模型解析

创建时只传 `model_name` 字符串，经注入的 `model_resolver`（封装
`resolve_member_model(team_spec, model_name=..., model_index=None)`）解析出完整
`TeamModelConfig`，解析不到则 fail fast（`AGENT_TEAM_CONFIG_INVALID`）。与
`TeamWorkerBackend` / `AvatarSessionManager` 的 `model_resolver` 注入模式一致。`TeamInfra`
存 `tiny_agent_model_resolver`（`AgentConfigurator.setup_infra` 注入，capture `ctx.team_spec`），
team-scoped 与 ephemeral 调用方共用同一解析路径。

### 结构化输出

复用现成的 `StructuredOutputTool` + `StructuredOutputFinishRail`（harness 无原生
`response_format`）。`run()` 把工具放进 per-call 的 spec 副本（`model_copy(update=...)`，绕过
字段校验，与 worker 一致）；`chat()` 在 schema turn 用 `ability_manager.add_ability` /
`remove_ability` 临时挂载。`schema` 参数支持 dict / pydantic / None，复用
`workflow/engine/schema.py` 的 `resolve_schema` + `coerce`。

### 两种生命周期

- **ephemeral**：`create_tiny_agent(...)` 或预定义 `create_title_agent` / `create_summary_agent`
  （返回 `TinyAgent`，可复用）+ 一步到位 `generate_title` / `generate_summary`（返回字符串，
  `async with` 自动清理）。
- **team-scoped**：`TeamAgentSpec.tiny_agents: dict[str, TinyAgentSpec]` 声明**多个**独立的
  不可见成员；`TeamAgent.get_tiny_agent(name)` 首次访问时 lazy build、缓存到
  `TeamInfra.tiny_agents`（一名一个），`stop_coordination` / `shutdown_self` 时全部 dispose，
  `pause_coordination` 保留。**不入团队 DB**。

## 关键决策

- **per-process 语义**：`TeamInfra` 是 per-process 的（leader / 各 teammate 各自进程），所以
  team-scoped tiny agent 的"团队唯一（每名一个）"工程上落地为"每进程每名一个"——跨进程共享
  一个 LLM 实例没有意义（多轮上下文在内存）。
- **`run()` 每次新建临时 harness**：保证无状态、无 rail 累积、真 ephemeral，且每次用唯一
  card id（`{name}-{seq}`）避免并发调用共享 owner id 撞工具注册。`chat()` 用持久 harness 保活。
- **team-scoped 纯自定义**：预定义（标题 / 摘要）只服务 ephemeral；team spec 不引用预定义类型。
- **纯 Python API**：不注册为 leader 可调的 LLM 工具。摘要 / 标题等是框架 / 应用代码调用。
- **`enable_task_loop`**：`DeepAgentSpec.resolve_parts` 既有行为硬编码 `enable_task_loop=True`；
  tiny agent 走 `run_once` 单次 invoke 不触发自驱循环，与 worker 一致，未改既有逻辑。

## 前置重构：结构化输出工具归属

`StructuredOutputTool` / `StructuredOutputFinishRail` 无 swarmflow 业务耦合（只依赖
`tools/locales` + core + harness base），原埋在 `workflow/backends/structured_output_tool.py`。
tiny agent（顶层能力）import 它会形成 `tiny_agent → workflow.backends` 的反向依赖坏味道。
故 `git mv` 到 `tools/structured_output_tool.py`（tool 的正确归属），彻底改 import（不留
re-export shim），swarmflow 的 `team_worker_backend` / `avatar_session_backend` /
`backends/__init__` 与对应测试一并切换；`backends/__init__` 仍 re-export `StructuredOutputTool`
保持 swarmflow 子包公共表面不变。

## 被拒方案

- **单一 team-scoped 实例**：初版设想 team 内唯一一个不可见成员；用户要求支持**多个**独立的
  团队级 tiny agent，改为 `dict[name, spec]` + 按名缓存。
- **tiny agent 直接 import `workflow.backends`**：依赖方向不对，改为前置把工具下沉到 `tools/`。
- **`run` 与 `chat` 复用同一 harness**：`run_once` 在 supervisor 活跃时会 raise；且单轮复用会
  累积 rail。改为 `run` 每次临时 harness、`chat` 独立持久 harness，互不干扰。

## 已知遗留

- 预定义 prompt / schema 暂作模块常量（`_TITLE_PROMPT` / `_SUMMARY_PROMPT` / `_*_SCHEMA`，
  按语言）；文案增多时再迁 `tools/locales`。
- team-scoped `chat()` 的持久 harness 在 `pause_coordination` 时保留（仅 stop / shutdown
  dispose）；跨 pause/resume 的 tiny chat 上下文行为未做专门处理（当前同进程 resume 自然保活）。
