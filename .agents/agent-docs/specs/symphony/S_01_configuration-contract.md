---
id: S_01_configuration-contract
title: Symphony 运行配置与 Runtime 契约
type: S
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-10-31
updated: 2026-11-10
modules: [openjiuwen/symphony]
files: [openjiuwen/symphony/config.py, openjiuwen/symphony/runtime.py, openjiuwen/symphony/inventory.py, openjiuwen/symphony/runtime_storage.py, tests/unit_tests/symphony/test_config.py, scripts/export_symphony_config.py, tests/unit_tests/symphony/test_config_export.py, openjiuwen/symphony/config.tree.txt, openjiuwen/symphony/AGENTS.md, openjiuwen/symphony/README.md]
designs: [D-20261029-1619-skill-directory-graph-api, D-20261031-1614-symphony-config-contract, D-20261103-1438-symphony-capability-retirement, D-20261109-1603-symphony-orchestration-experience, D-20261109-1745-shared-capability-fingerprints]
---

# Symphony 运行配置与 Runtime 契约

## 1. 当前边界

`openjiuwen.symphony.config` 提供唯一运行配置定义；包根导出 SymphonyConfig、SymphonyRuntime 和 DiscoverySession。Runtime 仅接收 model/config，内部组装扫描、指纹、树、图、Discovery 和默认 Flow，业务消费者直接使用统一子模型。

配置构造不扫描目录、不读取环境或根分类文件、不创建模型客户端，也不启动服务。目录有效性、扫描、模型调用、执行权限、缓存签名和发布由运行层处理。

旧模型检索、分支浏览工具及独立经验库连同代码和配置退役；Discovery、构树、指纹、编排、动态观测和 Flow 保留。旧目标配置 `retrieval.search`、`retrieval.toolkit`、`experience` 按未知字段拒绝，不提供兼容别名。历史用户工件不迁移、不删除。

## 2. 统一入口与默认补全

```python
from openjiuwen.symphony.config import SymphonyConfig

config = SymphonyConfig.model_validate({
    "capability": {"sources": [{"type": "skill", "root": "./capabilities"}]},
    "storage": {"artifact_root": "./.symphony"},
    "retrieval": {
        "index_build": {"request": {"max_concurrency": 4}},
        "prompt": {"candidate_budget_ratio": 0.02},
    },
    "orchestration": {"experience": {"enabled": True, "acceptance": {"min_successes": 2}}},
})
restored = SymphonyConfig.model_validate_json(config.model_dump_json())
schema = SymphonyConfig.model_json_schema()
```

`capability.sources` 和 `storage.artifact_root` 必填。来源列表至少一项，每项必填 `type` 和 `root`。同类型可指定多个目录，所有目录共用集合级扫描、指纹策略和产物根。旧的集合级 `capability.root` 和 `capability.type` 按未知字段拒绝。策略节点用独立默认工厂补全，可以只覆盖深层字段。

| 公共分组 | 子配置与职责 |
|---|---|
| capability | sources（逐项 type、root）、scan（深度/预算/解析）、fingerprint（extraction/evaluation/cache） |
| storage | artifact_root |
| retrieval | enabled、index_build、prompt（候选提示预算和优先对象）、output、use_existing_index |
| retrieval.index_build | structure（深度/分支/根分类/容量倍率）、request、postprocess、equivalence、profiles、prompt、incremental、task、output |
| orchestration | enabled、min_edge_confidence、build（max_concurrency/matching）、plan、experience |
| orchestration.experience | enabled、acceptance、narrative |

retrieval 和 orchestration 可独立禁用，同时关闭合法。`orchestration.experience` 默认关闭，禁用节点仍校验。`retrieval.enabled` 统一控制构树、读取分类树和创建发现会话。

## 3. 单一可写路径与保留模型

树结构唯一位于 retrieval.index_build.structure，包含 max_capabilities_per_node_multiplier。该倍率对应递归构树的叶节点容量阈值，递归构树按 int(branching_factor * max_capabilities_per_node_multiplier) 计算；对象数不超过阈值时可停止细分，配置本身不计算阈值。原 retrieval.build.dynamic 节点及目标 DynamicTreeConfig、TreeExpansionConfig 已删除，不提供兼容别名。

重平衡间隔 rebalance_interval，以及 expand_threshold_multiplier、early_stop_multiplier、lazy_split_multiplier、classification_batch_multiplier、structure_sample_multiplier 五个倍率没有实际功能消费者，不再进入目标配置。对应业务重复定义已清理；request.classification_batch_limit 的有效批量上限继续保留。

构建任务策略位于 retrieval.index_build.task，构建展示与缓存指标位于 retrieval.index_build.output。allow_fallback_tree 反向对应业务 strict_failure，目标不再接受第二个反向开关。旧索引保护与回退树独立配置：存在可用旧索引且 preserve_previous_index_on_failure=True 时，失败应优先保护旧索引；allow_fallback_tree=False 时缺模型配置或构建失败不能生成回退树。模型和工件是否存在由构建入口判断。

构图整体并发唯一位于 orchestration.build.max_concurrency，阶段上限位于 capability.fingerprint.extraction.max_concurrency；构图时取两者较小值。关系阈值唯一位于 orchestration.min_edge_confidence。plan.dynamic_graph_enabled 保留，控制是否在规划中使用已有动态观测，不触发执行或构图。

缓存指标位于 retrieval.index_build.output.cache_metrics_enabled，只控制缓存命中和提示指纹统计，不控制缓存是否启用；指纹缓存仍由 capability.fingerprint.cache.enabled 配置。request.caching 已从目标配置删除，对应业务字段与旧签名输入一并删除，旧产物不兼容。retrieval.index_build.prompt 提供确定性策略和发现种子。会话上下文、候选比例和优先对象统一位于 retrieval.prompt；candidate_budget_tokens 为只读派生值，不进入输入和序列化。

| 原配置路径 | 当前路径 |
|---|---|
| retrieval.build.* | retrieval.index_build.* |
| retrieval.discovery.prompt / output / use_existing_index | retrieval 下的同名字段 |
| retrieval.build.max_depth / branching_factor | retrieval.index_build.structure 下的同名字段 |
| retrieval.build.max_concurrency / request_timeout_seconds | retrieval.index_build.request.max_concurrency / timeout_seconds |
| retrieval.build.task.strict_failure | 删除，反向由 retrieval.index_build.task.allow_fallback_tree 表达 |
| retrieval.build.dynamic.expansion.max_capabilities_per_node_multiplier | retrieval.index_build.structure.max_capabilities_per_node_multiplier |
| retrieval.build.dynamic 的其余字段与节点 | 删除，无实际功能消费者 |
| retrieval.build.request.caching | 删除，业务定义和旧签名输入也已移除 |
| retrieval.build.prompt.cache_observability | retrieval.index_build.output.cache_metrics_enabled |
| retrieval.discovery.budget | retrieval.prompt |
| capability.fingerprint.extraction.body_limit / max_tokens | max_body_chars / max_output_tokens |
| capability.fingerprint.evaluation.evidence_text_limit | max_evidence_chars |
| retrieval.build.profiles.description_limit / rule_limit | max_description_chars / max_rule_chars |
| retrieval.build.request.context_window | context_window_tokens |
| orchestration.build.extraction_mode | capability.fingerprint.extraction.llm_enabled |
| orchestration.build.require_consensus | orchestration.build.matching.require_consensus |

`retrieval.build` 和 `retrieval.discovery` 作为未知字段拒绝。Swarm 使用更新后的 YAML 模板。

TreeRequestConfig、TreeStructureConfig、IndexTreeStructureConfig、TreeBuildConfig、IndexTreeBuildConfig、TreeManagerConfig、TreePromptConfig、IndexBuildConfig、IndexArtifactConfig、TreeBuildOptions 等同义目标包装不恢复。树策略直接由 RetrievalBuildConfig 的子节点组合；DiscoveryPromptConfig 直接维护预算和优先对象。

PUBLIC_CONFIG_MODELS 登记公共树可达的 31 个具体模型。INTERNAL_CONFIG_MODELS 登记 FingerprintProtocolConfig 和 OneShotTreeBuildConfig，CONFIG_MODELS 合计 33 个。JsonValue 是根分类数据的 JSON 类型。

`config.__all__` 提供 CapabilityType 以及 SymphonyConfig、CapabilityConfig、StorageConfig、RetrievalConfig、RetrievalBuildConfig、OrchestrationConfig、OrchestrationBuildConfig、OrchestrationPlanConfig、OrchestrationExperienceConfig。登记表、基类和其余子模型可显式访问模块属性。

检索专用模型后端、联合类型 LLMClientConfig、紧凑码表、生成约束、单次请求覆盖、得分截断、工具浏览和 Experience 模型已删除。真实树解析设置保留在 retrieval/build/tree/context.py 和 retrieval/build/workflows/artifacts.py，不在目标配置中镜像。

## 4. 数据、校验与默认值

- 使用 Pydantic v2 原生校验、序列化和 Schema；未知字段、类型错误和越界值给出完整嵌套路径。
- 整数拒绝 bool、数字字符串、浮点数；数值拒绝 NaN/Infinity。构建 method、BuildMethod 和 runtime_config 入参别名在业务工作流中也已删除，统一传 config。
- 路径接受非空文本或 pathlib 纯路径，仅保存文本。根分类可包含 JSON 值；路径不触发读取。
- `capability.sources[].type` 的目标格式为 skill/expert/connector/mcp。当前运行层实现 skill 读取器。
- 所有配置模型禁止属性重新赋值。嵌套容器不是深层冻结，各实例默认容器和子模型独立。
- model_dump(mode="json") / model_validate、model_dump_json / model_validate_json 可往返；不接受客户端、线程池、回调和缓存句柄。
- 公共构建默认深度 6、分支 128（至少 2）、并发 2、请求超时 420 秒、分类批量 32；底层构树与统一公共默认值一致，不保留旧入口默认值镜像。
- structure.max_capabilities_per_node_multiplier 默认 1.5，保持正数、严格数值和有限值约束；计算基数仍为分支数。
- task.allow_fallback_tree=True、preserve_previous_index_on_failure=True；output.verbose=False、show_tree=True、display_capability_root=None、generate_tree_html=False、cache_metrics_enabled=True。show_tree 与 HTML 开关独立，原工作流的显式展示覆盖不变。
- 指纹提取 max_body_chars=16000、max_output_tokens=2048（也接受 None，表示沿用注入模型）、评估 max_evidence_chars=512。协议 extraction_version 为 symphony-capability-extraction-v3，不在配置阶段生成缓存签名。
- 画像 max_description_chars=140、max_rule_chars=120（均至少 40）；构建 context_window_tokens=0、max_output_tokens=0，0 表示未指定，不与 None 互换。
- Discovery 默认提示比例 0.01、上下文 200000、派生预算 2000、输出字符 12000、结果数 10、列表条目 40、增量提示字符 4000；优先对象列表与 use_existing_index=False 保持不变。
- 图构建并发 1、指纹提取并发 4、关系共识 True、min_edge_confidence=0.7、plan.mode=fast、top_k=3、max_depth=4、dynamic_graph_enabled=False。编排经验默认 enabled=False、min_successes=3、min_pack_success_rate=0.8、max_examples=5。

测试将 dcdcef3ef 导出的历史默认值按迁移表重排，并将基线单目录映射为 sources 的单项写法。分类树复用使用当前 Runtime 布局和从构树子模型提取的签名。Flow 使用 symphony.experience_recipe.v1 协议。

## 5. 导出与验证

每个模型有中文 docstring，每个字段有中文 title/description；Schema 保留约束、枚举和适用示例。default_factory 从真实字段元数据读取，不能将 Schema 缺少 default 视为必填。

在已有 Python 环境运行 `python scripts/export_symphony_config.py`，自动更新 config.tree.txt（保留“全量配置”标题）。默认只展开公共树；--include-internal 输出保留模型参考，默认写入 config.reference.txt；--output 可指定位置。脚本不导入 SDK 包根，相同内容不重复写入。生成文件禁止手改，不安装 watcher/Git hook。

配置验证入口是 tests/unit_tests/symphony/test_config.py 和 test_config_export.py，覆盖默认补全、深层覆盖、错误路径、往返、独立容器、元数据、登记和退役字段拒绝。共享构树/Discovery 生命周期见 test_discovery_index_lifecycle.py，验证真实临时工件与确定性模型替身。

配置整理及无效目标字段清理的实施与验证见 F_20261031_1614_symphony-config-contract；能力退役依据为 D-20261103-1438-symphony-capability-retirement，实际验证与跨仓适配由其对应 feature 记录维护。


## 6. Runtime 当前运行契约（2026-11-09）

- 构造先校验 `capability.sources` 恰有一项，再固定能力根、产物根和根分类文件相对路径，复制嵌套配置。多来源由配置模型验证，Runtime 在创建产物前明确拒绝。
- `retrieval/orchestration` 均提供 status/read/build/cancel_build；进度与发布前附属产物钩子是单次调用参数。图结果是公共值对象，树结果为字典；均携带构建 ID、版本、变化与复用计数、诊断。
- `retrieval.create_session` 返回 DiscoverySession，工具与提示 Rail 共用。会话从 Runtime 或 `config=` 参数深复制 RetrievalConfig，使用 prompt、output 和 use_existing_index。检索开关和构树由 Runtime 持有的配置决定。预算和索引版本可冻结，source_by_id 以 capability_id 为键，目录、可见及禁用条件由调用方动态提供。
- 会话发现消费扫描器已接受的 Skill 记录。检索状态返回扫描错误诊断，并将不完整集合对应的索引标为过期。构树和指纹发布要求完整扫描，扫描错误导致构建失败并保留上次成功产物。
- 检索与编排独立。编排和经验都开启才接收新执行证据及使用 Runtime Flow。禁用时 `submit_evolution()` 返回空结果，直接观测写入与 Flow 执行入口在创建存储前拒绝。
- model=None 允许只读和确定性发现，并可按任务策略回退构树。构图和规划需要模型。
- 检索构建与构图共用已配置 Skill 集合的 `FingerprintService`、指纹产物和缓存。允许回退构树且宿主缺少模型时，以元数据生成指纹。构图发布与本次使用的指纹快照绑定。
- `DiscoverySession.capability_search(query, type, operation, category, cursor, top_k)` 按单个 `type` 选择目录。`type=skill` 为默认值，复用现有目录的 `list`、`search`、分类和游标。SkillFS 只为会话可见且启用的记录使用来源和内容哈希一致的指纹。摘要、场景和关键词参与召回，指纹缺失或过期时使用当前目录描述。
- 宿主通过 `create_session(search_catalog=...)` 处理 `expert`、`connector`、`mcp` 的单次查询，按会话授权筛选候选后可用 `discovery.search_capabilities()` 排序。`get_tools()` 返回单个 `capability_search` 工具，和 SDK 直接调用共用同一会话目录和游标。
- skill 读取器已实现。expert/connector/mcp 在读取时明确报不支持。多来源及混合类型构建由独立的运行方案定义。
- 原生 Model 在所属宿主循环调用；同步树算法在工作线程调度。请求超时/任务超时/取消跨桥传播，模型取消清理最多等待5秒，超界终止构建且不重试/回退。永久HTTP错误优先于错误文本判断，不重试。
- 显式 candidate_ids=[] 或全部失效 ID 返回 no_plan，不扩大范围。规划读取已有动态观测与经验写入独立，Rail 的新观测提交受经验开关控制。
- Runtime 持有惰性默认 Flow，create_scope(store=...) 共享默认打包/审核组件并使用宿主受限存储；授权、确认和安装是宿主职责。
- aclose 为单次拥有的关闭任务，等待者取消不取消清理。图构建、嵌套规划和增量任务以及文件写入线程必须完成取消/收尾；不关闭宿主模型。

| 配置路径 | 真实消费者 | 生效阶段 |
|---|---|---|
| capability.sources[0].root/type | DirectoryInventory、RuntimeStorage | 当前单来源入口的显式扫描和集合身份 |
| capability.scan.max_depth/limits/manifest | SkillFolderScanner、SkillManifestParser | 扫描及解析 |
| capability.fingerprint.extraction | FingerprintService / Extractor | 提取批次、并发、正文、输出与超时 |
| capability.fingerprint.evaluation/cache | EvaluationSuite、FingerprintCache | 提取后的评估和缓存复用 |
| storage.artifact_root | RuntimeStorage、三个引擎 | inventory/cache/tree/graph/flow及发布 |
| retrieval.index_build.structure/request | TreeBuilder/OneShotBuilder/ModelBridge | 结构与模型请求，递归叶容量倍率 |
| retrieval.index_build.profiles/prompt | 画像和提示构建 | 文本裁剪、协议、种子 |
| retrieval.index_build.postprocess/equivalence | 树后处理与等价分组 | 生成后的整理 |
| retrieval.index_build.incremental | 增量工作流 | 增量阈值、重分类与升级全量 |
| retrieval.index_build.task | toolkit任务调度 | 总超时、回退、旧索引保护 |
| retrieval.index_build.output | 构建产物/展示/缓存指标 | 输出阶段，不进入树内容签名 |
| retrieval.prompt | SkillFS | 会话预算和优先对象；宿主解析模型缺省容量 |
| retrieval.output/use_existing_index | DiscoverySession、SkillFS | 搜索、列表、游标输出、索引快照 |
| orchestration.build | OrchestrationService / Matcher | 构图并发、候选和匹配批量 |
| orchestration.min_edge_confidence | Matcher、Fast/BeamPlanner | 构图与规划共用阈值 |
| orchestration.plan | Fast/BeamPlanner、GraphEngine | 模式、top_k、深度与动态观测读取 |
| orchestration.experience.enabled | Runtime、GraphEngine、RuntimeFlow | 图观测写入和 Flow 启停 |
| orchestration.experience.acceptance/narrative | SymphonyFlowEngine、蒸馏与叙事组件 | 接纳、叙事样例与候选生成 |

新版 runtime.json 绑定绝对能力根与类型；非空无布局标记目录、错误布局版本、不同集合均拒绝，不删除历史目录。树commit签名覆盖内容、构建策略、协议、模型身份；图身份包含指纹、图策略和模型。无模型查询继续比较内容/策略，保留产物模型身份。构建后发布前重新检查集合快照，失败和取消不切换当前版本。构图复用的本次关系匹配计数为零，graph_reused_count=1；新构建ID与旧产物版本分开记录。

运行接入替代设计旧草案中的 Source 注入、retrieval.search 和兼容路线，历史设计与历史测试记录保留，不视作当前行为。
