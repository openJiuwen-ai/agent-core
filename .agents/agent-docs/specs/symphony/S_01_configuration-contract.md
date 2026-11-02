---
id: S_01_configuration-contract
title: Symphony 目标配置模型规约
type: S
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-10-31
updated: 2026-10-31
modules: [symphony]
files: [openjiuwen/symphony/config.py, tests/unit_tests/symphony/test_config.py]
designs: [D-20261031-1614-symphony-config-contract]
---

# Symphony 目标配置模型规约

## 1. 当前边界

`openjiuwen.symphony.config` 提供已实现的纯配置模型；其定义是后续 Runtime 接入的目标契约。当前 Runtime、引擎和 Swarm 仍使用原有定义，包根 `openjiuwen.symphony.__init__` 未重导出新模型。

本规约只承诺配置构造、校验、数据往返和 Schema 元数据。真实目录有效性、扫描、模型调用、图/树构建、执行权限和产物发布均不属于配置模型的职责。

## 2. 入口与结构

```python
from openjiuwen.symphony.config import SymphonyConfig

config = SymphonyConfig.model_validate({
    "capability": {"root": "./capabilities", "type": "skill"},
    "storage": {"artifact_root": "./.symphony"},
})
schema = SymphonyConfig.model_json_schema()
payload = config.model_dump(mode="json")
restored = SymphonyConfig.model_validate(payload)
```

公共树只包含 capability、storage、retrieval、orchestration；前两组中的 root、type 和 artifact_root 必填。retrieval 按 build/search 分层，orchestration 按 build/plan 分层，两个能力默认都启用。两者同时关闭仍是合法配置。

内部模型独立覆盖扫描、指纹、Flow、Discovery、Experience、树构建、渐进检索、agent toolkit、独立检索服务和模型后端。CONFIG_MODELS 登记所有具体配置模型，支持后续逐模型导出文档。字段、默认值和说明的唯一来源是模型定义；本规约不复制整张字段表。

## 3. 数据与校验不变量

- 未知字段、错误基础类型、越界值、非法枚举和不允许的 null 产生 Pydantic ValidationError。错误位置保留完整嵌套路径。
- 整数参数不接受 bool、数字字符串或浮点数；数值参数拒绝 NaN / Infinity。嵌套能力即使被禁用，也必须通过校验。
- 路径接受非空文本或 pathlib 纯路径并保存为文本；不 expanduser、resolve、检查存在性或创建目录。根分类的文件路径也只保存文本。
- 公共 capability.type 当前枚举为 skill/expert/connector/mcp；这不表示对应运行读取器已接入。内部通用标签字段按所属模块约束处理。
- 模型实例、客户端、执行器、回调和运行缓存句柄不进入配置。后端额外选项只允许可序列化 JSON 值。
- 属性赋值默认冻结。DynamicTreeConfig、TreeBuildSettings、ExperienceBankBuildConfig 保留配置可调整需求并校验赋值；子列表和字典并非深层冻结，各实例的默认容器独立。
- 对合法配置，model_dump(mode="json") / model_validate 与 model_dump_json / model_validate_json 均可往返。派生属性不作为重复可写配置字段导出。

## 4. 默认值与兼容范围

新公共配置沿用已确认的目标选择；内部配置保留原入口有效默认值。例如底层树构建为单并发、180 秒、分类批量 20，索引工作流和 toolkit 为双并发、420 秒、分类批量 32；底层渐进检索候选数为 5，服务和公共入口为 10。

独立指纹提取并发仍为 4，目标公共图构建并发为 1；两者用途不同。对象画像字段改用 capability 命名，已有工具标识字符串和紧凑码表顺序保留原值。

本模块不是旧 dataclass 的直接替换：层级、类型检查和容器表示属于新的配置契约。现有运行默认值、缓存签名和产物格式未由本阶段修改。当前配置签名不能直接替换为新模型的 model_dump，接入阶段必须明确迁移映射。

## 5. 文档与验证入口

每个模型 docstring 与每个字段 title/description 使用中文；Schema 保留约束、枚举和适用示例。default_factory 对应的子模型和容器可通过模型字段元数据获得实际默认值，文档工具不能把 Schema 缺少 default 误判为必填。

验证入口为 `tests/unit_tests/symphony/test_config.py`。实现证据见 [配置契约开发记录](../../features/F_20261031_1614_symphony-config-contract.md)，设计依据见 [配置先行设计](../../designs/active/D-20261031-1614-symphony-config-contract/design.md)。
