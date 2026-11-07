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
updated: 2026-11-03
approved-date: 2026-10-31
domain: core
modules: [symphony]
files: [openjiuwen/symphony/config.py, tests/unit_tests/symphony/test_config.py, scripts/export_symphony_config.py, tests/unit_tests/symphony/test_config_export.py, openjiuwen/symphony/config.tree.txt, openjiuwen/symphony/AGENTS.md, openjiuwen/symphony/README.md]
related: [D-20261029-1619-skill-directory-graph-api]
tags: [symphony, configuration, pydantic]
scope: 分层纯配置模型、树形文档导出和配置验证；运行链路迁移留待后续
---

# Symphony 配置契约先行

## 1. 决策归属与确认

本设计记录用户已在会话中确认并明确要求实施的“Symphony 配置先行计划”。它承接 [Runtime 总体草案](../../proposals/D-20261029-1619-skill-directory-graph-api/design.md) 的配置部分，将配置模型和运行链路迁移拆为两个阶段。

2026-10-31，用户先要求多层配置、Pydantic 和中文说明，再明确要求先整理 config 文件，最后以“PLEASE IMPLEMENT THIS PLAN”确认本阶段范围。该确认只覆盖配置契约及验证，不表示整个 Runtime 重构方案已批准。评审者账号未提供，reviewers 不推断为 Git 作者。

## 2. 目标配置

在 `openjiuwen/symphony/config.py` 集中定义全部纯配置，采用 Pydantic v2。文件注明“目标配置，尚未接入运行链路”。初始公共配置按以下结构组合；挂载范围、路径和组合约束由第 8、10、11、12 节的用户确认方案更新：

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

本阶段新增目标配置模型和针对性验证。既有定义仍是当前生产行为的依据；不修改 Runtime、引擎、Swarm、包根公共导出、依赖文件或缓存逻辑。新旧定义暂时并存，不提供自动转换或接入别名。

下一阶段再按已审阅的配置层级更新消费者、保留有效参数和缓存身份、删除旧定义并接入 SymphonyRuntime(model, config)。在实际接入前，不能把新配置校验成功解释为对应对象读取器或运行能力已经实现。

## 5. 验收设计

1. 最小输入补全默认子配置；非法值的错误定位包含完整字段路径。
2. 所有纯配置模型能够导出 JSON Schema，并保留中文说明、约束和示例。
3. 配置能够经 JSON 字典和 JSON 文本往返，各实例不共享可变默认值。
4. 当前配置源的默认值逐项映射到目标位置；不同入口的默认差异得到保护。
5. 构造和序列化不访问文件系统或模型后端，现有公共配置导出仍指向原定义。
6. 验证限于配置契约，不运行格式化或静态检查。实际证据由开发 F 文档记录。


## 6. 2026-11-02 配置收敛确认

用户在子 Agent 检视后明确要求先实施公共导出收敛、检索配置复用、任务策略归位，并修复 method 校验；默认值保持不变，暂不接入运行链路。本节续写同一配置契约，公共四组、中文元数据及既有默认行为的决策继续有效。

- config 模块的公共导出限定为 SymphonyConfig 配置树的 9 个模型和 CapabilityType。公共与内部模型分别登记，完整登记仍供内部验证和文档工具显式读取，不随登记数量扩大公共导出。
- 独立检索服务复用渐进检索的字段结构，分别保留 top_k=10 和 top_k=5，不引入新的公共基类或统一默认对象。
- CapabilityIndexBuildConfig 的 preserve_previous_index_on_failure、strict_failure 归 task，与总时限同组；output 只管理生成预览等产物设置。目标配置不保留旧 output 路径别名；当前运行消费者不变。
- IndexBuildConfig 和 ResolvedIndexBuildConfig 保留整数编码 4。两者均须在字面量匹配前拒绝浮点数、字符串和布尔值，继续导出 integer/const=4 的 Schema。

验收补充：公共导出不混入内部模型；共享检索字段的覆盖、序列化和可变默认值隔离继续有效；两项失败策略的新路径可用且旧路径明确报错；字典与 JSON 输入均不能绕过 method 类型约束。解析快照去重、倍率调参范围和 Runtime 接入均留待后续决定。


## 7. 2026-11-02 树形配置文档导出确认

用户要求输出全部配置的树形结构，并在每个配置项后以 # 注释说明；随后明确选择“每次完成配置修改后，由开发流程自动运行生成脚本”。本节承接原配置元数据导出方向，先实施文本树，不引入 Markdown 文档框架。

- 新增 scripts/export_symphony_config.py，读取 Pydantic model_fields、字段默认值、中文说明和枚举，不另外维护一份字段清单。
- 生成 openjiuwen/symphony/config.tree.txt：公共树递归展开，内部模型逐一定义并引用子模型；注明必填、默认值和目标配置尚未接入运行链路。
- 导出只列可配置字段；长列表展示数量并指向源默认定义，不将只读派生属性当作输入字段。公共/内部边界以现有注册表为准。
- 在 Symphony 模块 AGENTS.md 中要求配置变更后于交付前自动重新生成；生成文件进入版本管理并由定向测试核对一致性。触发方式不包含文件监听器、Git hook 或 Runtime 导入副作用。
- 脚本按自身位置定位配置与默认输出，允许显式覆盖输出路径；独立加载纯配置文件，不依赖包根和运行模块。相同内容重复导出不重写文件。

验收：每个内部模型字段均被列出，公共树完整展开，中文注释和必填/None 区别正确；嵌套默认工厂覆盖、联合后端类型和递归引用能够展示；同一源重复导出内容稳定，生成文件与配置一致；从仓库外目录运行也能导出，且不导入 openjiuwen 运行模块。


## 8. 2026-11-02 用户可调配置并入统一入口

用户在审阅统一配置树建议后明确回复“可以，合并一下吧”。本节部分替代第 2、6 节的公共四组及内部策略独立输入决定；纯配置、旧消费者继续工作、默认值和不接入 Runtime 的边界仍有效。继续同一设计，不另建设计编号。

- 公共入口扩展为 capability、storage、retrieval、orchestration、flow、experience。仅 capability.root、capability.type、storage.artifact_root 必填，其余节点使用独立默认工厂补全。
- capability 增加 scan 与 fingerprint；指纹开放 extraction、evaluation、cache，协议版本与附加签名继续内部维护。提取开关只保留 capability.fingerprint.extraction.llm_enabled，替代原 orchestration.build.extraction_mode。
- retrieval.build 直接组合 structure、request、postprocess、equivalence、profiles、prompt、incremental、task、output；search 组合 top_k、traversal、render、generation；discovery 和 toolkit 保留各自语义及默认值。
- orchestration.build 保留整体 max_concurrency 并组合 matching；共识策略只保留 matching.require_consensus，不再提供第二个匹配并发配置。整体构图并发是上限，后续接入时指纹阶段并发还受 extraction.max_concurrency 限制，两者取较小值。
- flow 组合 enabled、acceptance、narrative；experience.build 复用聚类、执行、模式配置。Flow 默认 enabled=true 沿用独立模块默认，不表示本阶段启动服务或后台任务。
- 公共树构建仍采用深度 6、分支 128（最小 2）、并发 2、请求超时 420 秒、分类批量 32；检索 top_k=10、并行分支 3、请求超时 120 秒；图构建整体并发 1、指纹阶段上限 4。底层独立入口的不同默认值继续保留。
- TreeBuildSettings、ResolvedIndexBuildConfig、RuntimeRetrieverConfig、模型后端、协议标识及尚无统一入口的独立算法/调用参数继续独立。输入参数与派生设置不产生两个可写来源。
- 公共模型登记覆盖根 Schema 可达的所有子模型，用于验证和文档；config 模块的 __all__ 保留原核心配置类并增加 FlowConfig、ExperienceConfig，不随子模型数量扩大便利导出。包根导出不改。
- 目标配置的旧扁平路径不提供别名，按未知字段报完整路径；仍未接入运行消费者，后续迁移使用新规范路径。中文元数据同步并自动重新导出完整配置树。

验收：最小输入及深层部分覆盖能补全默认值；旧入口的默认值逐项映射到新的公共位置；重复路径、内部协议/后端/派生字段被拒绝；嵌套错误位置完整；所有配置可序列化往返、生成中文 Schema，可变默认值不共享；公共登记恰好覆盖根 Schema，内部登记继续完整；导出结果与配置一致。只执行配置及导出测试。


## 9. 2026-11-02 清理重复的独立配置模型

用户确认 GraphPlanningConfig 与公共规划策略重复后，明确要求“检查一下，还有没有类似的重复独立内部配置，清理一下”。在同一设计内收敛过渡镜像，公共六组结构、默认值、约束和运行链路不变；本节替代第 8 节继续在目标 config.py 中镜像解析设置的安排。

- 检查全部内部模型的字段来源、组合关系、默认差异和运行引用。删除只用于旧字段对照或重复解析快照、没有目标运行消费者的 FingerprintConfig、GraphPlanningConfig、GraphMatchingConfig、CapabilityIndexBuildConfig、TreeBuildSettings、ResolvedIndexTreeStructureConfig、ResolvedIndexBuildConfig、RuntimeRetrieverConfig。
- 指纹和规划默认值直接与公共节点及独立协议数据对照；索引 task/output 的验证直接进入公共 RetrievalBuildConfig，不为测试保留旧模型包装。
- 解析设置的真实实现保留在原运行模块；不删除其当前消费者。重复目标模型的唯一字段（例如树构建解析时的连续失败上限）仍由真实运行设置管理，不擅自增加到公共输入。
- 保留具有独立职责/默认值/约束的参数族：树底层请求与结构、独立索引工作流和提示协议、渐进/服务检索默认差异、模型后端、生成约束、单次请求以及动态树、一次性构建。不同入口的 1/180/20 与 2/420/32、top_k=5/10 不因清理混用。
- 根据本轮前已提出的文档用途区分，将默认 config.tree.txt 收敛为 SymphonyConfig 用户配置树；--include-internal 显式追加保留模型的开发参考。支持 --output 单独保存完整参考；开发流程仍自动生成默认用户文档。

验收：删除名称不再可从目标模块访问且不再登记；公共 Schema 和默认序列化结果与清理前完全一致；全部保留模型的约束、元数据、默认差异和往返继续验证；真实旧配置导出与消费者保持不变。默认导出仅含公共树，显式完整导出覆盖全部剩余内部字段，两种模式均可独立执行，重复生成不重写。仅运行配置与导出验证，不运行静态检查。


## 10. 2026-11-02 构建与检索配置进一步收敛

用户明确要求只梳理 config、业务代码后续修改，并选择“一起收敛”构建和检索包装，随后以 PLEASE IMPLEMENT THIS PLAN 确认本节方案。本节部分替代第 6、8、9 节因旧入口默认差异保留同义目标模型的决定；公共六组、既有公共默认值、中文元数据、配置先行和原业务定义继续工作的边界保持有效。

- 删除 TreeRequestConfig、TreeStructureConfig、IndexTreeStructureConfig、TreeBuildConfig、IndexTreeBuildConfig、TreeManagerConfig、TreePromptConfig、IndexBuildConfig、IndexArtifactConfig、TreeBuildOptions、ProgressiveRetrieverConfig、RetrieverConfig、DisclosureConfig，共 13 个目标模型，不留同义别名或替代包装。
- IndexRequestConfig 与 RetrievalTreeStructureConfig 直接定义完整字段；构建策略统一在 RetrievalBuildConfig，检索策略统一在 RetrievalSearchConfig。AgenticRenderConfig 直接继承 RetrievalRenderConfig 并显式保留工具浏览默认值。
- DynamicTreeConfig 通过 retrieval.build.dynamic 挂到公共树，只保留 rebalance_interval=50 和六个扩展倍率 1.5、0.7、1.7、1.3、6.0、12.0；移除结构副本与六个派生属性。后续运行层共用 retrieval.build.structure 计算阈值，本轮不实现转换或计算器。
- allow_fallback_tree=True 归 retrieval.build.task；verbose=False、show_tree=True、display_capability_root=None 归 retrieval.build.output，generate_tree_html=False 位置不变。新增字段沿用原目标模型默认值，不将旧业务入口的显式覆盖改为这些默认值。
- 旧工作流的 method 仅支持 TREE=4，目标配置不再提供该字段及专用类型/校验函数。提示指纹版本继续由原业务模块维护。模型由后续 Runtime 的 model 参数承接；独立后端联合类型仍保留，由 TypeAdapter 直接验证。
- 保留其他独立职责的配置。预期 63 个模型，其中公共树可达 48 个、内部 15 个；不扩大 __all__ 或包根公共导出。

验收：最小输入和深层覆盖能到达所有新归位字段；完整错误路径、序列化往返、可变默认值隔离及中文 Schema 继续成立。对照修改前快照，既有公共字段、默认值、类型、约束和示例不变，仅允许上述新增字段与说明变化；新归位默认值与原目标内部模型一致。删除名称不再出现在模块、登记表及导出模型参考中，真实业务配置与调用链不变。自动生成 config.tree.txt，保留“全量配置”标题；只运行配置和导出测试。


## 11. 2026-11-02 配置树评审后的四项语义整理

用户要求一个独立子智能体评估配置树，随后明确确认：“先处理前四项，保持默认值不变，只改 config.py、配置测试和相关文档，重新生成 config.tree.txt，暂时不改业务代码”。本节部分替代第 6 节保留 strict_failure 的目标决定，以及第 10 节六个倍率字段和原经验聚类字段的命名；六个顶层分组、节点归属、所有保留字段的默认值和配置先行边界仍有效。

1. 回退决策统一为 retrieval.build.task.allow_fallback_tree，默认 True；删除 strict_failure，不提供别名或第二个模式开关。preserve_previous_index_on_failure 默认 True，继续独立配置。后续运行迁移时，allow_fallback_tree=False 须承接旧严格模式的缺模型前置失败与错误报告，并禁止回退；存在可用旧索引且启用保留策略时，不能让降级结果覆盖旧索引。本阶段只声明策略，不检查模型配置、文件或当前索引状态。
2. retrieval.build.dynamic.expansion 下六个字段增加 _multiplier 后缀，依次为 max_capabilities_per_node_multiplier、expand_threshold_multiplier、early_stop_multiplier、lazy_split_multiplier、classification_batch_multiplier、structure_sample_multiplier；默认值继续为 1.5、0.7、1.7、1.3、6.0、12.0，仍要求正数。lazy_split 的基数为节点容量，其余为分支数；分类批量倍率与绝对批量上限不合并。
3. experience.build.clustering.capability_cluster_num 改为 query_clusters_per_capability_set，默认 None，继续接受正整数或 None。说明明确为“每个能力集合内成功查询的聚类数”，None 表示自适应数量；不再暗示能力类别数或经验库总簇数。
4. RetrievalSearchConfig 增加纯配置组合校验：mode=logit_selection、compact_codes_enabled=False、fallback_mode=error（含省略时的默认值）的组合拒绝，允许显式 fallback_mode=generate。错误定位到 generation 节点，并说明相关字段及修正方式；从 SymphonyConfig 构造时保留 retrieval.search 前缀。禁用 retrieval 仍校验配置，后端打分能力留给后续运行层检查。

本轮不调整 search.generation/request、Discovery 的 budget/prompt、缓存观测归属或其余字段命名，不新增模型、公共导出或业务适配层。旧目标字段按未知字段拒绝，真实业务定义中的旧字段继续工作；上述变化会收紧目标输入集合，但不改变默认行为。

验收：对照变更前配置快照，除退役 strict_failure 和七个明确改名字段外，默认序列化结果一致；标量类型和约束保持一致，检索仅收紧指定组合。覆盖旧字段拒绝、深层覆盖、完整错误路径、字典/JSON 往返、子模型输入和禁用节点校验；导出的 Schema/配置树包含新的中文语义与条件说明。运行配置和导出测试，自动生成 config.tree.txt；不得修改业务代码、运行格式化或静态检查。


## 12. 2026-11-02 请求预算、提示配置与单位命名整理

用户继续确认实施独立评审的后四项，要求“保持默认值不变，只改配置、配置测试和相关文档，重新生成 config.tree.txt，暂时不改业务代码”。本节续写同一设计，替代第 11 节暂缓这四项的范围限制，以及旧 generation/budget/缓存观测位置和单位字段命名；保留六个顶层分组及前四项整理后的语义。

1. retrieval.search.generation 改为 selection，类型为 RetrievalSelectionConfig，只管理 mode、trie_constrained_decoding_enabled 与 logit；输出和超时预算移到 search.request，类型为 RetrievalRequestBudgetConfig，字段为 max_output_tokens=96、timeout_seconds=120.0。工具检索的 AgenticRequestConfig 复用预算字段定义并保留 timeout_seconds 只能为正数的约束；自然语言检索继续接受 None。配置实例和入口覆盖独立，旧 RetrievalGenerationConfig 不保留别名。logit 与紧凑编码的组合规则保持第 11 节语义，错误路径相应改为 retrieval.search.selection。
2. retrieval.build.prompt.cache_observability 移到 build.output.cache_metrics_enabled，默认 True。prompt 只保留确定性和结构发现种子；指标观测与 request.caching 的缓存策略开关不合并、不相互推导，四种组合仍合法。
3. retrieval.discovery.budget 的 candidate_budget_ratio=0.01、context_window_tokens=200000 直接并入 DiscoveryPromptConfig，与原 preferred_capability_ids 同组；删除 DiscoveryBudgetConfig 和 budget 节点。只读 candidate_budget_tokens 随之归 prompt，派生公式、至少为 1 的规则和非序列化边界不变，output 独立保留。
4. 公共策略统一单位命名：指纹正文 body_limit → max_body_chars、输出 max_tokens → max_output_tokens，评估 evidence_text_limit → max_evidence_chars；画像 description_limit/rule_limit → max_description_chars/max_rule_chars；构建 context_window → context_window_tokens。检索和工具请求统一 max_output_tokens/timeout_seconds；已有明确单位的其他字段保持原样，不修改独立模型后端、采样或单次调用模型。所有数值、None/0 含义、标量类型与约束保持不变。

不增加兼容别名或第二个可写路径。预期公共可达模型仍为 48 个、内部 15 个、合计 63 个；不扩大 __all__ 或包根导出。相关运行参数的字段映射、缓存签名、Runtime 和消费者迁移仍留到后续。

验收：按明确路径映射比较变更前后全部公共叶子项的默认值和 Schema 约束，不合并不同入口的值；15 个内部模型的 Schema 保持一致。覆盖旧路径/旧类退役、请求与选择策略分离、超时可空性差异、缓存策略与观测独立、Discovery 提示预算派生、深层覆盖、完整错误路径、字典/JSON 往返和中文说明。自动重新生成 config.tree.txt 并只执行配置、导出测试，不运行格式化或静态检查。


## 13. 2026-11-03 能力退役的后继决定

用户批准连代码与配置一起删除旧模型检索、分支浏览和独立经验库，方案由 [D-20261103-1438-symphony-capability-retirement](../D-20261103-1438-symphony-capability-retirement/design.md) 承接。此前有关这些能力保留的选择被局部替代；嵌套结构、中文元数据、保留默认值和目标配置尚未接入 Runtime 等约定继续有效。此前章节保留其当时语境。
