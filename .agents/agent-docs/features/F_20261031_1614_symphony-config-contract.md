---
id: F_20261031_1614_symphony-config-contract
title: Symphony 多层 Pydantic 配置契约
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-10-31
updated: 2026-11-02
modules: [symphony]
files: [openjiuwen/symphony/config.py, tests/unit_tests/symphony/test_config.py, scripts/export_symphony_config.py, tests/unit_tests/symphony/test_config_export.py, openjiuwen/symphony/config.tree.txt, openjiuwen/symphony/AGENTS.md, openjiuwen/symphony/README.md]
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


## 2026-11-02 配置收敛与校验修正

本次续写同一特性，比较基线为工作树 feature/symphony-config-contract 的 7f0c29ca9fa81461eeb4543cf56b57d7959d01f3。用户已明确选择先落地三项整理与 method 校验修复；未扩大到解析设置去重、算法倍率调整或运行消费者迁移。

实际变化：

- 公共/内部登记拆为 PUBLIC_CONFIG_MODELS 和 INTERNAL_CONFIG_MODELS，完整 CONFIG_MODELS 仍为 79 个模型；__all__ 从 83 个名称收敛为公共树 9 个模型及 CapabilityType。内部模型和登记表仍可由内部工具显式访问。
- RetrieverConfig 继承 ProgressiveRetrieverConfig，共享 traversal/render/generation/llm 的定义；top_k 仍分别为 10/5。共享字段的中文元数据随共同定义统一，不再维护服务入口的重复措辞，JSON 数据路径和默认值不变。
- 两项失败策略从 output 移到 task，默认 preserve_previous_index_on_failure=True、strict_failure=False 保持不变。output 保留 generate_tree_html=False；旧目标路径明确报错，不增加迁移别名。
- 两个 method 字段使用同一个整数字面量校验类型，保留编码 4 和 Schema 的 const 约束。根因、修复前证据与回归见 [构建方式校验修复记录](../bugfix/symphony/B_20261102_1507_symphony-build-method-strict-integer.md)。

选择原因：公共导出按宿主真正使用的配置树定义，内部登记服务验证和文档，两者职责分开；检索器复用结构以减少重复维护，同时保留已有入口默认值；失败处理归任务生命周期，使输出设置只描述产物。没有采用统一所有检索默认值、引入额外基础配置模型、删除 method 字段或提前接入 Runtime 的方案，因为本次仅获准收敛配置并保持默认值。

实际验证：在新工作树复用主仓虚拟环境，Python 3.11.15、pytest 9.0.2。

```bash
source /Users/xiaoyulin/Documents/Huawei/code/agent-core/.venv/bin/activate
export PYTHONPATH=.:${PYTHONPATH:-}
python -m pytest tests/unit_tests/symphony/test_config.py -q
```

2026-11-02 结果：**220 passed，2 个既有 Pydantic 弃用警告，0.69 秒测试时间**。本轮覆盖公共导出及登记分区、两套检索器非默认嵌套覆盖/往返/容器隔离、失败策略新旧路径、两个 method 的字典和 JSON 非法输入，以及全部原有默认值对照与 79 个模型的 Schema/往返验证。

配置实现和测试之外仅同步 D/S/F/B 及生成索引；未修改包根导出、Runtime、引擎、Swarm、旧配置、依赖或缓存逻辑。未运行格式化、静态检查或运行链路测试，本次未提交或推送。检视采用范围内自检，核对上述四项差异和用户授权边界；上轮子 Agent 结论只作为本轮修改依据，不当成本轮独立复查证据。


## 2026-11-02 开发流程自动导出配置树

用户先要求列出带中文注释的全部配置，继而要求修改后自动导出，并明确选择开发收尾流程触发。本次继续同一配置特性，目标配置和默认值沿用上一节；新增开发工具与生成文档，不修改运行消费者。

新增 scripts/export_symphony_config.py：使用 Pydantic 的 model_fields / FieldInfo 读取说明、默认值及类型；公共 SymphonyConfig 递归展开，70 个内部模型逐一列出并引用子模型。生成 config.tree.txt 共 457 行，覆盖全部 79 个模型。字段标明默认值或必填，枚举按类型定义展示；大码表只列数量并定位到源默认定义，只读派生属性不当作可配置输入。

工具直接按文件位置加载 config.py，避免为了导出文档导入 SDK 包根或运行模块。未通过实例化根模型填造必填路径；默认工厂在纯配置边界内读取，依赖前序输入的工厂仅作标记。递归模型保留引用，嵌套默认工厂的显式覆盖可展开；重复导出相同内容不触碰文件，输出不含会反复变化的时间戳。

模块新增 AGENTS.md，将每次配置变更后自动执行生成脚本列为开发收尾要求；README 提供配置树入口和调用命令。仓库原有 *.txt 忽略规则只为这一份生成文档增加精确例外。配置说明仍只维护在 config.py，不引入另一份人工字段表。

选择原生字段元数据加轻量文本渲染，是因为当前需要固定的树形文本与 # 中文注释，不需要另加文档框架依赖。用户选择开发流程触发，因此未增加本地监听器或 Git hook；本轮也没有实现 Markdown/HTML 文档框架或在 Runtime 导入时写文件。

实际验证命令：

```bash
source /Users/xiaoyulin/Documents/Huawei/code/agent-core/.venv/bin/activate
export PYTHONPATH=.:${PYTHONPATH:-}
python scripts/export_symphony_config.py
python -m pytest tests/unit_tests/symphony/test_config.py tests/unit_tests/symphony/test_config_export.py -q
```

2026-11-02 结果：**227 passed，2 个既有 Pydantic 弃用警告，0.79 秒测试时间**。新增 7 项验证涵盖必填/可空/枚举、嵌套默认工厂覆盖、递归引用、前序输入依赖工厂、全部内部模型字段和后端联合类型、生成文件一致性、幂等写入，以及使用隔离 Python 子进程从仓库外目录导出并确认未导入 openjiuwen 模块。未运行格式化或静态检查。

配置模型实现未在本次导出任务中继续改动；上一节的导出收敛、任务路径和 method 修复仍有效。D/S/F 与生成索引已同步，Runtime/引擎/Swarm/依赖未改，本轮未提交或推送。自动动作由开发收尾规则执行；手工编辑文件不会触发后台监听。


## 2026-11-02 用户可调策略合并到 SymphonyConfig

用户确认上一轮提出的六组配置树后，继续同一配置契约实现。此节替代前文公共树仅含 9 个模型、内部策略全部独立的范围描述；此前 method 严格校验、旧消费者不改和各入口默认值仍有效。配置变化是 Pydantic 目标入口的变化，本次没有 Git 分支合并、提交或推送。

capability 增加 scan/fingerprint；retrieval 的 build/search 采用多层策略，增加 discovery/toolkit；orchestration.build 组合匹配策略；根增加 flow/experience。结构、请求、任务和输出各自归位，根最小输入仍只有三个必填值。公共分支数沿用至少 2 的限制，通过 RetrievalTreeStructureConfig 复用索引结构并单独保留该约束。

从指纹、关系匹配和提示配置抽出 FingerprintPolicyConfig、GraphMatchingPolicyConfig、TreePromptPolicyConfig；独立入口在其上保留原协议身份/并发/提示版本。这样公共树可调策略不用复制字段，也不会把协议标识或重复并发暴露为输入。原提取方式只由 capability.fingerprint.extraction.llm_enabled 表达；共识只由 orchestration.build.matching.require_consensus 表达。图构建整体上限与指纹阶段上限的有效取值约定在元数据和 spec 中说明，运行实现仍待后续接入。

检索复用方向调整为公共 RetrievalSearchConfig → ProgressiveRetrieverConfig（增加后端、top_k=5）→ RetrieverConfig（top_k=10），独立入口默认值和 JSON 路径保留。公共构建将索引子策略直接组合，避免 retrieval.build.build 再包一层。复用候选生成配置后公共请求超时可显式为 None（沿用后端策略），默认 120 秒不变。experience.build 保留原可校验赋值性质，其他节点仍遵循原冻结约束；不引入额外服务启动副作用。

合计 84 个配置模型：公共树可达 46 个、剩余内部 38 个。新增 5 个模型用于策略拆分、公共树结构约束和经验分组；不是新增运行能力。__all__ 保留原核心配置类并增加 FlowConfig、ExperienceConfig，共 11 个类及 CapabilityType，不按登记数扩大便利导出。公共 Schema 的 JsonValue 递归类型不误计为配置模型。

配置树自动重新导出为 419 行，公共策略完整展开，原内部模型的共享子配置由公共树提供定义。README 增加实际配置构造和部分覆盖示例，模块 AGENTS 补充归属、默认值和登记/导出边界。D 第 8 节记录用户确认及验收计划，S 更新当前结构和八条字段迁移关系；原历史验证记录保留。

验证命令（已有主仓 Python 3.11.15 / Pydantic 2.12.5 环境，当前工作树源码）：

```bash
source /Users/xiaoyulin/Documents/Huawei/code/agent-core/.venv/bin/activate
export PYTHONPATH=.:${PYTHONPATH:-}
python scripts/export_symphony_config.py
python -m pytest tests/unit_tests/symphony/test_config.py tests/unit_tests/symphony/test_config_export.py -q
```

2026-11-02：**260 passed，2 个既有 Pydantic 弃用警告，0.86 秒测试时间**。相较上一轮增加 33 项通过的用例实例：新模型 Schema/往返、统一入口默认补全和全部可调索引字段的源默认值对照、六组深层覆盖、15 类重复/内部路径的字典及 JSON 拒绝、深层错误定位和根实例默认容器隔离。既有 method 回归、无文件系统/环境副作用、包根导出保持原配置、独立导出不加载运行模块和生成文件一致性均继续通过。

范围内按 jw-review 自检，核对与本轮前工作区快照的差异、实际配置入口、继承后的字段、所有默认工厂及生成树，不将此前子 Agent 评审当作本轮独立检视。Runtime、引擎、Swarm、包根导出、依赖和缓存逻辑未改；未运行格式化或静态检查，未验证新配置的运行接入。用户后续若要求接入，需要映射新增路径及缓存身份，不能把本轮配置通过当作端到端运行证据。


## 2026-11-02 清理重复独立配置与导出范围

用户追问 GraphPlanningConfig 是否属于真实运行实现，经核实两个同义目标类均在新 config.py 中；真正运行消费者仍使用 orchestration/config.py 的 dataclass。用户随后明确要求检查并清理类似的重复内部配置。本轮沿用现有特性，局部替代上一节保留解析镜像和全部内部附录的安排；不改公共结构或实际运行定义。

### 盘点与清理依据

逐项读取 38 个内部模型、其继承/组合引用和默认值，搜索 openjiuwen/tests/examples/scripts 对目标模块的使用。目标模块的静态导入只见配置测试和 README 示例，导出器按文件加载；没有运行消费者。清理依据同时考虑参数是否被公共树覆盖，以及独立用途/默认差异，没有仅因暂未接入而删除所有内部定义。

| 删除的目标模型 | 当前配置或真实运行定义 |
|---|---|
| GraphPlanningConfig | orchestration.plan 与 orchestration.min_edge_confidence；旧运行 OrchestrationConfig 继续存在 |
| GraphMatchingConfig | orchestration.build.matching 与 orchestration.build.max_concurrency |
| FingerprintConfig | capability.fingerprint 与独立 FingerprintProtocolConfig |
| CapabilityIndexBuildConfig | retrieval.build 下的结构、策略、任务和输出；旧 SkillIndexBuildConfig 不改 |
| TreeBuildSettings | 原 retrieval/build/tree/context.py 的解析设置；包括原连续失败上限 5 |
| ResolvedIndexTreeStructureConfig / ResolvedIndexBuildConfig | 原 retrieval/build/workflows/artifacts.py 的 ResolvedBuildConfig 与解析流程 |
| RuntimeRetrieverConfig | 原 retrieval/search/service/models.py 的 _RuntimeRetrieverConfig 与转换函数 |

删除 8 个重复组合/解析快照及其登记；不保留同义别名，也不再为了默认值对照测试重新建包装模型。删除仅用于镜像工厂返回注解的 Self 导入。配置模型总数由 84 变为 76，公共仍为 46，内部由 38 变为 30；__all__ 的 11 个核心模型及 CapabilityType 不变。

### 剩余 30 个内部模型的保留理由

| 参数族 | 模型与理由 |
|---|---|
| 指纹协议（1） | FingerprintProtocolConfig 保存版本与附加签名，公共策略不包含这些字段 |
| 树入口参数（7） | TreePromptConfig、TreeRequestConfig、TreeBuildConfig、IndexTreeBuildConfig、TreeStructureConfig、IndexTreeStructureConfig、TreeManagerConfig；保留底层与索引的 1/180/20 和 2/420/32，以及分支 8/128、根分类输入形态和提示协议差异；组合由后续独立工作流/调用参数实际引用 |
| 动态及一次性构建（3） | TreeExpansionConfig、DynamicTreeConfig、OneShotTreeBuildConfig；倍率、重平衡和一次性完整生成预算具有独立语义 |
| 独立索引工作流（3） | CapabilityIndexStorageConfig、IndexArtifactConfig、IndexBuildConfig；独立存储状态文件、回退树策略、构建 method 和模型参数不能从公共配置等价替换 |
| 模型后端（7） | RetrievalModelConfig、OpenAIClientConfig、VLLMClientConfig、TransformersParallelismConfig、TransformersOptimizationConfig、TransformersClientConfig、DistributedGenerationConfig；模型绑定、设备、并行与优化参数不属于 Runtime 用户策略 |
| 生成参数（3） | TrieConstraintConfig、GenerationConstraintsConfig、LLMGenerationConfig；允许 ID 集合与模型采样参数区别于候选选择策略 |
| 检索入口及调用策略（5） | DisclosureConfig、ProgressiveRetrieverConfig、RetrieverConfig、RetrievalRequestConfig、ScoreTruncationConfig；保留展示深度差异、top_k=5/10、独立后端、单次覆盖以及评分截断 |
| 单次构建选项（1） | TreeBuildOptions 只表达显式调用覆盖与展示选项，其可空默认不能替换为服务默认配置 |

本轮清理后仍保留多种独立入口，原因已逐组记录；没有把生产源码同名类误当成可删除目标。后续统一运行入口如要进一步退役这些配置族，需要同时审阅真实独立消费者。

### 验证与文档

规划、指纹和索引的默认值测试直接使用公共节点与当前旧配置逐字段比较；协议数据单独对照。任务和输出负向用例改为验证公共 RetrievalBuildConfig。继续保留 IndexBuildConfig 的 method 严格整数回归；ResolvedIndexBuildConfig 的镜像已删除，历史 B 文档中的两模型测试证据保留为当时事实。

对清理前的公共 JSON Schema 与完整最小配置序列化结果各保存一份临时基线，清理后逐项比较：**两者完全一致**。该检查覆盖公共字段、约束、中文元数据、默认值和嵌套结构；基线位于本次临时目录，不作为新的仓库快照文件。

默认导出现在只展开公共树；--include-internal 生成包含 30 个保留模型的参考，默认输出 config.reference.txt，避免覆盖 config.tree.txt；--output 在两种模式均有效。补充默认文档无内部附录、完整参考字段覆盖、两个默认输出路径隔离、显式路径和仓库外隔离执行验证。README、模块 AGENTS、S 与 D 第 9 节同步此范围。

实际验证命令（主仓已有 Python 3.11.15 / Pydantic 2.12.5 环境，当前工作树源码）：

```bash
source /Users/xiaoyulin/Documents/Huawei/code/agent-core/.venv/bin/activate
export PYTHONPATH=.:${PYTHONPATH:-}
python scripts/export_symphony_config.py
python -m pytest tests/unit_tests/symphony/test_config.py tests/unit_tests/symphony/test_config_export.py -q
```

2026-11-02：**241 passed，2 个既有 Pydantic 弃用警告，1.13 秒测试时间**。相较上一轮，删除 8 个模型的 16 项 Schema/往返实例、已删除解析镜像的 6 项 method 实例和 1 项重复索引默认值测试；增加镜像不再暴露验证及 3 项导出模式验证，所有剩余模型与公共默认值对照继续通过。

检视以本轮开始时的未提交工作区快照为基线（HEAD 仍为 7f0c29ca9fa81461eeb4543cf56b57d7959d01f3），按 jw-review 对范围内差异自检；当前生产运行定义、Runtime、引擎、Swarm、包根导出、依赖与缓存未改。未运行格式化或静态检查，未提交或推送，不将配置验证解释为运行接入完成。

交付前一致性核对发现工作区的生成文件标题已被改为“全量配置”。保留该编辑并同步到生成脚本；再次执行导出提示内容已是最新，没有重写文件。仅重跑受影响的 test_config_export.py，结果为 **10 passed，2 个既有警告，0.62 秒**；配置模型未再变化，231 项配置验证沿用本轮上面的通过证据。


## 2026-11-02 构建与检索配置进一步收敛

### 决策与实际改动

用户确认 D 第 10 节方案，要求配置先行并同时收敛构建和检索包装。本节部分替代上一节“剩余 30 个内部模型的保留理由”：旧入口默认值不同不再作为保留同义目标模型的依据，原业务定义和消费者继续工作；历史的 76/46/30 模型数量及 241 项测试结果仍是当时事实。

删除 13 个目标模型：TreeRequestConfig、TreeStructureConfig、IndexTreeStructureConfig、TreeBuildConfig、IndexTreeBuildConfig、TreeManagerConfig、TreePromptConfig、IndexBuildConfig、IndexArtifactConfig、TreeBuildOptions、ProgressiveRetrieverConfig、RetrieverConfig、DisclosureConfig。请求、结构分别直接定义在 IndexRequestConfig 和 RetrievalTreeStructureConfig；AgenticRenderConfig 直接继承 RetrievalRenderConfig，保留工具浏览默认值。没有兼容别名、替代组合或为测试而建的包装。

DynamicTreeConfig 和 TreeExpansionConfig 挂到 retrieval.build.dynamic；前者去掉 structure 与六个派生属性，保留可赋值校验。结构唯一来源为 retrieval.build.structure，后续运行层负责阈值计算。allow_fallback_tree 归 task，verbose/show_tree/display_capability_root 归 output；新字段默认值与清理前的内部目标模型逐项一致。原高层工作流显式传入 show_tree=False，当前没有改变这一行为；新目标默认 True 的来源是原 TreeBuildOptions，后续接入时仍需按真实入口处理。

配置模型由 76 降为 63，公共树可达由 46 增为 48，内部由 30 降为 15；__all__ 的 11 个核心类与 CapabilityType 不变。独立后端联合类型保留，测试通过 TypeAdapter 直接验证后端选择和序列化；独立协议、存储、一次性构建、生成及调用/评分参数本轮未合并。

IndexBuildConfig.method 随包装退役，删除 TreeBuildMethod 和 _strict_integer；不再为只接受 4 的目标字段保留专用测试。历史 [method 严格整数修复](../bugfix/symphony/B_20261102_1507_symphony-build-method-strict-integer.md) 的故障、修复和验证证据保留，本轮属于目标字段退役，不是恢复宽松校验；原业务 BuildMethod 未变。

### 验证证据与范围

比较基线为本轮开始的工作区快照，HEAD 仍是 7f0c29ca9fa81461eeb4543cf56b57d7959d01f3；保存位置 /private/tmp/symphony-config-convergence-9d7hixqk，包含源文件、公共 Schema、默认序列化结果与新归位参数的旧内部默认值。该临时基线只用于本轮对照，不作为仓库测试 fixture。

对照结果：仅增加 dynamic、task.allow_fallback_tree 和 output 的三个字段；既有公共字段、默认值、类型、约束、title 与 examples 全部不变。Schema 比较只排除有意更新的 description 文本，并显式移除计划中新增的节点后比较，未忽略类型或约束。新归位默认值与旧内部目标模型完全一致。Symphony 目录的 Python 源文件哈希核对显示只有 config.py 改变。

配置测试直接与真实 toolkit、树构建、索引工作流、检索服务及其他模块的配置定义对照；旧入口默认差异和协议/模型参数显式排除，不再依赖已退役包装。补充新路径的部分覆盖、字典/JSON 往返、动态树实例隔离、完整错误路径和禁止重复输入路径。全部保留模型继续覆盖中文 Schema 和序列化。

执行环境：主仓已有 Python 3.11.15、Pydantic 2.12.5、pytest 9.0.2，PYTHONPATH 指向当前工作树。命令：

```bash
source /Users/xiaoyulin/Documents/Huawei/code/agent-core/.venv/bin/activate
export PYTHONPATH=.:${PYTHONPATH:-}
python scripts/export_symphony_config.py
python -m pytest tests/unit_tests/symphony/test_config.py tests/unit_tests/symphony/test_config_export.py -q
```

结果：**220 passed，2 个既有 Pydantic 弃用警告，1.07 秒**。后续只更新内部参考标题措辞，定向复验 test_config_export.py：**10 passed，2 个既有警告，0.53 秒**；配置源码未再变化，210 项模型验证沿用本轮证据。

config.tree.txt 已重新生成，标题“全量配置”保留，默认文档只包含公共树；内部参考中没有退役模型。README 示例、模块 AGENTS、当前 S 与 D 第 10 节同步。没有新增 watcher 或 Git hook，没有修改业务模块、Runtime、引擎、包根导出、依赖或缓存签名；未运行格式化、静态检查，未提交或推送。下一阶段才执行运行链路和缓存映射迁移，配置验证不证明这些工作已经完成。


交付前按 jw-review 对本轮工作区快照到候选的配置、测试、导出和文档差异完成自检，核对模型删除、字段归位、继承默认值、后端联合类型、包根导出及源码消费者；范围内未发现需要修改代码的问题。复查中将 S 的设计章节引用补到第 10 节，并在历史 B 中追加 method 退役后的适用范围，没有改写历史故障和验证结果。配置源码和测试在通过后未改变，文档修订不触发重复执行模型测试。


## 2026-11-02 Rebase 到最新 develop 并解决冲突

### 基线与冲突处理

用户要求将已推送的 feature/symphony-config-contract rebase 到 develop 并解决冲突。获取远端后确认 origin/develop 仍为 e089198835426cd7681761b0cc278670cd83031f，upstream/develop 已前进 59 个提交至 da6045f3b0c66de64b3d9bf8cfebfbd950154b18，因此采用后者作为本轮固定目标，不移动另一个工作树中的本地 develop。

Rebase 前分支为 e9d715c0578e8dfffc2d389d3c90ff91680c1c78，本地恢复点为 backup/symphony-config-contract-pre-rebase-e9d715c05。两个配置提交依次重放为 8784b086a 和 6c9aadd59。文本冲突仅在生成的文档索引：总索引、设计索引及 bugfix 索引；源文档均保留，通过 sync_documents.py 重新生成合并结果，并保留索引作者。同步器附带补写的 7 篇上游旧文档 frontmatter 已撤回，避免本次 rebase 改动无关源文档；索引包含两侧文档，最终按 286 篇源文档生成。

Range-diff 排除生成索引后，两个配置提交的补丁均等价；rebase 完成后，配置、配置测试、导出脚本与树形文件均与原分支一致。上游 README 的新说明与本分支目标配置说明自动合并，均予保留。

### 上游契约兼容

上游 c8c621b22 将 FingerprintSettings 的提取协议从 v2 升为 v3，并让 llm_max_tokens 接受 None。原目标配置仍是 v2 且拒绝 None，会使默认值源对照失败，也无法表达上游新增的预算选项。

本轮仅在目标 config.py 中将 FingerprintProtocolConfig.extraction_version 对齐 symphony-capability-extraction-v3，并将 capability.fingerprint.extraction.max_tokens 扩展为正整数或 None，默认继续为 2048。补充中文说明、字典/JSON 输入与完整错误路径验证，重新导出 config.tree.txt 并同步当前规约。模型总数仍为 63，公共 48、内部 15；业务实现保持 upstream/develop 的版本，未提前接入新配置。

### 验证与交接

本轮实际基线与配置输入约束发生变化，因此重新运行受影响的配置和导出测试；不是在不变提交上重复推送前验证。主仓已有 Python 3.11.15、Pydantic 2.12.5、pytest 9.0.2 环境，PYTHONPATH 指向当前工作树：

```bash
source /Users/xiaoyulin/Documents/Huawei/code/agent-core/.venv/bin/activate
export PYTHONPATH=.:${PYTHONPATH:-}
python scripts/export_symphony_config.py
python -m pytest tests/unit_tests/symphony/test_config.py tests/unit_tests/symphony/test_config_export.py -q
```

结果：**227 passed，2 个既有 Pydantic 弃用警告，1.05 秒**，包括现有默认值源对照以及新增 None/正整数预算、零值/负值/布尔值/小数/字符串拒绝验证。未运行格式化或静态检查。测试后仅同步文档，不再次运行未受影响的测试。

此记录对应重放后的 6c9aadd59 加本轮目标指纹配置兼容补丁；重写后的提交与原远端历史不同。当前原远端分支为 e9d715c05，远端更新需使用该明确旧值的租约保护，不能用无保护的强推覆盖后续提交。本地 rebase 和恢复点不等于已经完成远端历史更新。


## 2026-11-02 配置树评审后的四项语义整理

### 确认与替代范围

本轮基线为 feature/symphony-config-contract 的 395a5df964c98c982a6231f1a549dbc3feb23a99，开始时工作区干净。用户在独立子智能体评审后确认仅落实前四项，并限定 config.py、配置测试与相关文档，保持默认值，重新生成配置树；业务模块后续迁移。确认方案续写在关联 D 第 11 节，当前有效契约更新到关联 S；本节部分替代早期章节中保留 strict_failure 及使用旧倍率/聚类名称的决定，不重写历史测试事实。

### 实际实施与取舍

- 删除 IndexTaskConfig.strict_failure，回退决策只保留 allow_fallback_tree=True；preserve_previous_index_on_failure=True 继续独立。旧 toolkit 的 not strict_failure 映射作为来源证据，默认值对照显式验证该反向关系。四种保留开关组合均可构造，配置不判断模型齐备性或索引状态；未来运行接入须迁移禁止回退时的缺模型前置失败、错误报告，以及可用旧索引的保护优先级。
- TreeExpansionConfig 六个字段统一添加 _multiplier 后缀，默认值保持 1.5、0.7、1.7、1.3、6.0、12.0，正数约束不变。保留 lazy_split 相对节点容量、其他五项相对分支数的区别；说明中明确分类批量倍率不替代 request.classification_batch_limit 的绝对上限。
- 经验聚类字段改为 query_clusters_per_capability_set，默认仍为 None，正整数或 None 的类型不变。中文元数据明确实际处理的是每个能力集合内成功轨迹的查询，而非能力类别数或整个库的簇数，并补充 None/4 示例。
- RetrievalSearchConfig 使用 Pydantic 字段校验，在前序 render 已校验后检查 generation。logit_selection + 关闭紧凑编码 + error 回退被拒绝，错误路径为 retrieval.search.generation；允许 generate 回退，generate 选择模式不受此限制。选择字段校验以保留定位到 generation 的错误路径，不把 render 与 generation 合并。后端能力检查仍不进入纯配置。

八个退役输入名称（一个删除、七个改名）均按未知字段拒绝，无别名或自动迁移。其余四项评审建议本轮未实施：不移动 search.generation/request、不合并 Discovery 节点、不迁移缓存观测、不统一其他单位字段。模型登记仍为 63 个（公共 48、内部 15），便利导出和业务包根导出保持原样。

### 验证证据

修改前保存目标源码、完整最小输入的默认序列化结果及公共 Schema，临时位置为 /var/folders/xq/wcv2ptms57b92bkfwgbv40zw0000gn/T/symphony-config-semantics-v5x1pmkq。对照时只执行上述七个字段改名及 strict_failure 删除，再比较所有默认值，结果完全一致。Schema 对照只额外排除明确修改的中文元数据和聚类字段新增示例，其余字段、类型、标量约束和默认值完全一致；新组合约束由 Pydantic 校验实现，并以中文说明导出。

实际环境：macOS、主仓已有 Python 3.11.15 / Pydantic 2.12.5 / pytest 9.0.2，PYTHONPATH 指向当前工作树。执行：

```bash
source /Users/xiaoyulin/Documents/Huawei/code/agent-core/.venv/bin/activate
export PYTHONPATH=.:${PYTHONPATH:-}
python scripts/export_symphony_config.py
python -m pytest tests/unit_tests/symphony/test_config.py tests/unit_tests/symphony/test_config_export.py -q
```

结果：**293 passed，2 个既有 Pydantic 弃用警告，1.10 秒**。覆盖选择模式/紧凑编码/回退方式的八种组合、缺省回退、禁用 retrieval、已构造子模型、Python/JSON 输入和完整错误路径；覆盖旧字段拒绝、倍率正数约束、查询聚类数量的严格整数与自适应值、保留策略组合、默认值来源、序列化和文档导出。警告仍来自 connector_pool.py 与 http_client.py 的既有 class Config。

config.tree.txt 已自动重新生成，保留“全量配置”标题；README 示例与说明、当前 S 和 D 第 11 节同步。本轮按 jw-review 对上述基线到工作区差异自检，核对单一回退决策、字段名与默认值映射、条件校验、输出文档及运行边界，未发现需追加代码修改的问题。配置与导出验证不代表未来 Runtime 适配或真实模型行为已经验证。

未修改业务代码、Runtime、引擎、Swarm、依赖或缓存逻辑；未运行格式化、静态检查或业务链路测试，未提交或推送。测试通过后仅完成文档收尾与索引同步，不重复运行未受影响的配置测试。


## 2026-11-02 配置树评审后四项归属与命名整理

### 确认、基线与实际决定

用户明确要求继续落实前次独立评审的后四项，保持默认值，并限制为配置、配置测试与相关文档；关联 D 第 12 节记录确认方案。本轮基线是上一节 293 项测试通过后的未提交工作区，HEAD 仍为 395a5df964c98c982a6231f1a549dbc3feb23a99，完整保留前四项改动及历史证据。本节替代上一节暂缓后四项的实施范围，当前路径由关联 S 统一维护。

- 将 RetrievalGenerationConfig 拆为 RetrievalSelectionConfig 与 RetrievalRequestBudgetConfig。selection 管理模式、解码约束和 logit 打分；request 管理 max_output_tokens=96 与 timeout_seconds=120.0。AgenticRequestConfig 继承预算字段并覆盖不可空的正数超时约束；自然语言检索继续接受 None。没有共享实例、覆盖值或新增后端包装；内部 RetrievalRequestConfig 仍只表达单次 top_k 覆盖。
- 将 cache_observability 从构建 prompt 移到 output.cache_metrics_enabled，保留 True。中文说明明确这是模型请求缓存命中、提示指纹统计及其展示；不等同于 request.caching，也不因缓存策略关闭而禁用观测。
- 将 DiscoveryBudgetConfig 的两个字段与只读 candidate_budget_tokens 合到 DiscoveryPromptConfig，删除旧类、登记和 budget 节点；优先对象列表、预算默认值、计算公式及派生值不进入输入/序列化的规则均保留。
- 公共策略按字符、token、秒统一字段名，完整迁移表见 S 第 3 节。保留所有默认值、类型和约束，尤其是画像字符数至少 40、构建 token 预算的 0、指纹输出预算与自然语言检索超时的 None；独立后端和采样字段不跟随改名。

前四项新增的 logit/紧凑编码组合校验继续生效，错误路径随节点改名为 retrieval.search.selection，错误提示指出 selection.logit.fallback_mode。旧 generation、Discovery budget、旧单位字段和缓存观测路径均被拒绝，无兼容别名；两个退役类不再存在于模块、登记或导出参考。模型总数仍为 63，公共 48、内部 15，__all__ 与业务包根导出不变。

### 验证与交接

本轮开始时保存源码、配置测试、配置树、公共默认值和 Schema 快照，位置为 /var/folders/xq/wcv2ptms57b92bkfwgbv40zw0000gn/T/symphony-config-naming-dh21b8y5。对照使用已声明的字段改名/归位关系，未把不同入口的值合并：**133 个公共叶子项的默认值全部一致，133 项 Schema 标量/容器约束和示例全部一致**。Schema 叶子比较仅排除允许更新的 title/description；另逐个比较 15 个内部模型的完整 Schema，结果完全一致，公共便利导出也一致。组合校验的行为与新错误路径由配置测试验证。

使用主仓已有 Python 3.11.15、Pydantic 2.12.5、pytest 9.0.2，PYTHONPATH 指向当前工作树：

```bash
source /Users/xiaoyulin/Documents/Huawei/code/agent-core/.venv/bin/activate
export PYTHONPATH=.:${PYTHONPATH:-}
python scripts/export_symphony_config.py
python -m pytest tests/unit_tests/symphony/test_config.py tests/unit_tests/symphony/test_config_export.py -q
```

结果：**339 passed，2 个既有 Pydantic 弃用警告，1.14 秒**。覆盖全部模型默认值与中文元数据、旧类/旧路径拒绝、请求超时可空性差异、各入口预算独立、四种缓存开关组合、提示预算取整与最小值、完整错误路径及往返；前轮的 logit 组合、倍率、聚类与单一回退策略验证继续通过。配置树自动生成并保留“全量配置”标题，导出脚本未改。

当前 S、README 示例及 D 第 12 节已同步；历史设计章节与前轮测试数字保留为当时事实。按 jw-review 对本轮快照到候选的配置、测试和文档差异自检，重点核对分层职责、旧参数映射、默认值/约束、入口隔离、组合校验和独立导出。业务模块、Runtime、引擎、Swarm、依赖、缓存签名和包根导出均未修改；配置验证不表示运行链路已完成迁移。

未运行格式化、静态检查或业务链路测试，未提交或推送。测试通过后仅进行文档及索引收尾，不再次运行未受影响的配置测试。
