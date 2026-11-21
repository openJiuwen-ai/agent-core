---
id: F_20261121_1035_symphony-unified-planning
title: Symphony 统一规划入口
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-21
updated: 2026-11-21
modules: [openjiuwen/symphony]
files: [openjiuwen/symphony/graph_engine.py, openjiuwen/symphony/orchestration/service.py, examples/symphony/runtime.py]
designs: []
design_note: 依据 2026-11-21 当前会话明确确认的统一规划入口方案实施，没有单独创建 D 文档。
---

# Symphony 统一规划入口实施记录

## 依据与决策

用户确认由调用方提供 `type`，Core 集中分流；`plan(type="expert")` 统一返回专家任务图。这部分替代早期按专家能力层输出能力链的接口语义；关系图构建、专家候选证据和技能动态图契约继续有效。当前契约维护在 [S_02_orchestration-plans](../specs/symphony/S_02_orchestration-plans.md)。

## 实际实现

`OrchestrationService.plan()` 统一选择 `plan_skill_graph()` 或现有 `plan_subagent_graph()`，没有额外的模型分类调用。技能专用方法固定读取 Skill 层，继续支持禁用候选、静态版本和动态图覆盖。专家规划继续使用实时可见专家与 inline 角色，返回 `plan_kind=expert_tasks`。

`SymphonyGraphEngine.plan()` 在运行态检查后只为技能准备观测快照；专家不要求技能图来源可用。Engine 的两个专用方法以固定类型进入统一入口，沿用关闭、取消和在途操作跟踪。未知类型和专家调用携带的技能图约束明确报错，避免读取错误图层或静默丢弃约束。

JiuwenSwarm 统一调用 `runtime.orchestration.plan(type=...)`，继续提供会话模型、manifest 可见范围和技能禁用状态。Core 不引入 Swarm 产品或目录依赖。README 和示例同步介绍三个入口及旧专家接口的行为变化。

## 验证

- Python 3.11.15，Core 基线 `96c8f16b8` 加本次工作区差异。
- 在改实现前运行新增入口用例，确认出现缺少技能入口、专家统一入口错误要求技能图等 7 个预期失败；原有 9 个用例通过。
- `source .venv/bin/activate; export PYTHONPATH=.; python -m pytest tests/unit_tests/symphony -o addopts='' -q --tb=short`：995 项通过。测试的本机 HTTP 回环服务需要允许绑定临时端口。
- 覆盖统一与专用入口、两种规划模式、具名输入、实时专家候选、技能动态图和指定修订、纯 inline 计划、ready/no_plan、异常、取消及运行态拒绝。模型采用可控替身，未调用外部模型服务。
- 下游 Swarm 的 compose 与专家派发相关 91 项测试通过，初次联调使用 `PYTHONPATH` 指向本地 Core，不能作为固定 Git 依赖安装的证据。
- `uv build --wheel --no-build-isolation --offline --python .venv/bin/python --out-dir /tmp/symphony-unified-wheel` 构建成功；使用 `uv --no-config pip install --offline --no-deps --target /tmp/symphony-unified-installed` 将 wheel 安装到隔离目录。核对 Swarm 实际加载 `/tmp/symphony-unified-installed/openjiuwen/symphony/__init__.py`，相同 91 项测试通过。安装包验证与固定 Git 提交安装仍分别记录。
- 暂存区 CodeCheck 首次命中 `G.CTL.03`（专家参数校验的布尔表达式过多）；修复前代码已由现有共享规则检出，没有规则漏检。改为对技能图上下文做 `any(value is not None ...)` 校验，独立只读复查确认行为等价；`python -m pytest tests/unit_tests/symphony/test_expert_task_planning.py -o addopts='' -q --tb=short`：62 项通过。

## 偏差与遗留

实现目标与会话确认方案一致。用户已明确同意将本次 Core 改动提交并推送到 `origin/feature/symphony-config-contract`，随后更新 Swarm 的完整依赖引用，Swarm 保持未提交。发布后的依赖锁定与固定 Git 提交安装结果由 Swarm 的对应实施记录维护。提交和推送本身不触发源码复测；新的安装依赖只做相应接入验证。
