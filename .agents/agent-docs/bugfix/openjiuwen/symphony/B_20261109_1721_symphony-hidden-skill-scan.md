---
id: B_20261109_1721_symphony-hidden-skill-scan
title: Symphony 忽略技能归档和隐藏资产
type: B
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
modules:
- openjiuwen/symphony/shared/fingerprint
files:
- openjiuwen/symphony/shared/fingerprint/scanner.py
- tests/unit_tests/symphony/test_scanner.py
designs: []
design_note: 技能文件夹扫描的局部修复。
---

# Symphony 忽略技能归档和隐藏资产

技能目录中的 `.archive/versions/content/` 可能保存旧版 `SKILL.md`。递归扫描会把这些入口当成活动技能。同 ID 的旧版和现行版会被重复 ID 检查一起丢弃。即使限制了入口扫描深度，归档文件仍可能进入现行技能的资产哈希。

入口发现和资产哈希在读取目录时跳过点号开头的文件及目录。已显式指定的扫描根可以是隐藏目录。单目录扫描上限仍计入遇到的隐藏条目。

在 `f3f8f5761` 基线上复现了浅层与不限深度下的同 ID 冲突。修复后，扫描器的 33 个用例通过，覆盖活动技能保留、隐藏资产变化保持指纹稳定和可见资产变更更新指纹。使用修复版 Core 和替身模型运行 Swarm 图谱构建，刷新前识别出旧归档节点，刷新后只保留活动技能。

Swarm 发布使用该修复需要将锁定的 Core 提交更新到包含此变更的版本。
