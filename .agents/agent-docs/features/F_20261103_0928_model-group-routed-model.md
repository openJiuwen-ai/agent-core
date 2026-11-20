---
id: F_20261103_0928_model-group-routed-model
title: 模型组路由模型的全程命名与可观测
type: F
author: hh0205
author_email: huanghao206@huawei.com
modules:
- openjiuwen/agent_teams
- openjiuwen/core/foundation/llm
- openjiuwen/extensions/observability
- openjiuwen/harness
files:
- openjiuwen/core/foundation/llm/model_clients/intelli_router_model_client.py
- openjiuwen/core/foundation/llm/routing/intelli_router_errors.py
- openjiuwen/core/runner/callback/events.py
- openjiuwen/extensions/observability/callback_handler.py
- openjiuwen/harness/image_modality_probe.py
- openjiuwen/agent_teams/agent/stream_controller.py
- openjiuwen/agent_teams/agent/team_agent.py
- openjiuwen/agent_teams/reliability/handler.py
- openjiuwen/agent_teams/reliability/rail.py
- openjiuwen/agent_teams/reliability/reporter.py
- openjiuwen/agent_teams/prompts/messages.py
- openjiuwen/agent_teams/team_context.py
designs: []
design_note: issue #5985 驱动的跨仓特性，无独立 D 设计；jiuwenswarm 侧配套改动在其仓库另行归档
---

# 模型组路由模型的全程命名与可观测 实施与决策归档

## 关联依据

- 设计：无独立设计文档（issue #5985 直接驱动）；关键选择与被排除方案见"实际实现与开发决策"。
- spec：`S_19_reliability-framework.md`（不变量 8，路由通知双路投递）、
  `S_09_prompts-and-rails.md`（不变量 31，名册模型字段）、
  `S_14_monitor-and-observability.md`（不变量 18，模型组 span 命名与失败标记）。
- 日期：2026-11-03。
- 实际实施范围：模型组（intelli_router）会话中路由实际选中模型在提示词、history、trace、
  名册四条链路的命名；成员/leader 的切换告知；图像模态探测与共享路由的状态隔离；
  `intelli_router_errors` 三个私有辅助提为公共。jiuwenswarm 侧（占位符写入 runtime.setting、
  runtime_state 组名）不在本仓范围。

## 决策归属与关系

既有路由通知机制（`MODEL_ROUTE_NOTICE`、`ReliabilityHandler._deliver_route_notice`，先于本特性）
为续写关系：本特性保留成员路径的立即投递与 (router, deployment, reason, status) 去重键，
变更 leader 自身路径的投递时机与通知文案（见 S_19 不变量 8），旧结论在该两处被局部替代。

## 上下文与跨边界影响

- **跨仓契约**：占位符 `<routed-model/>` 由 jiuwenswarm 的 RuntimePromptRail 写入
  runtime.setting，本仓 router 在每次尝试发送前填充。两侧独立可部署：rail 在安装的 openjiuwen
  不支持时回退写组名；本仓适配器以 `label or model_name or "unknown"` 兜底，畸形部署也不会把
  字面占位符发给厂商。
- **探针 → 共享路由（本特性修复的既有缺陷）**：图像模态探测在会话启动时对模型组 agent 发起
  后台模型调用，此前与真实调用共享缓存路由——第 1 顺位故障时探测抢先撞错并把冷却写进共享
  路由，用户第一条消息被静默送到第 2 顺位且无任何事件。`IntelliRouterConfig.router_scope`
  参与路由缓存 key，探测持独立实例，其失败只影响探测自身。
- **无 trace 上下文的后台调用**：组路由的失败原先无 span、无日志。选路 INFO 日志
  （选中模型 + 冷却跳过列表）在客户端发送路径上记录，覆盖全部组调用。

## 实际实现与开发决策

1. **占位符在发送前填充**（`_RoutedModelAdapter.transform_request`）：提示词构建时路由尚未
   发生，"上一轮实际模型"回写无法覆盖 failover 的每一次尝试；发送前是唯一能拿到该次确定名字
   的位置。`_fill_routed_model` 只复制携带占位符的消息，不改调用方列表（router 逐次复用）。
2. **失败请求照常命名并标错**：`LLM_ROUTE_ATTEMPT` 在每次尝试发送时（早于任何响应）触发，
   span 逐次改名，最终名为最后一次尝试——401 这类无输出请求在 trace 里可见、可归因；整通
   失败由客户端触发 `LLM_CALL_ERROR`（沿用 openai/anthropic 客户端模式）标 ERROR + error.type。
   why-not：仅在 `LLM_ROUTE`（路由应答后）命名——被拒请求将永远无名。
3. **leader 自己的通知搭下一条输入**：通知在失败被检测那一刻产生，而本轮输入早已发出，
   物理上本轮输入不可能包含它；`ReliabilityRail.on_user_message` 把待发通知拼在下一条输入
   最前面。why-not：作为独立 round 投递——通知 round 会顶掉切换那次调用已在备用模型上
   产出的回答。
4. **通知只报故障侧**：替代模型由路由按当次可用性现选（冷却恢复后可能回到更前顺位），点名
   无法保证正确；文案为"已自动切换到组内其他可用模型"，恒为真。
5. **服务模型记录后台化**：`StreamController` 在 `llm_route(selected)` 上的成员行写库改
   `create_task`（任务引用集 + stop 排空），用户可见的 chunk 流不等数据库。
6. **名册字段**：`models_used` 去重、至多 5 条、最新标 latest call；写成功 bump `updated_at`
   触发 `TeamContextTracker` 名册 diff 重投。

## 与设计的偏差

无独立设计文档；实施中确认的方向级选择均已在上一节记录 why / why-not。

## 实际执行与证据

| 执行日期 / 验证范围 | 实现版本与环境 | 命令 / 复现步骤 / 记录链接 | 实际结果 | 未覆盖项 / 失败原因 |
| --- | --- | --- | --- | --- |
| 2026-11-02 单测（命名/通知/隔离/日志） | develop 基线 worktree + 共享 venv | `pytest tests/unit_tests/core/foundation/llm/test_intelli_router_routed_model.py test_intelli_router_model_client.py::TestRouterCache tests/unit_tests/extensions/observability/test_llm_route_span.py` | 通过（含失败标错、scope 隔离、选路日志） | 无 |
| 2026-11-02 单测（team 侧） | 同上 | `pytest tests/unit_tests/agent_teams/reliability/ test_model_group_naming.py test_team_policy_rail.py` | 265 通过 + 2 xfail | 无 |
| 2026-11-02 真机·坏 key 回退 | jiuwenswarm 本地服务 + 模型组 test2（GLM-5.3 坏 key） | 会话问答，查 trace / 路由日志 | 第一轮先试默认→401 ERROR span→重试应答；通知下一轮到达；选路日志全程可查 | 无 |
| 2026-11-02 真机·探针隔离 | 服务重启后首个会话 | 对照路由日志 `cooldown_skipped` 与探测日志 | 探测走独立路由（自身冷却 60→120s 递增），共享路由保持干净，第一轮照常先试默认 | 无 |
| 2026-11-01 真机·单模型回归 | 单模型团队会话 | 对照 trace / history / 名册 | 全部既有行为不变，`model_group` / `models_used` 不出现 | DashScope 集成测试因本机无外网失败（基线即如此） |

### 可见行为、副作用与证据有效范围

- 模型可见：`runtime.setting` 当前模型为该次实际模型（占位符填充后）；
- 用户可见：trace 的 `chat <模型>` span 名、ERROR + `error.type` 的失败请求、history
  `context.usage.model` / `deployment`、名册 `models_used` 行；
- 独立读取：`logs/llm.log` 的 `model group route:` 选路行（含冷却跳过）可脱离 trace 独立核对。
- 证据对应 develop 基线 + 本分支；路由行为依赖 intelli_router 包版本（冷却/退避语义在其侧），
  包升级后冷却相关结论需重验。

## 验证结论

- 已证明范围：四条链路的命名在正常/回退/失败路径上一致；通知双路投递与去重；探针隔离；
  单模型路径无回归（单测 + 真机）。
- 未覆盖或未通过的承诺：三个"知情盲区"为设计取舍而非缺陷（见已知遗留）；DashScope 集成
  测试因环境无外网未执行。
- 可支持的交付判断：满足 #5985 的验收范围（模型组下实际模型全程可见、`*` 不再用户可感知）。

### 覆盖与资料同步

已盘点/实现/检视/验证：模型客户端、观测 handler、reliability 三件套、stream_controller、
名册渲染、探针隔离及对应测试；本次已同步 S_09 / S_14 / S_19。未同步：features 索引——
同步工具当前会对约 270 份历史文档回填 frontmatter（批量迁移），为避免无关噪音未在本特性内
执行，建议单独提交文档迁移。

## 已知遗留

| 遗留项 | 未处理原因 / 性质 | 影响与现有处置 | 后续入口 / 条件及所需验证 |
| --- | --- | --- | --- |
| 失败发生轮的回答不知道原因 | 设计取舍（通知物理上只能搭下一条输入） | 该轮回答盲但不出错；trace 有 ERROR 可查 | leader 主动查询实时路由状态（已评估，暂缓） |
| 会话全程落在冷却窗口内则无通知 | 设计取舍（无自身失败事件） | 冷却随连续失败线性增长，窗口会变大；选路日志可查 | 同上 |
| 网络类瞬时故障被路由调用内消化，零通知 | 设计取舍（无异常抛出） | 任何会话不会因此收到通知 | 同上 |
| features 索引未收录本文档 | 同步工具附带批量迁移，独立处理 | 索引暂缺本文档条目 | 单独文档迁移提交后运行 sync_documents.py |
