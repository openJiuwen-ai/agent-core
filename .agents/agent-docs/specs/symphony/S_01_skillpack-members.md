---
id: S_01_skillpack-members
title: SkillPack 成员与根文档规约
type: S
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
designs:
  - symphony-design-docs:SDD-0013
---

# SkillPack 成员与根文档规约

## 元信息

- 关联模块：`openjiuwen/symphony/flow/`。
- 最近修订：2026-10-28。
- 设计依据：[symphony-design-docs](https://gitcode.com/lin-xiaoyu/symphony-design-docs) 的 SDD-0013 v9 §4.2.1、§4.3.5，路径 `proposals/SDD-0013-jiuwenswarm-skill-pack-productization/design.md`。
- 实施记录：[F_20261026_2032_skillpack-members](../../features/symphony/F_20261026_2032_skillpack-members.md)。

## 范围与数据

本规约适用于给 `SymphonyFlowEngine` 配置 `SkillPackAdapter` 的宿主。其他产物适配器继续使用原有通用能力流程。
宿主负责从真实执行记录取得节点，并根据其权威库存提供 `metadata.skill_type`；Core 不访问宿主目录和用户身份。

`capability_type=skill` 表示能力大类，`skill_type=skill` 表示普通单 Agent Skill。
`skill_type` 经 `normalize_execution_graph`、配方蒸馏和 `CapabilityPackager` 原样保留；缺失时不推断普通类型。
`SymphonyRuntime` 提交图观测时只在副本中去除该宿主注解，保持图观测的严格字段契约；随后向 Flow 提交的原执行图必须保留类型，不能因此绕过普通成员检查。其他未知字段仍由图观测校验拒绝。

## 不变量

1. SkillPack 至少包含两个普通 Skill。SwarmSkill、嵌套 Pack、Tool、Agent 和未知类型不能作为成员。
2. 类型检查同时覆盖证据入库、历史证据蒸馏、候选读取、安装准备与直接调用渲染器。
3. 根名称不能与成员名称相同；冲突时追加 `-pack`。根 frontmatter 使用 `kind: skillpack` 和按执行链排序的 `skills`。
4. Core 只在受控临时产物目录生成根 `SKILL.md`。宿主负责确认、安装及安装前再次读取成员状态。

## 接口与失败

- `SkillPackAdapter.validate_members(structure)`：检查节点 metadata，失败抛出 `SkillPackNotInstallableError`。
- `SymphonyFlowEngine.ingest`：类型不支持时返回 `False`，不把记录加入 Flow 证据文件。
- `get_candidate/list_candidates`：不返回类型不满足要求的历史配方。
- `review_and_prepare_install`：不合格成员返回 `rejected`，不生成可安装产物。
- `SkillPackAdapter.render`：还要求简单线性链，保留已有分支/环路拒绝规则及正文固定段落。

历史缺少类型的记录仍保留在原存储中，但不能成为 SkillPack。宿主应使用真实记录重新采集，而非回填推断类型。
类型契约与宿主 Tool 必须使用相配的 Core 版本；宿主不能因 SDK 缺失检查能力而默许类型未知的候选。

## 验证入口

`tests/unit_tests/symphony/test_skillpack_member_types.py` 覆盖类型拒绝、历史记录与全链路保留；
`test_capability_flow.py` 和 `test_capability_flow_observation.py` 覆盖通用流程回归。
