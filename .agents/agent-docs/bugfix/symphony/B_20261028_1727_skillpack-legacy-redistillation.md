---
id: B_20261028_1727_skillpack-legacy-redistillation
title: 升级普通成员校验后，旧配方阻止合格新证据生成候选
type: B
author: 霆龙(吴建宇)
author_email: wujianyu@xiaohongshu.com
modules:
  - openjiuwen/symphony/flow
files:
  - openjiuwen/symphony/flow/engine.py
  - tests/unit_tests/symphony/test_skillpack_member_types.py
designs:
  - symphony-design-docs:SDD-0013
---

# 升级普通成员校验后，旧配方阻止合格新证据生成候选

## 依据与范围

- 日期：2026-10-28。
- 关联设计：[symphony-design-docs](https://gitcode.com/lin-xiaoyu/symphony-design-docs) 的 SDD-0013 v9，路径 `proposals/SDD-0013-jiuwenswarm-skill-pack-productization/design.md`；修复其中普通成员限制的升级兼容问题。
- 范围：`openjiuwen/symphony/flow/engine.py` 的旧配方复用和蒸馏输入指纹；不修改 Runtime、宿主身份模型或安装流程。
- 规约：[SkillPack 成员与根文档规约](../../specs/symphony/S_01_skillpack-members.md)。本次恢复其“旧无类型证据不参与、新真实证据可重新采集”的既定契约，不放宽普通成员限制。
- 影响：已有 verified 配方的用户升级后，同一流程重新执行仍无法生成可用 SkillPack；保留被过滤的历史证据时还会重复触发无变化蒸馏。

## 现象与根因

旧 Flow 先从三条未记录 `skill_type` 的成功证据生成 verified 配方。切换到 `SkillPackAdapter` 后，再加入相同流程的三条或四条可信普通成员证据，配方类型仍为空，`list_candidates()` 持续返回空。

新类型检查正确过滤了旧证据，但“结构未变”的优化仅比较节点、边、计数和判级。它复用旧 `combination_structure`，或只刷新质量及来源；新证据里的可信类型没有进入新版本。候选读取因此一直拒绝这个旧配方。

另一处不一致是：蒸馏结束保存了过滤后证据的指纹，是否有新证据的判定却比较全部已接收证据。只要旧无类型记录仍在存储中，处理完成后仍会被判为有变化。

## 修复决策

1. 只有既有配方满足当前 adapter 的成员约束时，才允许进入同结构复用。否则沿已有建版流程，仅用本轮合格证据重建配方；内容中的类型发生变化，生成新的不可变版本，旧版本文件保持不变。
2. `min_successes` 和统计仍只计算过滤后的合格证据。阈值设为三条时，两条新证据只能形成草稿，三条才可产生候选，不借用旧记录凑数。
3. 读取本轮输入时计算完整证据快照的指纹，完成时保存该值。过滤决定证据是否可参与沉淀，指纹表示这批输入已经被处理，两者分开。异步构建期间新到达的证据不属于该快照，仍留给后续蒸馏。
4. 未配置 `SkillPackAdapter` 的通用 Flow 继续允许原有无类型证据，同结构统计更新不会额外创建版本。

## 未采用的做法

- 不给旧记录回填 `skill_type=skill`：旧记录不能证明成员是普通 Skill，回填会绕过新门槛。
- 不改写旧版本或直接给旧配方补类型：类型必须来自新真实证据，新内容应遵守既有版本机制。
- 不在蒸馏结束时重新读取全库并保存指纹：这样会把等待期间刚到达、尚未参与本轮计算的证据错误标记为已处理。
- 不删除被过滤历史：历史可继续审计，是否允许生成 SkillPack 由当前 adapter 判定。

## 实际验证

2026-10-28，在未修复代码上新增升级与指纹测试，`test_skillpack_member_types.py` 得到 **5 失败、12 通过**：三种新证据数量下旧版本未更新、被过滤历史导致指纹不一致，以及处理并发新增后仍残留指纹差异。

修复后，在当前 Core 源码和既有只读 Python 3.11 测试环境运行以下命令，**138 通过，10.08 秒**；两条警告是既有 Pydantic 配置弃用。未运行格式化或静态检查。

```bash
PYTHONPATH=/Users/wujianyu/Documents/layshang/Projects/skillpack-productization/work/agent-core /Users/wujianyu/Documents/layshang/Projects/symphony-integration/work/.venv/bin/python -m pytest tests/unit_tests/symphony/test_capability_flow.py tests/unit_tests/symphony/test_capability_flow_observation.py tests/unit_tests/symphony/test_skillpack_member_types.py tests/unit_tests/symphony/test_graph_observation.py tests/unit_tests/symphony/test_orchestration_runtime.py -o addopts='' -q
```

新增七个用例覆盖：新证据 2/3/4 条的阈值及新版本、旧不可变文件逐字节保留、过滤历史后的重启无重复蒸馏、无 adapter 的原有版本/计数行为，以及两种 adapter 下异步构建期间新增证据仍待处理。原普通成员拒绝、Runtime 副本、图观测及编排测试同时通过。

## 验证边界

本次使用临时真实 Flow 存储验证升级与重启，不修改演示数据。没有重新运行真实 WorkSwarm/Hub 演示；该链路先前的验收证据不替代新打包依赖的验证。宿主仍须提供可信成员类别并在确认安装前重验当前状态。
