---
id: B_20261102_1507_symphony-build-method-strict-integer
title: Symphony 构建方式严格整数校验
type: B
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-02
updated: 2026-11-02
modules: [symphony]
files: [openjiuwen/symphony/config.py, tests/unit_tests/symphony/test_config.py]
designs: [D-20261031-1614-symphony-config-contract]
---

# Symphony 构建方式严格整数校验

## 现象与影响

基线 7f0c29ca9fa81461eeb4543cf56b57d7959d01f3 的配置检视中，IndexBuildConfig.model_validate({"method": 4.0}) 接受浮点数并输出整数 4。ResolvedIndexBuildConfig 采用相同 Literal[4] 类型。其行为不符合配置规约中“整数参数拒绝浮点数”的约束，严重度 P3；目标配置尚未接入运行链路，未认定为生产回归。

## 关联设计与当前契约

用户在 2026-11-02 的配置收敛任务中明确批准修复，同时要求默认值保持不变、暂不改运行链路。依据 [配置先行设计](../../designs/active/D-20261031-1614-symphony-config-contract/design.md) 和 [配置规约](../../specs/symphony/S_01_configuration-contract.md)。这是同一配置特性中的约束修复，未新增运行能力。

## 根因与修复决策

Literal[4] 的匹配允许数值等价的 4.0，模型级 strict=True 没有消除这一字面量匹配行为。仅检查最终输出为整数不能证明输入类型正确。

两个字段共用 TreeBuildMethod：在 BeforeValidator 中先拒绝 bool 和非 int 输入，再由 Literal[4] 限定编码。抛出 ValueError 由 Pydantic 形成 ValidationError，错误仍定位到 method；JSON Schema 保留 type=integer、const=4。IndexBuildConfig 默认仍为 4，ResolvedIndexBuildConfig 仍要求显式提供 method。

没有删除字段或改成宽泛 int：前者超出保持配置形态的修复范围，后者会丢失固定编码约束；也不依赖模型级 strict 设置作为整数字面量的唯一输入校验。

## 回归证据与适用范围

候选为上述基线加本轮 config.py / test_config.py 工作区差异。在 Python 3.11.15、pytest 9.0.2 的既有虚拟环境执行配置测试文件，2026-11-02 结果为 220 passed、2 个既有 Pydantic 弃用警告。

针对两个模型，test_build_method_rejects_non_integer_and_unsupported_codes 分别从字典和 JSON 入口验证 4.0、字符串 4、True、False、None 和编码 5 均被拒绝，错误位置为 method。原有默认值对照和全部模型往返验证整数编码 4 继续有效；Schema 验证保留 integer/const=4。

完整命令和特性范围见 [开发记录](../../features/F_20261031_1614_symphony-config-contract.md)。未调用真实模型或运行链路，未运行格式化或静态检查。

## 防错模式与后续

已在本模块规约中明确整数字面量先检查类型，再检查允许值；不将该修复扩大为全仓类型校验重构。后续修改 method 类型、校验器或 Pydantic 依赖版本时，应复查字典/JSON 两种输入入口及 Schema。运行消费者迁移仍属于后续阶段。


## 2026-11-02 目标字段退役后的适用范围

后续经用户确认的[配置收敛方案第 10 节](../../designs/active/D-20261031-1614-symphony-config-contract/design.md) 删除 IndexBuildConfig；此前 ResolvedIndexBuildConfig 已作为解析镜像删除。因此目标 config.py 不再包含 method、TreeBuildMethod 或 _strict_integer，原字典/JSON 整数字面量回归随字段退役，不继续作为当前配置的验收要求。

上述现象、根因、修复决策及 220 项历史测试证据保持原文，说明该字段存在时的正确校验方式。当前公共构建入口拒绝 method 作为未知字段，真实业务工作流的 BuildMethod 及校验行为没有改动。最新实施与验证见[开发记录的进一步收敛章节](../../features/F_20261031_1614_symphony-config-contract.md)，当前契约以配置规约为准。
