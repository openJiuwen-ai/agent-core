---
id: S_01_configuration-contract
title: Symphony 目标配置模型规约
type: S
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-10-31
updated: 2026-11-03
modules: [symphony]
files: [openjiuwen/symphony/config.py, tests/unit_tests/symphony/test_config.py, scripts/export_symphony_config.py, tests/unit_tests/symphony/test_config_export.py, openjiuwen/symphony/config.tree.txt, openjiuwen/symphony/AGENTS.md, openjiuwen/symphony/README.md]
designs: [D-20261031-1614-symphony-config-contract, D-20261103-1438-symphony-capability-retirement]
---

# Symphony 目标配置模型规约

## 1. 当前边界

`openjiuwen.symphony.config` 提供纯配置模型，是后续 Runtime 接入的目标契约。当前 Runtime、引擎和 Swarm 仍使用保留业务模块的原定义；包根不重导出新配置，`SymphonyRuntime(model, config)` 尚未实现。

配置构造不扫描目录、不读取环境或根分类文件、不创建模型客户端，也不启动服务。目录有效性、扫描、模型调用、执行权限、缓存签名和发布由运行层处理。

旧模型检索、分支浏览工具及独立经验库连同代码和配置退役；Discovery、构树、指纹、编排、动态观测和 Flow 保留。旧目标配置 `retrieval.search`、`retrieval.toolkit`、`experience` 按未知字段拒绝，不提供兼容别名。历史用户工件不迁移、不删除。

## 2. 统一入口与默认补全

```python
from openjiuwen.symphony.config import SymphonyConfig

config = SymphonyConfig.model_validate({
    "capability": {"root": "./capabilities", "type": "expert"},
    "storage": {"artifact_root": "./.symphony"},
    "retrieval": {
        "build": {"request": {"max_concurrency": 4}},
        "discovery": {"prompt": {"candidate_budget_ratio": 0.02}},
    },
    "flow": {"acceptance": {"min_successes": 2}},
})
restored = SymphonyConfig.model_validate_json(config.model_dump_json())
schema = SymphonyConfig.model_json_schema()
```

仅 capability.root、capability.type、storage.artifact_root 必填；所有策略节点用独立默认工厂补全，可以只覆盖深层字段。

| 公共分组 | 子配置与职责 |
|---|---|
| capability | root、type、scan（深度/预算/解析）、fingerprint（extraction/evaluation/cache） |
| storage | artifact_root |
| retrieval | enabled、build、discovery |
| retrieval.build | structure、dynamic（重平衡/扩展倍率）、request、postprocess、equivalence、profiles、prompt、incremental、task、output |
| retrieval.discovery | prompt（候选提示预算/优先对象）、output、use_existing_index |
| orchestration | enabled、min_edge_confidence、build（max_concurrency/matching）、plan |
| flow | enabled、acceptance、narrative |

retrieval 和 orchestration 可独立禁用，同时关闭合法；禁用节点仍校验。Flow 的 enabled 默认 True，配置存在或启用不表示启动服务。

## 3. 单一可写路径与保留模型

树结构唯一位于 retrieval.build.structure；dynamic 只包含 rebalance_interval 和 expansion，不重复结构或派生阈值。六个倍率以 _multiplier 结尾；lazy_split_multiplier 相对节点容量，其余相对分支数。classification_batch_multiplier 不替代 request.classification_batch_limit 的绝对上限。

构建任务策略位于 retrieval.build.task，展示与缓存指标位于 output。allow_fallback_tree 反向对应业务 strict_failure，目标不再接受第二个反向开关。旧索引保护与回退树独立配置：存在可用旧索引且 preserve_previous_index_on_failure=True 时，失败应优先保护旧索引；allow_fallback_tree=False 时缺模型配置或构建失败不能生成回退树。模型和工件是否存在由业务判断，尚不接入目标配置。

构图整体并发唯一位于 orchestration.build.max_concurrency，阶段上限位于 capability.fingerprint.extraction.max_concurrency；后续接入构图时取两者较小值。关系阈值唯一位于 orchestration.min_edge_confidence。plan.dynamic_graph_enabled 保留，控制是否在规划中使用已有动态观测，不触发执行或构图。

缓存指标位于 retrieval.build.output.cache_metrics_enabled，与 request.caching 独立；prompt 只保留确定性策略和发现种子。Discovery 上下文、候选比例和优先对象统一位于 retrieval.discovery.prompt；candidate_budget_tokens 为只读派生值，不进入输入和序列化。

| 原目标路径 | 当前路径 |
|---|---|
| retrieval.build.max_depth / branching_factor | retrieval.build.structure 下的同名字段 |
| retrieval.build.max_concurrency / request_timeout_seconds | retrieval.build.request.max_concurrency / timeout_seconds |
| retrieval.build.task.strict_failure | 删除，反向由 retrieval.build.task.allow_fallback_tree 表达 |
| retrieval.build.dynamic.expansion 的无后缀倍率 | 同名字段加 _multiplier 后缀 |
| retrieval.build.prompt.cache_observability | retrieval.build.output.cache_metrics_enabled |
| retrieval.discovery.budget | retrieval.discovery.prompt |
| capability.fingerprint.extraction.body_limit / max_tokens | max_body_chars / max_output_tokens |
| capability.fingerprint.evaluation.evidence_text_limit | max_evidence_chars |
| retrieval.build.profiles.description_limit / rule_limit | max_description_chars / max_rule_chars |
| retrieval.build.request.context_window | context_window_tokens |
| orchestration.build.extraction_mode | capability.fingerprint.extraction.llm_enabled |
| orchestration.build.require_consensus | orchestration.build.matching.require_consensus |

TreeRequestConfig、TreeStructureConfig、IndexTreeStructureConfig、TreeBuildConfig、IndexTreeBuildConfig、TreeManagerConfig、TreePromptConfig、IndexBuildConfig、IndexArtifactConfig、TreeBuildOptions 等同义目标包装不恢复。树策略直接由 RetrievalBuildConfig 的子节点组合；DiscoveryPromptConfig 直接维护预算和优先对象。

PUBLIC_CONFIG_MODELS 登记公共树可达的 33 个具体模型，INTERNAL_CONFIG_MODELS 只登记 FingerprintProtocolConfig、OneShotTreeBuildConfig、RetrievalModelConfig，CONFIG_MODELS 合计 36 个。JsonValue 是根分类数据的 JSON 类型，不计入模型。

`config.__all__` 只提供 CapabilityType 和 9 个核心模型：SymphonyConfig、CapabilityConfig、StorageConfig、RetrievalConfig、RetrievalBuildConfig、OrchestrationConfig、OrchestrationBuildConfig、OrchestrationPlanConfig、FlowConfig。登记表、基类和其余子模型不进入便利导出，但可显式访问模块属性。

检索专用模型后端、联合类型 LLMClientConfig、紧凑码表、生成约束、单次请求覆盖、得分截断、工具浏览和 Experience 模型已删除。真实树解析设置保留在 retrieval/build/tree/context.py 和 retrieval/build/workflows/artifacts.py，不在目标配置中镜像。

## 4. 数据、校验与默认值

- 使用 Pydantic v2 原生校验、序列化和 Schema；未知字段、类型错误和越界值给出完整嵌套路径。
- 整数拒绝 bool、数字字符串、浮点数；数值拒绝 NaN/Infinity。构建 method、BuildMethod 和 runtime_config 入参别名在业务工作流中也已删除，统一传 config。
- 路径接受非空文本或 pathlib 纯路径，仅保存文本。根分类可包含 JSON 值；路径不触发读取。
- capability.type 仍为 skill/expert/connector/mcp，表达目标格式范围，不表示所有读取器已实现；通用字段名不含 skill。
- 属性默认冻结；retrieval.build.dynamic 保留可调整语义并校验赋值。嵌套容器不是深层冻结，各实例默认容器和子模型独立。
- model_dump(mode="json") / model_validate、model_dump_json / model_validate_json 可往返；不接受客户端、线程池、回调和缓存句柄。
- 公共构建默认深度 6、分支 128（至少 2）、并发 2、请求超时 420 秒、分类批量 32；旧底层树入口的并发 1、超时 180 秒、批量 20、分支 8 继续由业务维护。
- 动态树默认重平衡间隔 50，六个扩展倍率依次为 1.5、0.7、1.7、1.3、6.0、12.0，均为正数；计算基数不变。
- task.allow_fallback_tree=True、preserve_previous_index_on_failure=True；output.verbose=False、show_tree=True、display_capability_root=None、generate_tree_html=False、cache_metrics_enabled=True。show_tree 与 HTML 开关独立，原工作流的显式展示覆盖不变。
- 指纹提取 max_body_chars=16000、max_output_tokens=2048（也接受 None，表示沿用注入模型）、评估 max_evidence_chars=512。协议 extraction_version 为 symphony-capability-extraction-v3，不在配置阶段生成缓存签名。
- 画像 max_description_chars=140、max_rule_chars=120（均至少 40）；构建 context_window_tokens=0、max_output_tokens=0，0 表示未指定，不与 None 互换。
- Discovery 默认提示比例 0.01、上下文 200000、派生预算 2000、输出字符 12000、结果数 10、列表条目 40、增量提示字符 4000；优先对象列表与 use_existing_index=False 保持不变。
- 图构建并发 1、指纹提取并发 4、关系共识 True、min_edge_confidence=0.7、plan.mode=fast、top_k=3、max_depth=4、dynamic_graph_enabled=False。Flow 默认 enabled=True、min_successes=1、min_pack_success_rate=0.8、max_examples=5。

所有保留配置的默认值、约束和路径不变；构建签名、工件格式与 Flow 的 symphony.experience_recipe.v1 协议不变。目标 model_dump 不能直接替换原缓存签名输入。

## 5. 导出与验证

每个模型有中文 docstring，每个字段有中文 title/description；Schema 保留约束、枚举和适用示例。default_factory 从真实字段元数据读取，不能将 Schema 缺少 default 视为必填。

在已有 Python 环境运行 `python scripts/export_symphony_config.py`，自动更新 config.tree.txt（保留“全量配置”标题）。默认只展开公共树；--include-internal 输出保留模型参考，默认写入 config.reference.txt；--output 可指定位置。脚本不导入 SDK 包根，相同内容不重复写入。生成文件禁止手改，不安装 watcher/Git hook。

配置验证入口是 tests/unit_tests/symphony/test_config.py 和 test_config_export.py，覆盖默认补全、深层覆盖、错误路径、往返、独立容器、元数据、登记和退役字段拒绝。共享构树/Discovery 生命周期见 test_discovery_index_lifecycle.py，验证真实临时工件与确定性模型替身。

历史配置整理事实见 F_20261031_1614_symphony-config-contract；能力退役依据为 D-20261103-1438-symphony-capability-retirement，实际验证与跨仓适配由其对应 feature 记录维护。
