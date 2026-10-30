# Swarmflow worker 渐进式工具暴露（team 级 opt-in）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-20 |
| 范围 | `schema/blueprint.py`、`agent/agent_configurator.py`、`tools/structured_output_tool.py` |
| 测试基线 | `test_agent_configurator.py::test_worker_base_spec_*`（3）、`test_structured_output_tool.py::test_card_declares_direct_exposure`、`test_member_spec.py`、`test_journal.py::test_call_signature_ignores_progressive_tool_key`；`agent_teams/workflow` + `agent_teams/agent` 全套通过 |
| 关联 | `S_12`（`TeamAgentSpec` 字段）、`S_18`（WORKER 不变量 7、结构化输出协议） |

## 背景

单 Agent 模式的渐进式工具暴露（`ProgressiveToolRail`：`tool_search` 元工具 + DEFERRED 工具）
已默认开启，团队侧零接入——`agents/harness/team/` 下对 `progressive_tool_enabled` 零引用。
swarmflow worker avatar 把 worker 基 spec 的全部工具一次性摊给模型，团队工具多时每次模型
调用都付完整清单的上下文税。

## 决策

1. **复用 harness 原生机制，不自建清单**。swarmflow 只负责把 `ProgressiveToolSpec(enabled=True)`
   写进 worker 基 spec；DIRECT/DEFERRED 冻结、BM25 检索、授权进会话全局状态、目录指纹校验
   全部由 `DeepAgent` 侧现有实现完成。一次性 worker 与 `agent_session` avatar 都经
   `derive_member_spec` 派生，该函数不覆盖 `progressive_tool`，两条路径自动继承。
2. **team 级独立字段 `TeamAgentSpec.progressive_tool: bool = False`**，不读单 Agent 全局开关。
   两类成员的工具面与风险不同构；默认关让存量团队字节兼容（关时 configurator 不写该键，
   基 spec 保持配置原值）。
3. **`StructuredOutputTool` 卡片显式声明 `exposure=DIRECT`**。`_apply_tool_exposure_policy`
   对未声明 exposure 且不在 `_DEFAULT_DIRECT_TOOL_NAMES` 的工具在渐进模式下判 DEFERRED；
   提交工具被延迟意味着带 schema 的调用必须先 `tool_search` 才能提交，小模型极易失败。
   在卡片构造期声明比在 backend 里 `set_exposure_declared` 更小且对所有消费者一致。
4. **`progressive_tool` 不进 `call_signature`**。identity 仍只含 label/phase/model(+isolation)，
   开关翻转不使 journal 缓存失效。

## 拒绝的方案

- swarmflow 自建 per-workflow 工具清单：两套清单必然漂移，授权持久化与指纹校验要重写。
- 共用单 Agent 的 `progressive_tool_enabled`：团队与单 Agent 的暴露面不同构，部署方需要
  独立控制。
- 暴露 `search_limit` 配置：无需求，取 `ProgressiveToolSpec` 默认 5。

## 已知遗留

- 授权名单随 `DeepAgentState` 由 checkpointer 落盘。引擎侧只保证**同进程**恢复（avatar session 走
  InMemory store，见 `S_18` 有状态会话契约第 12 条）；跨进程 / 重启后的续跑读回依赖宿主装配持久化
  checkpointer（`set_default_checkpointer`），引擎不装配。fork 子会话不继承授权（新档案盒重新检索）。
  一次性 `agent()` 每次调用都重新检索，属固有成本。
- worker 侧 `tool_call` 调用名单尚无任何层透传到前端，观测方案另行设计。
