# Swarmflow 工具对齐 Claude Code:提示词集中化 + 接口扩展

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-22 |
| 范围 | `workflow/engine/{facade,primitives,seam,provider}.py`(`agent()` 加 `isolation`/`agent_type`、`parallel`/`pipeline` 单次 ≤4096 校验)、`workflow/tool_swarmflow.py`(工具参数加 `script`/`name`/`resume_id` + invoke 四选一校验)、`tools/locales/{cn,en}.py`(参数串)、`tools/locales/descs/{cn,en}/swarmflow.md`(对标 CC 重写工具描述)、`prompts/{cn,en}/leader_swarmflow.md`(删除)、`prompts/sections.py` + `rails/{team_policy_rail,elements}.py` + `agent/agent_configurator.py`(移除 swarmflow section 链)、文档 `workflow/AGENTS.md` + `S_18` |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/` 83 passed(含新增 `test_agent_accepts_isolation_and_agent_type` / `test_parallel|pipeline_rejects_fan_out_beyond_cap` / `test_swarmflow_tool_rejects_unsupported_sources`,移除 section 断言);section 波及的 prompts/rails/agent 单测 63 passed |
| Refs | #1047 |

## 背景

参考两份 Claude Code(CC)Workflow 工具分析文档,对标其"一切编排指引内嵌工具 `description`"的设计,优化 swarmflow 特性工具。调研发现三个问题:

1. **分工差异**:CC 里调用 Workflow 的模型自己现写脚本(内联 `script` 参数),故其 ~150 行 `description` 几乎全是"如何编写编排脚本"的指引。swarmflow 工具当前只接受 `script_path`,Leader 是纯旁观者不写脚本。
2. **提示词分散且不完整**:swarmflow 相关提示词散落三处——工具描述(仅 12 行)、系统提示词 section `leader_swarmflow.md`(23 行)、原语 docstring(每个仅一行"Delegates to..")。相比 CC 把一切塞进一个 description,零散且缺"如何写脚本"。
3. **接口未对齐**:CC 有 `script`/`name`/`resumeFromRunId` 多脚本来源、`agent(isolation/agentType)`、budget 硬天花板、单次 fan-out 上限;swarmflow 这些接口缺失或不对齐。

## 决策

1. **提示词集中化**。删除 `leader_swarmflow.md`(cn/en)与其 section 装配链(`build_team_swarmflow_section` / `TeamSectionName.SWARMFLOW` / 聚合函数 `build_team_static_sections` 与 rail 链中**仅为该 section** 传递的 `enable_swarmflow`),把全部编排指引重组进工具描述 `descs/{cn,en}/swarmflow.md`(对标 CC 逐节:定位[扇出/验证/综合] / 何时用不用 + hybrid scout 先侦察后编排 / 常见工作流形态[Understand/Design/Review/Research/Migrate] + 多阶段串联 / 异步旁观契约 / 脚本来源四选一 / 脚本结构 + 返回值语义 / 原语 API[含陷阱] / worker 能跑什么[继承 teammate spec 工具] / pipeline vs parallel + 栅栏 3 场景 + smell test / 确定性约束 + 闭包陷阱 / 并发上限 / schema / budget / resume / 编排模式库 + 模式非穷举自造 / 代码骨架×4 / 确定性控制流用途)。对齐 CC「内嵌 description」设计。

2. **接口对齐 CC + 执行留空 + 诚实标注**。
   - 工具参数加 `script`(内联)/`name`(具名)/`resume_id`(=run_id 续跑),保留 `script_path`/`args`;`invoke` 四选一校验,对未实现来源返回明确 "not supported yet" 错误(非静默无操作)。
   - `agent()` 原语加 `isolation='worktree'` / `agent_type`(facade/primitives/seam/provider 全链 + `_ENGINE_OPTIONS` 白名单),透传至 backend 处留 TODO(暂不改变执行)。`call_signature` 只取 `label/phase/model`,二者不入签名,故 worker resume 零回归。
   - 工具描述完整描述这些能力 + "Availability" 状态标注(`script_path` 可用,其余实现推进中);budget 按硬天花板写(对齐 CC,标注实现推进中);resume 按 `resume_id`=run_id 写(对齐 CC runId,无 CLI 参数)。

3. **唯一真实现的执行能力**:`parallel`/`pipeline` 入口校验单次 fan-out ≤ `_MAX_FANOUT`(4096),超出抛 `WorkflowError`(对齐 CC「显式报错而非静默截断」),其余 CC 对齐能力执行层留空。

4. **保留 `enable_swarmflow` 工具注入链**:`spec.enable_swarmflow`(blueprint)→ `agent_configurator` 在 leader 时建 worker-model resolver → resolver 非 None 才注入 `swarmflow` 工具。这条链与 section 无关,完整保留(用户 API 不变);本次只清理它在 section 路径(`elements.py` → `TeamPolicyRail` → `build_team_static_sections`)的传递。

## 拒绝的方案

- **保留 section、只补工具描述**:违背用户"全放工具描述、对齐 CC"的诉求,且两处提示词(section + 描述)语义重叠、维护双份。
- **纯按 CC 写描述、不标注实现状态**:会让模型调用 `script`/`name`/`resume_id`/`isolation` 等未接通执行的能力却得不到预期行为(静默失败)。改为完整描述 + Availability 标注 + invoke 明确报错,既对齐表面又诚实。
- **把 isolation/agent_type 执行也一并做掉**:worktree 隔离与具名专家 agent 解析涉及 backend 与团队装配的较大改动,本轮范围是"接口 + 提示词就位",执行留空、后续对齐。
- **删除 `enable_swarmflow` spec 字段**:它仍是工具注入开关,删除破坏用户 API;只清理它在 section 路径的死传递。

## 验证

- 引擎:`agent(..., isolation='worktree', agent_type='Explore')` 经 MockBackend 跑通(参数被接受、不报错);`parallel`/`pipeline` 传 4097 项抛 `WorkflowError`,4096 通过。
- 工具:`invoke` 仅给 `script_path` 通过;只给 `script`/`name`/`resume_id` 返回 "not supported yet";四者全缺返回 "one of ... is required";并发冲突仍拦截。
- 描述:cn/en 经 `translator("swarmflow")` 原样加载(不传 kwargs 即不经 `PromptTemplate.format`,代码块花括号完整),含 isolation/resume_id/4096 等关键节;cn 6097 / en 10433 字符。
- section 移除:`build_team_static_sections(LEADER)` 产出 `[team_role, team_workflow, team_lifecycle]`(无 `team_swarmflow`),装配不报错;prompts/rails/agent 单测 63 passed。

## 已知遗留

- `script`(内联)/`name`(具名注册表)/`resume_id`(续跑)的**执行层**未实现:`invoke` 接收并明确拒绝,待异步工具执行框架接通(`resume_id` = `run_background` 已生成的 run_id)。
- `agent(isolation='worktree')` / `agent_type` **执行层**未实现:参数透传至 backend 但不生效,`call_signature` 暂不纳入二者(将来真实现、若影响结果需纳入签名)。
- budget **硬天花板**未实现:当前为计数 + 脚本自检,提示词已按硬天花板描述,待引擎在 `agent()` 调用前加预算拦截。
- `swarmskill-creator` skill 实体在另一仓(`jiuwenclaw/jiuwenswarm/.../skills/swarmskill-creator`),本仓工具描述自包含、仅一句话引用它作补充。
- 原语 docstring(`facade.py`/`primitives.py`)未逐一补全(工具描述已自包含核心),列为后续。
