---
id: F_20261026_2032_skillpack-members
title: SkillPack 普通成员约束实施记录
type: F
author: 霆龙(吴建宇)
author_email: wujianyu@xiaohongshu.com
modules:
  - openjiuwen/symphony
files:
  - openjiuwen/symphony/flow/distill.py
  - openjiuwen/symphony/flow/engine.py
  - openjiuwen/symphony/flow/packager.py
  - openjiuwen/symphony/flow/render.py
  - openjiuwen/symphony/runtime.py
  - tests/unit_tests/symphony/test_capability_flow.py
  - tests/unit_tests/symphony/test_capability_flow_observation.py
  - tests/unit_tests/symphony/test_skillpack_member_types.py
designs:
  - symphony-design-docs:SDD-0013
---

# SkillPack 普通成员约束实施记录

## 关联依据

- 设计：[symphony-design-docs](https://gitcode.com/lin-xiaoyu/symphony-design-docs) 的 SDD-0013 v9，路径 `proposals/SDD-0013-jiuwenswarm-skill-pack-productization/design.md`。
- 日期：2026-10-26。
- 规约：[S_01_skillpack-members](../../specs/symphony/S_01_skillpack-members.md)。
- 范围：Flow 类型传递、候选和安装准备过滤、根文档渲染，不包含宿主安装或 Hub API。

## 实际实现与决策

已有 `capability_type=skill` 不能区分普通 Skill 与 SwarmSkill。增加宿主提供的 `skill_type`，沿证据、配方和包材料传递；SkillPack 适配器在各入口严格检查普通成员。
没有把缺少类型的历史记录默认为普通 Skill，因为无法从这些记录证明成员类别；记录保留，但不能用于生成新候选。
通用能力适配器保持原有行为，避免把 SkillPack 产品限制扩散到其他 Symphony 产物。

实测发现默认配方名称可能等于最后一个成员名。根名称冲突时增加 `-pack`，避免安装时自引用或覆盖成员目录。

## 实际验证

2026-10-26，使用本地 agent-core 候选源码与 Python 3.11 pytest 环境：

- 最终组合：`python -m pytest tests/unit_tests/symphony/test_capability_flow.py tests/unit_tests/symphony/test_capability_flow_observation.py tests/unit_tests/symphony/test_skillpack_member_types.py -o addopts='' -q`：86 通过，其中新增普通成员约束 10 项、既有流程回归 76 项。两条警告来自既有 Pydantic 配置弃用，与本次改动无关。

覆盖普通成员从证据到根文档、未知/SwarmSkill/嵌套 Pack 拒绝、历史数据过滤、正文段落及名称冲突。
未执行真实模型评审、Hub 服务或最终安装包 E2E；不把上述定向测试视为跨仓发布验收。

## 已知限制

宿主必须提供可信类别并在安装前重验当前成员。JiuwenSwarm 产品化 Tool 依赖本次 Core 契约，需成对交付；旧 pin 不能替代该验收环境。

## 2026-10-27 真实环境集成修复

真实 WorkSwarm 已执行两个普通 Skill，后者读取前者生成的 JSON 文件，边评估返回成功，但 `SymphonyRuntime` 提交图观测出现 `ValidationError`。根因是宿主添加的 `skill_type` 不在通用图观测字段白名单中，导致提交在进入 Flow 前返回。

在 Runtime 的图观测副本中去除这一 Flow 专用注解，原始执行图和 Flow 类型校验保留。未放宽通用观测白名单。新增回归先复现 4 项失败，再验证普通成员入库、SwarmSkill/Pack/未知成员拒绝、其他未知元数据仍拒绝。Flow、Runtime 和图观测组合测试 121 项通过；真实安装包复验继续进行中。

## 2026-10-28 升级兼容修复与合并后复验

提交前检视发现旧无类型配方会阻止合格新证据生成新版本，并导致过滤历史后的指纹持续变化。修复与红绿测试记录见 [B_20261028_1727_skillpack-legacy-redistillation](../../bugfix/symphony/B_20261028_1727_skillpack-legacy-redistillation.md)。旧证据仍不计入阈值，旧版本文件保持不变。

合并 `upstream/develop` 后，在 Core `2c10d96136cf83a3f3c96fa8286329e89d15fe09` 使用当前源码和 Python 3.11 只读测试环境重新运行下列组合：**138 通过，2 条既有 Pydantic 配置弃用警告，7.74 秒**。测试前后工作区干净。此结果覆盖 Flow、类型传递、图观测与编排回归，不替代最终依赖锁或跨仓安装包验证。

```bash
PYTHONPATH=/Users/wujianyu/Documents/layshang/Projects/skillpack-productization/work/agent-core /Users/wujianyu/Documents/layshang/Projects/symphony-integration/work/.venv/bin/python -m pytest tests/unit_tests/symphony/test_capability_flow.py tests/unit_tests/symphony/test_capability_flow_observation.py tests/unit_tests/symphony/test_skillpack_member_types.py tests/unit_tests/symphony/test_graph_observation.py tests/unit_tests/symphony/test_orchestration_runtime.py -o addopts='' -q
```
