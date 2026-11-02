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
        "search": {
            "top_k": 5, "traversal": {"max_parallel_branches": 1},
            "request": {"max_output_tokens": 64, "timeout_seconds": None},
            "selection": {"mode": "generate"},
        },
        "discovery": {"prompt": {"candidate_budget_ratio": 0.02}},
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
| retrieval.search | top_k、traversal、render、request（输出/超时预算）、selection（选择/解码/打分） |
| retrieval.discovery | prompt（候选提示预算/优先对象）、output、use_existing_index |
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
| retrieval.search.request_timeout_seconds | retrieval.search.request.timeout_seconds |
| orchestration.build.extraction_mode | capability.fingerprint.extraction.llm_enabled；metadata 对应 false，llm 对应 true |
| orchestration.build.require_consensus | orchestration.build.matching.require_consensus |
| retrieval.build.task.strict_failure | 删除；回退决策仅由 retrieval.build.task.allow_fallback_tree 反向表达 |
| retrieval.build.dynamic.expansion.max_capabilities_per_node | retrieval.build.dynamic.expansion.max_capabilities_per_node_multiplier |
| retrieval.build.dynamic.expansion.expand_threshold | retrieval.build.dynamic.expansion.expand_threshold_multiplier |
| retrieval.build.dynamic.expansion.early_stop | retrieval.build.dynamic.expansion.early_stop_multiplier |
| retrieval.build.dynamic.expansion.lazy_split | retrieval.build.dynamic.expansion.lazy_split_multiplier |
| retrieval.build.dynamic.expansion.classification_batch | retrieval.build.dynamic.expansion.classification_batch_multiplier |
| retrieval.build.dynamic.expansion.structure_sample | retrieval.build.dynamic.expansion.structure_sample_multiplier |
| experience.build.clustering.capability_cluster_num | experience.build.clustering.query_clusters_per_capability_set |
| retrieval.search.generation 下的 mode / trie_constrained_decoding_enabled / logit | retrieval.search.selection 下的同名字段 |
| retrieval.search.generation.max_tokens | retrieval.search.request.max_output_tokens |
| retrieval.search.generation.request_timeout_seconds | retrieval.search.request.timeout_seconds |
| retrieval.toolkit.request.max_tokens | retrieval.toolkit.request.max_output_tokens |
| retrieval.toolkit.request.request_timeout_seconds | retrieval.toolkit.request.timeout_seconds |
| retrieval.build.prompt.cache_observability | retrieval.build.output.cache_metrics_enabled |
| retrieval.discovery.budget.candidate_budget_ratio | retrieval.discovery.prompt.candidate_budget_ratio |
| retrieval.discovery.budget.context_window_tokens | retrieval.discovery.prompt.context_window_tokens |
| capability.fingerprint.extraction.body_limit | capability.fingerprint.extraction.max_body_chars |
| capability.fingerprint.extraction.max_tokens | capability.fingerprint.extraction.max_output_tokens |
| capability.fingerprint.evaluation.evidence_text_limit | capability.fingerprint.evaluation.max_evidence_chars |
| retrieval.build.profiles.description_limit | retrieval.build.profiles.max_description_chars |
| retrieval.build.profiles.rule_limit | retrieval.build.profiles.max_rule_chars |
| retrieval.build.request.context_window | retrieval.build.request.context_window_tokens |

构图整体并发只有 orchestration.build.max_concurrency 一处配置；matching 不再包含另一处并发。指纹阶段上限属于 capability.fingerprint.extraction.max_concurrency，后续运行接入时参与构图取阶段与整体上限的较小值。关系阈值仅位于 orchestration.min_edge_confidence，plan 不再重复接收该值。

构建失败策略仅使用 retrieval.build.task.preserve_previous_index_on_failure / allow_fallback_tree，均默认 True；strict_failure 已退役，按未知字段拒绝。旧 toolkit 将 allow_fallback_tree 设置为 not strict_failure，目标不再同时接受这两个反向开关。产物展示使用 retrieval.build.output.generate_tree_html / verbose / show_tree / display_capability_root；回退树开关不放在 output，也不与失败保留旧索引开关合并。

后续运行接入须承接原严格模式的前置检查与失败报告：allow_fallback_tree=False 时，缺少构建模型配置应提前失败，构建失败不得生成回退树；True 允许回退，但存在可用旧索引且 preserve_previous_index_on_failure=True 时，优先保护旧索引，不用降级结果覆盖它。两个保留开关的四种组合均可配置；是否存在旧索引、模型配置是否齐备由运行层判断，纯配置不检查模型或文件。当前业务定义和这些运行处理尚未迁移。

retrieval.build.dynamic 只包含重平衡间隔和扩展倍率，结构唯一来源是 retrieval.build.structure；目标模型不再保存第二份结构或六个只读阈值属性。expansion 字段统一以 _multiplier 结尾；lazy_split_multiplier 相对节点容量，其余五项相对分支数，classification_batch_multiplier 不替代 request.classification_batch_limit 的绝对上限。阈值计算与根分类归一化留给后续运行层，不在配置构造时执行。

experience.build.clustering.query_clusters_per_capability_set 表示每个能力集合内成功查询的聚类数，默认 None 使用自适应数量；不是能力类别数，也不是经验库总簇数。字段保留正整数或 None 的严格类型约束，旧 capability_cluster_num 名称不提供兼容别名。

自然语言检索将候选选择策略与请求预算分开：selection 只组合 mode、trie_constrained_decoding_enabled 和 logit；request 只组合 max_output_tokens 与 timeout_seconds。RetrievalSelectionConfig 替代 RetrievalGenerationConfig，后者不再定义或登记。RetrievalRequestBudgetConfig 定义请求预算，AgenticRequestConfig 继承该字段组合并将 timeout_seconds 约束保留为不可空的正数；两种入口的模型实例、覆盖参数和默认工厂仍独立。独立 RetrievalRequestConfig 只表示单次调用 top_k 覆盖，不是请求预算的第二份配置。

缓存观测归 retrieval.build.output.cache_metrics_enabled，默认 True；构建 prompt 只负责确定性和结构发现种子。缓存指标观测与 request.caching=False 的缓存策略开关独立，四种组合均合法，不因关闭缓存策略而关闭后端缓存命中与提示指纹统计。原业务参数仍名为 cache_observability，后续映射时迁移，不在本轮改变运行逻辑。

DiscoveryPromptConfig 直接组合 candidate_budget_ratio、context_window_tokens 和 preferred_capability_ids，删除 DiscoveryBudgetConfig 与 discovery.budget 节点。只读 candidate_budget_tokens 移到 discovery.prompt，计算规则仍为 max(1, int(context_window_tokens * candidate_budget_ratio))，不作为输入字段、序列化字段或 Schema 属性；output 继续独立管理返回内容预算。

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
| RetrievalGenerationConfig | RetrievalSelectionConfig 保留选择策略，RetrievalRequestBudgetConfig 承接请求预算 |
| DiscoveryBudgetConfig | DiscoveryPromptConfig 直接定义预算与优先对象字段 |

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
- 指纹提取 max_output_tokens 接受正整数或 None，默认仍为 2048；None 表示提取调用不传此参数，沿用注入模型配置。独立 FingerprintProtocolConfig 的 extraction_version 对齐 develop 当前协议 symphony-capability-extraction-v3；目标模型仍不生成缓存签名或自动失效运行缓存。
- 检索组合约束：search.selection.mode=logit_selection 且 search.render.compact_codes_enabled=False 时，search.selection.logit.fallback_mode 必须为 generate；error（包括省略时的默认值）被拒绝。错误定位到 retrieval.search.selection，说明指出紧凑编码开关和生成式回退两种修正方式；单独构造 RetrievalSearchConfig 时定位到 selection。Python/JSON 输入、已构造的子模型以及 retrieval.enabled=False 均遵守此约束。generate 模式不额外要求紧凑编码；模型后端是否支持打分仍由后续运行层检查。Schema 与配置树导出中文条件说明，组合合法性通过 Pydantic 配置校验接口验证。

公共策略的计量字段使用明确单位：指纹 max_body_chars=16000、max_evidence_chars=512，画像 max_description_chars=140、max_rule_chars=120，构建 context_window_tokens=0、max_output_tokens=0。改名保留现有正数、画像字符数至少 40、构建 token 预算非负等约束；0 仍表示未显式指定，不与 None 互换。仅统一公共策略命名，独立模型后端、采样和单次调用模型不随之改名。

公共树构建沿用索引入口默认：深度 6、分支 128（至少 2）、并发 2、请求超时 420 秒、分类批量 32。真实底层树入口仍为并发 1、超时 180 秒、批量 20、分支 8，由原业务模块维护；目标 config.py 不再镜像这组不同默认值。公共检索保留 top_k=10、并行分支 3、超时 120 秒；复用分支选择配置后，该超时还接受 None，表示沿用后端策略。

公共动态树参数沿用原目标模型：rebalance_interval=50，expansion 的 max_capabilities_per_node_multiplier=1.5、expand_threshold_multiplier=0.7、early_stop_multiplier=1.7、lazy_split_multiplier=1.3、classification_batch_multiplier=6.0、structure_sample_multiplier=12.0。改名不改变正数约束、默认值或阈值计算基数。task.allow_fallback_tree=True 沿用原 IndexArtifactConfig，也对应旧 toolkit 的 strict_failure=False；output.verbose=False、show_tree=True、display_capability_root=None 沿用原 TreeBuildOptions。show_tree 与 generate_tree_html 是不同的展示开关，原高层工作流的 show_tree=False 显式覆盖没有修改，后续运行接入仍需处理该入口差异。

自然语言检索与工具浏览保留各自的 top_k、展示和请求预算；前者展示深度 99，后者展示深度 1、阈值 12、空码表且不展平。两种入口 request.max_output_tokens 都默认 96、timeout_seconds 都默认 120.0，只有自然语言检索超时接受 None；覆盖其中一个入口不改变另一个。Discovery 的提示比例 0.01、上下文 200000、优先对象列表及派生预算 2000 不变。LLMClientConfig 后端联合类型独立保留，可通过 Pydantic TypeAdapter 选择并序列化后端；不为后端验证重新创建检索包装。

图构建并发默认 1、指纹提取阶段上限默认 4、关系共识默认 true，Flow/Discovery/Experience 默认值沿用原模块。所有变化仍为目标契约，现有运行默认值、缓存签名和产物格式未改变；新 model_dump 不能直接替换旧缓存签名输入。

## 5. 文档导出与验证入口

每个模型 docstring、字段 title/description 使用中文，Schema 保留约束、枚举和适用示例。default_factory 对应子模型/容器的实际默认值由字段元数据读取；不能把 Schema 缺少 default 当成必填。

在已有 Python 环境运行 `python scripts/export_symphony_config.py`，直接加载纯配置文件生成 openjiuwen/symphony/config.tree.txt；默认只包含完整的 SymphonyConfig 用户配置树。--include-internal 显式追加保留模型参考，默认写入同目录 config.reference.txt，不覆盖用户配置树。两种模式均支持 --output 指定位置，不导入 SDK 包根或运行模块。每项包含 # 中文说明、默认值/必填及枚举；长列表展示数量，派生属性不列为输入，依赖前序输入的工厂不被无输入求值。

配置树禁止手改。按模块 AGENTS.md，每次修改模型、默认值、说明、校验、登记或导出脚本后，交付前自动生成；相同内容不重写，不含时间戳，不安装 watcher/Git hook，也不改变 Runtime 生命周期。

验证入口为 tests/unit_tests/symphony/test_config.py 与 test_config_export.py，覆盖全部模型的元数据、默认值与往返，以及统一入口的覆盖/错误路径/容器隔离、检索组合约束、超时可空性差异、独立缓存观测、提示预算派生、退役字段拒绝、生成文件一致性和仓库外独立导出。实际证据见 [开发记录](../../features/F_20261031_1614_symphony-config-contract.md)，已确认方案及后续清理范围见 [配置先行设计第 8—12 节](../../designs/active/D-20261031-1614-symphony-config-contract/design.md)。
