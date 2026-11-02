---
id: S_01_configuration-contract
title: Symphony 目标配置模型规约
type: S
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-10-31
updated: 2026-11-02
modules: [symphony]
files: [openjiuwen/symphony/config.py, tests/unit_tests/symphony/test_config.py, scripts/export_symphony_config.py, tests/unit_tests/symphony/test_config_export.py, openjiuwen/symphony/config.tree.txt, openjiuwen/symphony/AGENTS.md, openjiuwen/symphony/README.md]
designs: [D-20261031-1614-symphony-config-contract]
---

# Symphony 目标配置模型规约

## 1. 当前边界

`openjiuwen.symphony.config` 提供已实现的纯配置模型，是后续 Runtime 接入的目标契约。当前 Runtime、引擎和 Swarm 仍使用原有定义，包根 `openjiuwen.symphony.__init__` 未重导出新模型。

本规约只承诺配置构造、校验、数据往返和 Schema 元数据。目录有效性、扫描、模型调用、构图、执行权限和产物发布由后续运行接入负责。配置构造不读取环境、目录、根分类文件或模型后端，不启动服务。

## 2. 统一入口与默认补全

```python
from openjiuwen.symphony.config import SymphonyConfig

config = SymphonyConfig.model_validate({
    "capability": {"root": "./capabilities", "type": "expert"},
    "storage": {"artifact_root": "./.symphony"},
    "retrieval": {
        "build": {"request": {"max_concurrency": 4}},
        "search": {"top_k": 5, "traversal": {"max_parallel_branches": 1}},
    },
    "flow": {"acceptance": {"min_successes": 2}},
})
restored = SymphonyConfig.model_validate_json(config.model_dump_json())
schema = SymphonyConfig.model_json_schema()
```

仅 capability.root、capability.type、storage.artifact_root 必填；所有策略节点通过独立默认工厂补全，支持只覆盖某个深层字段。

| 公共分组 | 子配置与职责 |
|---|---|
| capability | root、type、scan（深度/预算/解析）、fingerprint（extraction/evaluation/cache） |
| storage | artifact_root |
| retrieval | enabled、build、search、discovery、toolkit |
| retrieval.build | structure、dynamic（重平衡/扩展倍率）、request、postprocess、equivalence、profiles、prompt、incremental、task、output |
| retrieval.search | top_k、traversal、render、generation |
| orchestration | enabled、min_edge_confidence、build（max_concurrency/matching）、plan |
| flow | enabled、acceptance、narrative |
| experience | build（clustering/execution/patterns） |

retrieval 和 orchestration 可独立禁用，两者同时关闭合法。Flow 的 enabled 默认 true 沿用独立模块配置，不表示 Runtime 已接入 Flow 或自动启动后台任务。Experience 只声明构建策略，不新增启用开关或隐式构建行为。

## 3. 单一可写路径与内部边界

以下是目标配置旧路径的替代关系；旧路径按未知字段拒绝，不提供别名，不影响仍使用旧定义的生产消费者。

| 原目标路径 | 当前路径 |
|---|---|
| retrieval.build.max_depth | retrieval.build.structure.max_depth |
| retrieval.build.branching_factor | retrieval.build.structure.branching_factor |
| retrieval.build.max_concurrency | retrieval.build.request.max_concurrency |
| retrieval.build.request_timeout_seconds | retrieval.build.request.timeout_seconds |
| retrieval.search.max_parallel_branches | retrieval.search.traversal.max_parallel_branches |
| retrieval.search.request_timeout_seconds | retrieval.search.generation.request_timeout_seconds |
| orchestration.build.extraction_mode | capability.fingerprint.extraction.llm_enabled；metadata 对应 false，llm 对应 true |
| orchestration.build.require_consensus | orchestration.build.matching.require_consensus |

构图整体并发只有 orchestration.build.max_concurrency 一处配置；matching 不再包含另一处并发。指纹阶段上限属于 capability.fingerprint.extraction.max_concurrency，后续运行接入时参与构图取阶段与整体上限的较小值。关系阈值仅位于 orchestration.min_edge_confidence，plan 不再重复接收该值。

构建失败策略使用 retrieval.build.task.preserve_previous_index_on_failure / strict_failure / allow_fallback_tree；产物展示使用 retrieval.build.output.generate_tree_html / verbose / show_tree / display_capability_root。回退树开关不放在 output，也不与失败保留旧索引开关合并。

retrieval.build.dynamic 只包含重平衡间隔和扩展倍率，结构唯一来源是 retrieval.build.structure；目标模型不再保存第二份结构或六个只读阈值属性。阈值计算与根分类归一化留给后续运行层，不在配置构造时执行。

FingerprintPolicyConfig、GraphMatchingPolicyConfig、TreePromptPolicyConfig 提供用户策略。指纹协议参数独立保留为 FingerprintProtocolConfig；提示指纹版本仅由原业务模块维护，公共树不接收这些协议字段。目标模块不再提供 FingerprintConfig、GraphPlanningConfig、GraphMatchingConfig、CapabilityIndexBuildConfig 等重复组合，默认值验证直接对照公共节点与旧实现。

构建和检索的同义目标模型已经退役，不提供别名：

| 退役目标模型 | 当前策略位置 |
|---|---|
| TreeRequestConfig | IndexRequestConfig 直接定义完整字段，使用 retrieval.build.request |
| TreeStructureConfig / IndexTreeStructureConfig | RetrievalTreeStructureConfig 直接定义完整字段，使用 retrieval.build.structure |
| TreeBuildConfig / IndexTreeBuildConfig / TreeManagerConfig | RetrievalBuildConfig 的策略组合 |
| TreePromptConfig | TreePromptPolicyConfig 的用户策略；协议版本由原业务模块维护 |
| IndexBuildConfig / IndexArtifactConfig / TreeBuildOptions | retrieval.build 的 structure/dynamic/request/task/output 等唯一配置位置 |
| ProgressiveRetrieverConfig / RetrieverConfig | RetrievalSearchConfig；模型实例独立注入 |
| DisclosureConfig | AgenticRenderConfig 直接复用 RetrievalRenderConfig 字段并保留工具浏览默认值 |

目标 config.py 不再镜像 TreeBuildSettings、ResolvedIndexTreeStructureConfig、ResolvedIndexBuildConfig、RuntimeRetrieverConfig。真实解析设置分别由 retrieval/build/tree/context.py、retrieval/build/workflows/artifacts.py、retrieval/search/service/models.py 维护，当前运行消费者不变。协议、独立存储、一次性构建、模型后端、生成约束、单次调用及评分截断配置继续保留，不再因旧入口默认值不同而声明同义策略；模型连接由未来 SymphonyRuntime(model, config) 的 model 参数承接，此构造入口尚未实现。

PUBLIC_CONFIG_MODELS 登记根 Schema 可达的 48 个模型；INTERNAL_CONFIG_MODELS 登记其余 15 个，CONFIG_MODELS 合计 63 个。JsonValue 是根分类数据使用的递归 JSON 类型，不属于配置模型登记。

config 模块的 __all__ 仅包含 CapabilityType 和 11 个核心配置类：SymphonyConfig、CapabilityConfig、StorageConfig、RetrievalConfig、RetrievalBuildConfig、RetrievalSearchConfig、OrchestrationConfig、OrchestrationBuildConfig、OrchestrationPlanConfig、FlowConfig、ExperienceConfig。登记表、基类、后端和其他子模型不进入便利导出；工具仍可显式访问模块属性。包根导出不变。

## 4. 数据、校验与默认值

- 未知字段、基础类型错误、越界值、非法枚举产生 Pydantic ValidationError，定位包含完整嵌套路径。禁用节点仍须通过校验。
- 整数拒绝 bool、数字字符串和浮点数；数值拒绝 NaN / Infinity。目标构建不再接收 method 字段；TreeBuildMethod 和专用字面量校验函数一并退役，原业务工作流的 BuildMethod 不变。
- 路径接受非空文本或 pathlib 纯路径并保存文本，不 expanduser、resolve 或创建目录。根分类数据可以包含 JSON 值，输入文件路径也不在配置阶段读取。
- capability.type 枚举为 skill/expert/connector/mcp，表示目标格式范围，不表示对应运行读取器已经接入。通用字段名不含 skill；既有工具标识与优先对象 ID 保持原字符串。
- 配置不包含客户端、执行器、线程池、回调、扫描结果或缓存句柄；独立后端额外选项只接受可序列化 JSON 值。
- 属性赋值默认冻结；retrieval.build.dynamic 与 experience.build 保留既有可调整需求并校验赋值；前者只允许调整重平衡和扩展策略。列表/字典不是深层冻结，所有实例的默认容器和子模型独立。
- 所有合法配置通过 model_dump(mode="json") / model_validate 和 model_dump_json / model_validate_json 往返；派生属性不作为重复可写字段导出。

公共树构建沿用索引入口默认：深度 6、分支 128（至少 2）、并发 2、请求超时 420 秒、分类批量 32。真实底层树入口仍为并发 1、超时 180 秒、批量 20、分支 8，由原业务模块维护；目标 config.py 不再镜像这组不同默认值。公共检索保留 top_k=10、并行分支 3、超时 120 秒；复用分支选择配置后，该超时还接受 None，表示沿用后端策略。

新增公共动态树参数沿用原目标模型：rebalance_interval=50，expansion 的 max_capabilities_per_node=1.5、expand_threshold=0.7、early_stop=1.7、lazy_split=1.3、classification_batch=6.0、structure_sample=12.0。task.allow_fallback_tree=True 沿用原 IndexArtifactConfig；output.verbose=False、show_tree=True、display_capability_root=None 沿用原 TreeBuildOptions。show_tree 与 generate_tree_html 是不同的展示开关，原高层工作流的 show_tree=False 显式覆盖没有修改，后续运行接入仍需处理该入口差异。

自然语言检索与工具浏览保留各自的 top_k、展示和请求预算；前者展示深度 99，后者展示深度 1、阈值 12、空码表且不展平。LLMClientConfig 后端联合类型独立保留，可通过 Pydantic TypeAdapter 选择并序列化后端；不为后端验证重新创建检索包装。

图构建并发默认 1、指纹提取阶段上限默认 4、关系共识默认 true，Flow/Discovery/Experience 默认值沿用原模块。所有变化仍为目标契约，现有运行默认值、缓存签名和产物格式未改变；新 model_dump 不能直接替换旧缓存签名输入。

## 5. 文档导出与验证入口

每个模型 docstring、字段 title/description 使用中文，Schema 保留约束、枚举和适用示例。default_factory 对应子模型/容器的实际默认值由字段元数据读取；不能把 Schema 缺少 default 当成必填。

在已有 Python 环境运行 `python scripts/export_symphony_config.py`，直接加载纯配置文件生成 openjiuwen/symphony/config.tree.txt；默认只包含完整的 SymphonyConfig 用户配置树。--include-internal 显式追加保留模型参考，默认写入同目录 config.reference.txt，不覆盖用户配置树。两种模式均支持 --output 指定位置，不导入 SDK 包根或运行模块。每项包含 # 中文说明、默认值/必填及枚举；长列表展示数量，派生属性不列为输入，依赖前序输入的工厂不被无输入求值。

配置树禁止手改。按模块 AGENTS.md，每次修改模型、默认值、说明、校验、登记或导出脚本后，交付前自动生成；相同内容不重写，不含时间戳，不安装 watcher/Git hook，也不改变 Runtime 生命周期。

验证入口为 tests/unit_tests/symphony/test_config.py 与 test_config_export.py，覆盖全部模型的元数据、默认值与往返，以及统一入口的覆盖/错误路径/容器隔离、生成文件一致性和仓库外独立导出。实际证据见 [开发记录](../../features/F_20261031_1614_symphony-config-contract.md)，已确认方案及后续清理范围见 [配置先行设计第 8—10 节](../../designs/active/D-20261031-1614-symphony-config-contract/design.md)。
