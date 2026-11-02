---
id: D-20261031-1614-symphony-config-contract
title: Symphony 配置契约先行
slug: symphony-config-contract
type: D
status: in-development
stage: active
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
reviewers: []
created: 2026-10-31
updated: 2026-10-31
approved-date: 2026-10-31
domain: core
modules: [symphony]
files: [openjiuwen/symphony/config.py, tests/unit_tests/symphony/test_config.py]
related: [D-20261029-1619-skill-directory-graph-api]
tags: [symphony, configuration, pydantic]
scope: 仅形成分层纯配置模型和配置验证；运行链路迁移留待后续
---

# Symphony 配置契约先行

## 1. 决策归属与确认

本设计记录用户已在会话中确认并明确要求实施的“Symphony 配置先行计划”。它承接 [Runtime 总体草案](../../proposals/D-20261029-1619-skill-directory-graph-api/design.md) 的配置部分，将配置模型和运行链路迁移拆为两个阶段。

2026-10-31，用户先要求多层配置、Pydantic 和中文说明，再明确要求先整理 config 文件，最后以“PLEASE IMPLEMENT THIS PLAN”确认本阶段范围。该确认只覆盖配置契约及验证，不表示整个 Runtime 重构方案已批准。评审者账号未提供，reviewers 不推断为 Git 作者。

## 2. 目标配置

在 `openjiuwen/symphony/config.py` 集中定义全部纯配置，采用 Pydantic v2。文件注明“目标配置，尚未接入运行链路”。公共配置按以下结构组合：

```text
SymphonyConfig
├── capability: root / type
├── storage: artifact_root
├── retrieval: enabled / build / search
└── orchestration: enabled / min_edge_confidence / build / plan
```

不同用途的内部配置继续独立，包括扫描、指纹、Flow、Discovery、Experience、树构建、检索服务和模型后端。同类参数组合为子模型，现有入口的有效默认行为分别保留；Runtime 目标入口的有意默认选择需要中文说明。

纯配置不接收客户端实例、线程池、进度回调、扫描结果或运行缓存句柄。对象根目录和产物根目录保存为文本，不在配置构造阶段扫描、创建或解析目录，也不读取环境变量。模型后端的纯参数不自动挂到公共 SymphonyConfig 上。

## 3. 校验与可导出说明

- 子配置使用独立的默认工厂，拒绝未知字段，校验默认值、数值范围及枚举。
- 采用 Pydantic 原生 model_validate、model_dump 和 model_json_schema。
- 每个模型有中文 docstring，每个字段有中文 title、description，按需包含单位、生效条件和 examples。
- 新配置禁止属性重新赋值；需保留可变性质的内部配置单独声明。嵌套列表、字典不承诺深层冻结，但默认容器不能跨实例共享。
- 字段说明只在模型元数据维护；本阶段不实现 Markdown 文档导出器。

## 4. 阶段边界

本阶段新增目标配置模型和针对性验证。既有定义仍是当前生产行为的依据；不修改 Runtime、引擎、Swarm、公共导出、依赖文件或缓存逻辑。新旧定义暂时并存，不提供自动转换或接入别名。

下一阶段再按已审阅的配置层级更新消费者、保留有效参数和缓存身份、删除旧定义并接入 SymphonyRuntime(model, config)。在实际接入前，不能把新配置校验成功解释为对应对象读取器或运行能力已经实现。

## 5. 验收设计

1. 最小输入补全默认子配置；非法值的错误定位包含完整字段路径。
2. 所有纯配置模型能够导出 JSON Schema，并保留中文说明、约束和示例。
3. 配置能够经 JSON 字典和 JSON 文本往返，各实例不共享可变默认值。
4. 当前配置源的默认值逐项映射到目标位置；不同入口的默认差异得到保护。
5. 构造和序列化不访问文件系统或模型后端，现有公共配置导出仍指向原定义。
6. 验证限于配置契约，不运行格式化或静态检查。实际证据由开发 F 文档记录。
