# 团队协作工具在渐进式工具暴露下保持直达

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-15 |
| 范围 | `rails/team_tool_rail.py` |
| 测试基线 | `tests/unit_tests/agent_teams/rails/test_team_tool_rail.py`（4）、`test_ability_manager_tool_exposure.py`、`test_agent_configurator.py`、`agent_teams/tools` 全套通过 |
| 关联 | `S_08`（不变量 29）、`F_113`（swarmflow worker 渐进式暴露） |
| Refs | #1435 |

## 背景

`F_113` 给 swarmflow worker 开了渐进式工具暴露（`TeamAgentSpec.progressive_tool`），worker
"是一个没有团队工具的 teammate"，所以当时只需保证 `structured_output` 不被延迟。宿主平台
（jiuwenswarm）现在让 leader / teammate 也跟随其单 Agent 的 `progressive_tool_enabled` 开关，
把 `ProgressiveToolSpec(enabled=True)` 写进成员基 spec——成员于是挂上 `ProgressiveToolRail`，
而 `AbilityManager._apply_tool_exposure_policy` 对**未声明 exposure 且不在
`_DEFAULT_DIRECT_TOOL_NAMES`** 的卡片一律判 `DEFERRED`。团队工具（`send_message` /
`create_task` / `view_task` / `spawn_*` / `claim_task` …）全部中招：leader 每次要协调成员都得先
`tool_search` 再经 `tool_call` 执行，系统提示词与工具描述里以裸名引用的调用顺序全部失效。

## 决策

1. **在 `TeamToolRail.init` 注册前统一声明 `ToolExposure.DIRECT`**（`declare_team_tool_direct`）。
   协作工具是成员的主接口，不是"长尾能力"，与 `_DEFAULT_DIRECT_TOOL_NAMES` 里文件 / 子代理 /
   todo 工具在单 Agent 里的地位等价；一处循环覆盖工厂产出与 rail 追加的 `workspace_meta`。
2. **显式声明优先**：卡片已在构造期声明 `exposure`（`model_fields_set` 含 `exposure`）或已被
   注册解析过（`get_exposure_declared()`）时不改写，保持 `F_113` "explicit wins" 的契约。
3. **不动 `_DEFAULT_DIRECT_TOOL_NAMES`**：那是 harness 级常量，团队工具名单随 dispatch / role /
   capability 变化（`S_08` 不变量 10 / 18），硬编码一份必然漂移。
4. **swarmflow worker 语义不变**：worker 没有团队工具，`TeamAgentSpec.progressive_tool` 仍只决定
   是否往 worker 基 spec 写 `ProgressiveToolSpec`；宿主若在 teammate 基 spec 上已写了该字段，
   worker 经 `_derive_swarmflow_worker_base_spec` 自然继承（字段不覆盖），这是 `F_113`
   "不覆盖该字段"决策的既有结果。

## 拒绝的方案

- **在 40 余个工具类的构造器里逐个写 `exposure=ToolExposure.DIRECT`**：`F_113` 对单个
  `structured_output` 这样做是最小改动；对整个团队工具集合，工厂 / rail 的一次循环更小、
  且新增工具不会漏声明。
- **让宿主平台在 rail 之外事后翻转 exposure**：注册后 `card.id` 已被 `add_ability` 改写成
  `{name}_{owner_id}`，`team.` 前缀不可用作判别；按名单翻转又回到方案一的漂移问题，而且
  `ProgressiveToolRail` 的 BM25 目录在 `finalize_startup` 冻结，事后翻转会留下"目录里是
  deferred、实际 direct"的不一致。
- **把团队工具名并入 `_DEFAULT_DIRECT_TOOL_NAMES`**：见决策 3。

## 验证

- 新增 `tests/unit_tests/agent_teams/rails/test_team_tool_rail.py`：裸卡片被声明 DIRECT、显式
  DEFERRED 不被改写；`AbilityManager.set_tool_exposure_policy(True)` 下 leader / teammate 经
  `TeamToolRail.init` 注册的每个团队工具都是 DIRECT，`uninit` 全部移除。
- `test_ability_manager_tool_exposure.py` / `test_agent_configurator.py` / `agent_teams/tools`
  全套回归通过。

## 已知遗留

- 宿主侧（jiuwenswarm）成员 spec 的开关接线不在本仓库；swarm 分支依赖本改动，合入后需把
  swarm `pyproject.toml` 的 `openjiuwen` rev 推进到包含本提交的 core 版本。
- 外部 CLI / MCP 成员不经 `AbilityManager`，exposure 对它们无意义，本改动不涉及。
