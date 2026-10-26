# Swarmflow 专家 worker：`agent_template` 复用专家团成员

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-14 |
| 范围 | `workflow/backends/expert_specs.py`（新）、`workflow/backends/{team_worker_backend,avatar_session_backend}.py`、`workflow/engine/{primitives,journal,progress,backends/base}.py`、`workflow/engine/backends/mock.py`、`workflow/{runner,tool_swarmflow}.py`、`agent/agent_configurator.py`、`rails/team_context.py`、`schema/events.py`、`tools/locales/descs/{cn,en}/workflow/swarmflow.md` |
| 测试基线 | `test_expert_specs.py`（派生 6 场景）、`test_agent_configurator.py::test_expert_specs_handle_*`、`test_worker_backend.py::test_agent_template_*` / `test_describe_agent_template_*` / `test_execute_worker_prepends_persona_*` / `test_preprocess_accepts_agent_template`、`test_journal.py::test_call_signature_folds_agent_template_when_set`、`test_engine.py::test_agent_started_carries_agent_template_*` / `test_team_event_schema_carries_expert_fields`、`test_avatar_session_backend.py::test_agent_session_picks_expert_*`、`test_agent_session_model_lock.py::test_session_*agent_template*` / `test_fork_child_inherits_agent_template`；`workflow` + `agent` + `rails` 全套通过 |
| 关联 | `S_18`（WORKER 不变量 9）、`F_114`（`agent_type` 分叉与事件透传结构）、`F_115`（会话首锁）、`F_116`（options 校验位置） |

## 背景

swarmflow worker 一直是 teammate 基 spec 的复印件：系统提示词写死、工具是 teammate 通用工具，脚本只能表达
"这一步叫什么 / 属于哪个阶段 / 用什么模型 / 交给哪个外部 CLI"，不能表达"由谁来干"。Team 侧的专家团
（AgentGroup）已把每个成员的人设与能力快照装进 `TeamAgentSpec`（`agents[m].agent_template_spec` +
`predefined_members[m].prompt`），但 `agent_configurator` 只把 `agents["teammate"]` 递给 worker 后端——专家信息
到此丢失。画布类产品（每个节点一个专家：分镜师、镜头师、合成师）需要脚本按节点点名专家。

## 决策

1. **一个键、引用成员**：`options={"agent_template": "<成员裸名>"}`。组由会话绑定，算子不写组、不收 `group/member`。
   否决 `expert`（无既有词汇对齐）与兼收前缀（引擎多一条解析分支）。
2. **专家表由本仓从 `TeamAgentSpec` 自身 join 派生**（`derive_swarmflow_expert_specs`），不要求平台另造字段。
   否决"平台写一份 `swarmflow_experts`"——两份真相源。
   `MockBackend.KNOWN_OPTIONS` 同样声明 `agent_template`，使离线预演（`preprocess_swarmflow`）接受该键而无需改动引擎层。
   无快照的本地预定义成员（`agent_template_spec` 缺失）被 `derive_swarmflow_expert_specs` 中的 `continue` 跳过、不进专家表；脚本点名此类成员会得到 backend 的"非成员"报错，与点名完全不存在的名字行为一致。
3. **最小基线**：清空 `tools`，保留 rails（事件流 / 文件操作 / 上下文压缩 / 安全 / Skill rail 是运行基础设施，
   清掉即不可运行）+ 观测 rails；能力来自模板快照热挂（既有 `NativeHarness._prepare`）。否决继承 teammate 全部工具（最小权限）。
4. **persona 拼接不替代**：`persona + "\n\n" + swarmflow_worker.{free|schema}` 原文。否决完全替代——会丢
   `structured_output` 交付协议（backend 事后硬检查 `did not submit a structured result`）。user prompt 不动。
5. **外部运行时成员收录并报错**：进表、`spec=None`、`run()` 明确报"暂未支持"。否决静默当 native 跑；否决不收录
   （后续"专家支持三方 Agent"要重做表）。
6. **签名 / 事件 / 会话首锁逐一复刻 `agent_type`**：非空并入 `call_signature`；`AGENT_STARTED` 增
   `agent_template` + `display_name`（新钩子 `describe_agent_template`）；`AgentSession._opened_template` 首锁，
   换名指向 `fork()`；fork 子会话经 options 合并天然继承。
7. **`_gate_agent_template` 先于 `_gate_agent_type` 运行**：两者同时非空时由模板闸优先报互斥错误（见决策 8）。非字符串值在引擎的 `_build_opts`（选项袋唯一校验点，紧随未知键校验）里先于 `AGENT_STARTED` 发射即被 `EngineError` 拒绝——`agent()`、`agent_session().send()` 与 fork 合并后的 options 因此共用同一道校验，脚本错误不会留下半身份的 agent 事件；backend 的 `isinstance` 判断保留为纵深防御（直连 backend 时仍走"不含 `/` 的裸成员名"报错分支，与含 `/` 的前缀名同源）。
8. **与 `agent_type` 互斥**：模板决定运行时；同时出现是脚本错误。
   Leader 永远不进专家表（它由 `AGENT.md` 另行装配、persona 是"分派与审阅"而非干活，且它正在驱动发起
   workflow 的那段对话）；`agent_template="leader"` 由 `_gate_agent_template` 单独报"is the Team Leader, not a worker"，
   而不是泛泛的"not a member"，免得作者去改包。否决把 Leader 单独派生成 worker——指挥官人设拼进 worker 提示词
   会让它去找不存在的团队工具。
9. **缺省零变化**：不传键时 spec / 提示词 / 签名 / 事件逐字节不变（`_execute_worker` 仅在命中时多传 `expert=`）。

## 已知遗留

- 外部运行时成员在 swarmflow 内执行（后续特性替换 `_gate_agent_template` 的报错分支为 `_execute_external_worker` 映射）。
- `skills` 可见性声明沿用 `agents[m].skills`（平台组装时已含模板技能名）；未做"只保留模板技能"的二次裁剪。
- rail 自注册的工具（如 Skill 工具集 rail）随 rail 保留，"最小权限"只覆盖 `DeepAgentSpec.tools`。
- 平台侧 pin 未升级时，旧引擎的 `KNOWN_OPTIONS` 会**硬拒** `agent_template`（`EngineError`），与 `agent_type` 的静默忽略不同。
- 首轮失败后用不同 `agent_template` 重试只改写锁、不重建 avatar（与既有模型首锁同形，待后续统一处理）。
