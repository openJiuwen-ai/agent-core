---
id: F_20261031_1614_symphony-config-contract
title: Symphony 多层 Pydantic 配置契约
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-10-31
updated: 2026-10-31
modules: [symphony]
files: [openjiuwen/symphony/config.py, tests/unit_tests/symphony/test_config.py]
designs: [D-20261031-1614-symphony-config-contract]
---

# Symphony 多层 Pydantic 配置契约

## 关联依据与范围

- 已确认设计：[D-20261031-1614-symphony-config-contract](../designs/active/D-20261031-1614-symphony-config-contract/design.md)。
- 当前规约：[S_01_configuration-contract](../specs/symphony/S_01_configuration-contract.md)。
- 比较基线：`e089198835426cd7681761b0cc278670cd83031f`，develop 工作区；本次新增两个代码/测试文件。
- 用户明确要求配置先行，Runtime、引擎、Swarm、公共导出、依赖和缓存逻辑暂不接入。

## 实际实现与决定

新增 `openjiuwen/symphony/config.py`，包含 79 个具体 Pydantic 配置模型和显式 CONFIG_MODELS 注册表。公共 SymphonyConfig 使用 capability/storage/retrieval/orchestration 四组；内部同类设置按请求、结构、后处理、画像、增量、后端等职责分组。

默认值分别对照 toolkit、树构建、工作流、指纹、规划、Flow、Discovery、Experience 和模型后端的现有配置。存在实际差异的配置族分别保留。两种原 GenerationConfig 分别表达为 LLMGenerationConfig 和 RetrievalGenerationConfig；原扁平编排设置由 GraphPlanningConfig 保留，新的 OrchestrationConfig 是目标公共聚合配置。

全部字段提供中文标题、说明及约束，适用字段提供示例。模型使用严格类型与未知字段拒绝，支持 Pydantic 原生 Schema 和序列化接口。路径只转为文本，包括根分类的 pathlib.Path，不读取目录或文件。原 tuple 配置在目标 JSON 接口中表达为独立列表，码表顺序不变。

旧 client、llm_openai_client、prompt_cache 等活对象不进入纯配置；对象列表、执行器和回调继续归运行调用参数。模型后端仍提供原有纯参数，但不挂到公共 SymphonyConfig，也不创建客户端。元数据说明显式区分“目标配置可校验”和“Runtime 已接入”。

不同入口默认值没有合并。配置先行允许新旧定义暂时共存，原定义仍是当前运行行为的依据。新配置没有提供旧符号兼容别名、配置文件加载器或缓存签名实现，避免提前改变运行链路。

## 与设计关系

本次实施用户最终确认的配置先行阶段，替代早期草案中“先迁移所有消费者”的执行顺序。新设计单独记录这一已确认范围；原 Runtime 总体草案保留 draft，不将其余运行时重构误记为已批准。

内部根分类输入补齐 pathlib.Path 的纯文本转换；TreeBuildOptions 的模型覆盖默认值保留为 None。列表/字典默认值使用独立工厂；冻结仅限制属性重赋值，不声称深层容器不可变。

## 验证证据

环境：macOS，Python 3.11.15，Pydantic 2.12.5，pytest 9.0.2；复用仓库 `.venv`，不安装依赖。

```bash
source .venv/bin/activate
export PYTHONPATH=.:${PYTHONPATH:-}
python -m pytest tests/unit_tests/symphony/test_config.py -q
```

2026-10-31 最终结果：**202 passed，2 个既有 Pydantic 弃用警告，0.46 秒测试时间**。警告来自现有 connector_pool.py 和 http_client.py 的 class Config 写法，不是新配置模型。

证据覆盖：

- 79 个具体模型逐一完成 JSON 字典和 JSON 文本往返、Schema 生成及中文字段元数据检查。
- 最小配置、独立开关、禁用组仍校验、完整错误路径、非法类型/数值/枚举、JSON 后端扩展参数。
- pathlib 路径文本转换、环境变量不参与默认值、构造不调用文件读取/创建/resolve；测试通过异常替身验证边界。
- 现有多个配置源逐字段比较默认值，原紧凑码表逐项相等，派生预算/阈值/拓扑值符合原规则。
- 新配置不覆盖现有包根 OrchestrationConfig，包根不导出 SymphonyConfig；旧运行入口仍使用原配置。

未运行格式化、静态检查或运行链路测试；未调用真实模型、构建图/树、更新 Swarm pin 或提交代码。

## 检视与资料同步

按 jw-review 进行范围内自检，比较对象为上述基线加当前新增配置及测试文件。检查职责边界、默认值来源、Schema 可导出性、运行对象隔离和公共导出隔离；配置模型和测试的实际验证结果见上文。检视仅覆盖配置阶段，不构成 Runtime 或 Swarm 集成通过结论。

已同步配置先行 D、当前配置模型 S 和本 F，并按文档工具刷新索引。没有改写既有运行规约，也没有把整体 Runtime 草案标记为交付完成。

## 已知后续工作

| 工作 | 当前边界 | 后续要求 |
| --- | --- | --- |
| Runtime 和两种引擎接入 | 未实施，旧配置仍生效 | 按确认的字段映射逐项接入并验证行为、默认值和资源生命周期。 |
| 缓存与持久化迁移 | 新模型没有生成运行签名 | 保护旧签名和产物读取，避免层级或字段改名导致无意失效。 |
| 对象格式与 Swarm 迁移 | 配置枚举仅表达目标类型 | 固定跨仓版本，验证真实读取器与应用接口。 |
| Markdown 配置文档导出 | 已提供中文字段元数据和 Schema | 后续实现导出器，递归解析子模型并处理默认工厂。 |
| 旧配置清理与依赖显式声明 | 本阶段按要求保留 | 消费者迁移时统一处理，不提前删除仍被调用的定义。 |
