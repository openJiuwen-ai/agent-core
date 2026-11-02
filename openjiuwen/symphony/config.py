# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Symphony 目标配置，尚未接入运行链路。

公共入口为 SymphonyConfig，按 capability、storage、retrieval、orchestration
组合子模型。其他模型集中描述内部模块的纯配置，不自动成为 Runtime 的输入。
现有模块配置仍是运行行为的依据；本文件不读取环境、扫描目录或创建模型客户端。

字段的中文 title、description 和约束用于后续生成配置文档。旧入口默认值不同的
配置族分别保留；配置中的 capability 命名不修改现有产物字段或工具标识。
客户端实例、线程池、取消/进度回调、扫描结果和前缀缓存句柄属于运行参数，
不进入这些可序列化模型。文件中的路径只保存文本，不检查存在性或解析绝对路径。
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue


def _path_text(value: object) -> object:
    """接受 pathlib 路径或非空文本，不访问文件系统。"""
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    return value


ConfigPath = Annotated[str, BeforeValidator(_path_text), Field(min_length=1)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveFloat = Annotated[float, Field(gt=0)]
Probability = Annotated[float, Field(ge=0, le=1)]
CapabilityType = Literal["skill", "expert", "connector", "mcp"]
RootCategoryInput = list[str | dict[str, JsonValue]] | ConfigPath | None


class SymphonyConfigModel(BaseModel):
    """纯配置模型基类：严格类型、拒绝未知字段、校验默认值并禁止属性重新赋值。

    嵌套列表和字典仍可修改，各实例使用独立的默认值；frozen 不代表深层容器冻结。
    需要保留既有可变配置行为的模型显式覆盖 frozen，并开启赋值校验。
    """

    model_config = ConfigDict(
        extra="forbid", strict=True, validate_default=True, frozen=True, allow_inf_nan=False,
    )


# 公共配置：沿用已确认的 Runtime 配置草案，不提前绑定现有服务。
class CapabilityConfig(SymphonyConfigModel):
    """一个 Runtime 所管理的对象定义集合；不同类型使用独立集合。"""

    root: ConfigPath = Field(
        title="对象根目录", description="静态对象定义所在目录；相对路径的解析由后续 Runtime 接入负责。",
        examples=["./capabilities"],
    )
    type: CapabilityType = Field(
        title="对象类型", description="对象定义格式；枚举值表达目标支持范围，不表示对应读取器已经实现。",
        examples=["skill", "expert"],
    )


class StorageConfig(SymphonyConfigModel):
    """Runtime 产物存储位置；路径检查和目录创建由后续运行层负责。"""

    artifact_root: ConfigPath = Field(
        title="产物根目录", description="保存 inventory、缓存、tree 和 graph 的根目录；模型校验不会创建目录。",
        examples=["./.symphony"],
    )


class RetrievalBuildConfig(SymphonyConfigModel):
    """公共树构建策略；默认值来自现有索引 toolkit 的构建入口。"""

    max_depth: PositiveInt = Field(6, title="分类树深度", description="分类树最大深度，与文件目录扫描深度无关。")
    branching_factor: Annotated[int, Field(ge=2)] = Field(
        128, title="分类分支上限", description="每个分类节点的最大分支数；公共入口至少允许两个分支。",
    )
    max_concurrency: PositiveInt = Field(
        2, title="构建并发上限", description="单次树构建最多并发的模型请求数，映射旧 max_workers。",
    )
    request_timeout_seconds: PositiveFloat = Field(
        420.0, title="构建请求超时", description="单次模型请求超时，单位秒；不限制整个构建任务时长。",
    )


class RetrievalSearchConfig(SymphonyConfigModel):
    """公共自然语言检索策略；候选上限采用 toolkit 默认值，并行度采用渐进检索默认值。"""

    top_k: PositiveInt = Field(
        10, title="候选数量上限", description="每次检索最多返回的对象数；可在调用时覆盖，不影响已有索引。",
        examples=[5, 10],
    )
    max_parallel_branches: PositiveInt = Field(
        3, title="并行分支上限", description="一次查询最多并行探索的树分支数；1 表示串行。",
    )
    request_timeout_seconds: PositiveFloat = Field(
        120.0, title="检索请求超时", description="单次检索模型请求超时，单位秒。",
    )


class RetrievalConfig(SymphonyConfigModel):
    """公共检索配置；树构建和查询分别配置，启用不会自动构建索引。"""

    enabled: bool = Field(True, title="启用检索", description="允许使用检索入口；独立于编排开关。")
    build: RetrievalBuildConfig = Field(
        default_factory=RetrievalBuildConfig, title="索引构建", description="显式构建分类树时使用的策略。",
    )
    search: RetrievalSearchConfig = Field(
        default_factory=RetrievalSearchConfig, title="候选检索", description="查询已有分类树时使用的默认策略。",
    )


class OrchestrationBuildConfig(SymphonyConfigModel):
    """公共图构建策略；统一并发上限是目标选择，不改变现有独立指纹服务的默认并发。"""

    extraction_mode: Literal["metadata", "llm"] = Field(
        "metadata", title="语义提取方式",
        description="metadata 使用确定性元数据提取；llm 追加模型语义提取。关系构图仍需要模型。",
    )
    max_concurrency: PositiveInt = Field(
        1, title="构图并发上限",
        description="目标单次图构建各阶段共用的模型请求上限；独立指纹配置仍保留并发 4。",
    )
    require_consensus: bool = Field(
        True, title="要求关系共识", description="沿用当前 Core 的两次关系判定一致性要求。",
    )


class OrchestrationPlanConfig(SymphonyConfigModel):
    """公共规划策略；生成计划，不执行对象。"""

    mode: Literal["fast", "beam"] = Field("fast", title="规划模式", description="fast 单次规划；beam 束搜索规划。")
    top_k: PositiveInt = Field(
        3, title="方案保留上限", description="仅 beam 使用，控制搜索保留宽度与最多输出方案数；fast 不使用。",
    )
    max_depth: PositiveInt = Field(4, title="规划展开深度", description="规划搜索深度，与分类树或目录扫描深度无关。")
    dynamic_graph_enabled: bool = Field(
        False, title="使用动态观测", description="规划时是否使用已有图观测结果；不表示自动执行或自动构图。",
    )


class OrchestrationConfig(SymphonyConfigModel):
    """公共编排配置；关系阈值同时用于图构建和规划。"""

    enabled: bool = Field(True, title="启用编排", description="允许使用编排入口；启用本身不触发构图。")
    min_edge_confidence: Probability = Field(
        0.7, title="最低关系置信度", description="构图接纳关系和规划使用关系的共同阈值，取值范围 0 到 1。",
    )
    build: OrchestrationBuildConfig = Field(
        default_factory=OrchestrationBuildConfig, title="图构建", description="显式构建关系图时的策略。",
    )
    plan: OrchestrationPlanConfig = Field(
        default_factory=OrchestrationPlanConfig, title="计划生成", description="使用已有图生成编排计划时的策略。",
    )


class SymphonyConfig(SymphonyConfigModel):
    """Symphony 目标公共配置；尚未被现有 SymphonyRuntime 接收。"""

    capability: CapabilityConfig = Field(title="对象集合", description="显式声明对象目录和类型。")
    storage: StorageConfig = Field(title="产物存储", description="显式声明本集合的产物根目录。")
    retrieval: RetrievalConfig = Field(
        default_factory=RetrievalConfig, title="检索能力", description="分类树构建和候选检索的默认配置。",
    )
    orchestration: OrchestrationConfig = Field(
        default_factory=OrchestrationConfig, title="编排能力", description="关系图构建和计划生成的默认配置。",
    )


# 目录扫描与指纹：默认值来自 shared/fingerprint/{scanner,parser,settings}.py。
class ScanLimitsConfig(SymphonyConfigModel):
    """目录发现和资源读取预算；单位分别为条目数和字节。"""

    max_asset_files: PositiveInt = Field(10_000, title="单对象资源数", description="单个对象可纳入哈希的资源文件数上限。")
    max_asset_file_bytes: PositiveInt = Field(
        256 * 1024 * 1024, title="单文件字节上限", description="单个资源文件的读取上限，单位字节。",
    )
    max_total_asset_bytes: PositiveInt = Field(
        512 * 1024 * 1024, title="单对象资源字节上限", description="单个对象资源文件的累计字节上限。",
    )
    max_scan_directories: PositiveInt = Field(50_000, title="扫描目录上限", description="一次扫描允许访问的目录总数。")
    max_directory_entries: PositiveInt = Field(20_000, title="目录条目上限", description="单个目录允许检查的条目数。")
    max_entrypoints: PositiveInt = Field(10_000, title="入口文件上限", description="一次扫描允许发现的对象入口文件数。")
    max_scan_asset_files: PositiveInt = Field(100_000, title="扫描资源数上限", description="一次扫描累计处理的资源文件数。")
    max_scan_asset_bytes: PositiveInt = Field(
        2 * 1024 * 1024 * 1024, title="扫描资源字节上限", description="一次扫描累计读取资源的字节上限。",
    )


class ManifestParseConfig(SymphonyConfigModel):
    """对象入口文档解析预算；配置模型本身不读取文档。"""

    max_manifest_bytes: PositiveInt = Field(
        4 * 1024 * 1024, title="入口文档大小上限", description="入口文档允许读取的最大字节数。",
    )
    max_frontmatter_depth: PositiveInt = Field(32, title="元数据嵌套深度", description="前置元数据允许的最大嵌套深度。")
    max_frontmatter_items: PositiveInt = Field(10_000, title="元数据条目上限", description="前置元数据累计条目数量上限。")


class DirectoryScanConfig(SymphonyConfigModel):
    """内部目录扫描配置；对象目录和类型由集合配置提供，无需公共 Source 对象。"""

    max_depth: NonNegativeInt | None = Field(
        None, title="目录扫描深度", description="目录递归深度；None 表示不单独限制深度，仍受扫描预算约束。",
    )
    limits: ScanLimitsConfig = Field(default_factory=ScanLimitsConfig, title="扫描预算", description="目录和资源读取预算。")
    manifest: ManifestParseConfig = Field(
        default_factory=ManifestParseConfig, title="入口解析", description="入口文档和前置元数据解析预算。",
    )


class FingerprintExtractionConfig(SymphonyConfigModel):
    """独立指纹服务的提取参数；LLM 语义提取默认关闭。"""

    llm_enabled: bool = Field(False, title="模型语义提取", description="是否在确定性元数据提取之外调用模型提取语义。")
    batch_size: PositiveInt = Field(16, title="提取批量大小", description="每批处理的对象数。")
    max_concurrency: PositiveInt = Field(4, title="提取并发上限", description="独立指纹服务的模型请求并发上限。")
    body_limit: PositiveInt = Field(16_000, title="正文字符上限", description="送入语义提取的对象正文字符数上限。")
    max_tokens: PositiveInt = Field(2_048, title="提取输出上限", description="提取模型单次输出的 token 数上限。")
    timeout_seconds: PositiveFloat | None = Field(
        None, title="提取请求超时", description="单次提取请求超时，单位秒；None 表示沿用模型适配器策略。",
    )


class FingerprintEvaluationConfig(SymphonyConfigModel):
    """指纹评估参数；静态评估不依赖模型评估开关。"""

    llm_enabled: bool = Field(False, title="模型评估", description="是否追加模型辅助评估。")
    evidence_text_limit: PositiveInt = Field(512, title="证据文本长度", description="单条评估证据文本的字符数上限。")


class FingerprintCacheConfig(SymphonyConfigModel):
    """指纹缓存策略；真实缓存读写仍由现有服务负责。"""

    enabled: bool = Field(True, title="启用指纹缓存", description="是否允许复用符合身份与内容校验的指纹缓存。")


class FingerprintProtocolConfig(SymphonyConfigModel):
    """指纹版本与签名输入；仅记录现有默认值，不在此生成运行缓存签名。"""

    schema_version: str = Field("1.0", title="指纹结构版本", description="现有指纹产物的结构版本。", min_length=1)
    extraction_version: str = Field(
        "symphony-capability-extraction-v2", title="提取协议版本", description="现有语义提取协议标识。", min_length=1,
    )
    evaluation_version: str = Field(
        "symphony-capability-evaluation-v1", title="评估协议版本", description="现有评估协议标识。", min_length=1,
    )
    configuration_signature: str = Field(
        "", title="附加配置签名", description="独立指纹服务允许附加的配置身份；空字符串表示未提供。",
    )


class FingerprintConfig(SymphonyConfigModel):
    """独立指纹纯配置，分组对应现有 FingerprintSettings；不挂载到公共 Runtime 配置。"""

    extraction: FingerprintExtractionConfig = Field(
        default_factory=FingerprintExtractionConfig, title="语义提取", description="对象元数据与模型提取策略。",
    )
    evaluation: FingerprintEvaluationConfig = Field(
        default_factory=FingerprintEvaluationConfig, title="指纹评估", description="评估方式及证据文本上限。",
    )
    cache: FingerprintCacheConfig = Field(
        default_factory=FingerprintCacheConfig, title="缓存策略", description="独立指纹缓存的开关。",
    )
    protocol: FingerprintProtocolConfig = Field(
        default_factory=FingerprintProtocolConfig, title="协议身份", description="指纹协议版本及附加签名输入。",
    )


# 独立模块：保留 orchestration/config.py、discovery/config.py、experience/models.py 的默认值。
class GraphPlanningConfig(OrchestrationPlanConfig):
    """现有内部规划器的配置族，额外保留构图和规划共用的阈值。"""

    min_edge_confidence: Probability = Field(
        0.7, title="最低关系置信度", description="现有内部规划入口使用的关系置信度阈值。",
    )


class GraphMatchingConfig(SymphonyConfigModel):
    """现有关系匹配的内部默认值，来自 orchestration.service 的配置归一化。"""

    batch_size: PositiveInt = Field(12, title="关系匹配批量", description="单批关系匹配的处理数量。")
    max_concurrency: PositiveInt = Field(1, title="关系匹配并发", description="关系匹配请求并发上限。")
    require_consensus: bool = Field(True, title="关系共识要求", description="是否要求两次关系判定一致。")
    max_candidates_per_capability_relation: PositiveInt = Field(
        32, title="单对象关系候选上限", description="每个对象关系允许保留的候选数，原字段含 skill 命名。",
    )
    max_port_mappings_per_candidate: PositiveInt = Field(
        12, title="端口映射候选上限", description="每个关系候选允许的输入输出端口映射数量。",
    )
    max_exact_io_pair_fanout: PositiveInt = Field(
        64, title="精确输入输出扩展上限", description="精确输入输出匹配产生的候选扇出上限。",
    )


class FlowAcceptanceConfig(SymphonyConfigModel):
    """Flow 组合能力包的接纳条件，按能力包整体统计判定。"""

    min_successes: PositiveInt = Field(1, title="最少成功次数", description="接纳能力包所需的成功记录数。")
    min_pack_success_rate: Probability = Field(0.8, title="最低成功率", description="能力包整体成功率下限。")


class FlowNarrativeConfig(SymphonyConfigModel):
    """Flow 经验叙述的示例预算。"""

    max_examples: PositiveInt = Field(5, title="叙述示例上限", description="每个能力包叙述保留的最多示例数。")


class FlowConfig(SymphonyConfigModel):
    """对应现有 SymphonyFlowConfig 的独立配置，未接入新 Runtime 配置树。"""

    enabled: bool = Field(True, title="启用经验沉淀", description="是否启用 Flow 的经验沉淀与能力包处理。")
    acceptance: FlowAcceptanceConfig = Field(
        default_factory=FlowAcceptanceConfig, title="接纳条件", description="能力包的成功次数和成功率要求。",
    )
    narrative: FlowNarrativeConfig = Field(
        default_factory=FlowNarrativeConfig, title="叙述预算", description="经验叙述示例的数量限制。",
    )


class DiscoveryBudgetConfig(SymphonyConfigModel):
    """Discovery 候选提示预算；模型上下文大小必须由调用方明确提供或使用现有默认值。"""

    candidate_budget_ratio: Annotated[float, Field(gt=0, le=1)] = Field(
        0.01, title="候选预算比例", description="候选提示可占用的上下文比例，范围大于 0 且不超过 1。",
    )
    context_window_tokens: PositiveInt = Field(
        200_000, title="上下文窗口", description="用于计算候选预算的上下文容量，单位 token。",
    )

    @property
    def candidate_budget_tokens(self) -> int:
        """按现有规则计算候选 token 预算，最少为 1。"""
        return max(1, int(self.context_window_tokens * self.candidate_budget_ratio))


class DiscoveryOutputConfig(SymphonyConfigModel):
    """Discovery 返回内容和列表分页的预算。"""

    max_chars: PositiveInt = Field(12_000, title="输出字符上限", description="一次发现结果的最多输出字符数。")
    max_results: PositiveInt = Field(10, title="结果数量上限", description="一次发现请求返回的最多结果数。")
    max_list_entries: PositiveInt = Field(40, title="列表条目上限", description="一次列表展示的最多条目数。")
    incremental_notice_max_chars: PositiveInt = Field(
        4_000, title="增量提示字符上限", description="增量变化提示的最多字符数。",
    )


class DiscoveryPromptConfig(SymphonyConfigModel):
    """Discovery 提示中的优先对象；现有真实对象标识保持原值。"""

    preferred_capability_ids: list[str] = Field(
        default_factory=lambda: ["skill-creator", "swarmskill-creator", "symphony-assistant"],
        title="优先对象标识", description="提示优先展示的对象标识列表，顺序有意义；不表示已授权执行。",
    )


class DiscoveryConfig(SymphonyConfigModel):
    """Discovery 的纯配置；不包含应用 YAML 加载和模型上下文推断。"""

    budget: DiscoveryBudgetConfig = Field(
        default_factory=DiscoveryBudgetConfig, title="候选预算", description="按上下文容量计算候选提示预算。",
    )
    output: DiscoveryOutputConfig = Field(
        default_factory=DiscoveryOutputConfig, title="输出预算", description="发现结果、列表和增量提示的限制。",
    )
    use_existing_index: bool = Field(False, title="使用已有索引", description="是否使用已经构建的检索索引辅助发现。")
    prompt: DiscoveryPromptConfig = Field(
        default_factory=DiscoveryPromptConfig, title="提示策略", description="优先对象等发现提示设置。",
    )


class ExperienceClusteringConfig(SymphonyConfigModel):
    """经验库构建的聚类设置。"""

    min_cluster_size: PositiveInt = Field(1, title="最小聚类规模", description="一个聚类至少包含的记录数。")
    capability_cluster_num: PositiveInt | None = Field(
        None, title="对象聚类数", description="指定对象聚类数量；None 表示由现有构建逻辑决定。",
    )


class ExperienceExecutionConfig(SymphonyConfigModel):
    """经验库构建的执行和待写入预算。"""

    max_concurrency: PositiveInt = Field(8, title="经验构建并发", description="经验构建工作并发上限，映射旧 max_workers。")
    pending_flush_threshold: PositiveInt = Field(
        20, title="待写入批量阈值", description="待写入经验累计达到此数量时触发刷新。",
    )


class ExperiencePatternConfig(SymphonyConfigModel):
    """经验模式提炼与合并设置。"""

    max_success_examples: PositiveInt = Field(20, title="成功示例上限", description="模式提炼保留的成功示例数。")
    min_hits: PositiveInt = Field(1, title="最少命中次数", description="生成模式所需的最少命中次数。")
    merge_threshold: Probability = Field(0.9, title="模式合并阈值", description="模式相似度达到此阈值时允许合并。")
    query_examples_count: PositiveInt = Field(5, title="查询示例数量", description="每个经验模式生成的查询示例数。")


class ExperienceBankBuildConfig(SymphonyConfigModel):
    """经验库构建配置；保留原配置可修改的性质，并对赋值进行校验。"""

    model_config = ConfigDict(frozen=False, validate_assignment=True)
    clustering: ExperienceClusteringConfig = Field(
        default_factory=ExperienceClusteringConfig, title="聚类策略", description="经验记录的聚类设置。",
    )
    execution: ExperienceExecutionConfig = Field(
        default_factory=ExperienceExecutionConfig, title="执行预算", description="经验构建并发和写入批量设置。",
    )
    patterns: ExperiencePatternConfig = Field(
        default_factory=ExperiencePatternConfig, title="模式提炼", description="经验模式的提炼和合并设置。",
    )


# 树构建：保留底层树、索引工作流、agent toolkit 三个入口各自的默认值。
class TreePostprocessConfig(SymphonyConfigModel):
    """分类树构建后的处理策略。"""

    enabled: bool = Field(True, title="启用后处理", description="是否执行分类树后处理。")
    max_passes: NonNegativeInt = Field(1, title="后处理轮数", description="最多执行的后处理轮数；0 表示不执行。")
    min_capabilities: Annotated[int, Field(ge=2)] = Field(
        6, title="后处理对象阈值", description="达到此对象数量才进行后处理，映射旧 min_skills。",
    )


class TreeEquivalenceConfig(SymphonyConfigModel):
    """分类树等价对象分组策略。"""

    enabled: bool = Field(False, title="启用等价分组", description="是否尝试在父分类内合并等价对象。")
    max_groups_per_parent: Annotated[int, Field(ge=2)] = Field(
        6, title="父分类分组上限", description="每个父分类最多保留的等价组数。",
    )
    allow_singleton_groups: bool = Field(True, title="允许单对象组", description="等价分组是否保留只含一个对象的组。")
    min_lexical_similarity: Probability = Field(0.0, title="最低词面相似度", description="等价候选的词面相似度阈值。")


class CapabilityProfilesConfig(SymphonyConfigModel):
    """分类树对象画像与选择规则提取设置。"""

    enabled: bool = Field(False, title="启用对象画像", description="是否在分类前生成对象语义画像。")
    select_rules_enabled: bool = Field(True, title="提取选择规则", description="生成画像时是否同时提取选择和排除规则。")
    batch_size: PositiveInt = Field(48, title="画像提取批量", description="每批画像提取处理的对象数。")
    description_limit: Annotated[int, Field(ge=40)] = Field(
        140, title="画像描述长度", description="对象画像描述的字符数上限。",
    )
    rule_limit: Annotated[int, Field(ge=40)] = Field(120, title="选择规则长度", description="单条画像选择规则的字符数上限。")


class TreePromptConfig(SymphonyConfigModel):
    """树构建的提示确定性、协议和缓存观测设置。"""

    deterministic: bool = Field(True, title="确定性提示", description="是否使用稳定排序和确定性提示构造。")
    discovery_seed: int = Field(42, title="结构发现种子", description="分类结构发现过程的随机种子。")
    fingerprint_version: str = Field("v1", title="提示指纹版本", description="提示指纹协议标识。", min_length=1)
    cache_observability: bool = Field(True, title="缓存观测", description="是否记录树构建缓存的观测信息。")


class TreeIncrementalConfig(SymphonyConfigModel):
    """索引增量更新策略；比率上界沿用现有逻辑，不额外限定为 1。"""

    max_change_ratio: Annotated[float, Field(ge=0)] = Field(
        0.25, title="增量变化比例", description="允许使用增量构建的最大对象变化比例。",
    )
    min_add_confidence: Probability = Field(0.18, title="新增分类置信度", description="新增对象归入已有分类的最低置信度。")
    min_add_confidence_margin: Probability = Field(
        0.04, title="新增分类置信度差", description="最佳分类相对其他候选所需的最小置信度差。",
    )
    branch_imbalance_ratio: Annotated[float, Field(ge=1)] = Field(
        3.0, title="分支不均衡阈值", description="分支规模不均衡达到此比率时需要重新评估结构。",
    )


class TreeRequestConfig(SymphonyConfigModel):
    """底层 TreeBuildConfig 的请求默认值：单并发、180 秒超时、分类批量 20。"""

    max_concurrency: PositiveInt = Field(1, title="树构建并发", description="底层树构建模型请求的并发上限。")
    caching: bool = Field(False, title="树构建缓存", description="是否启用底层树构建缓存。")
    max_retries: NonNegativeInt = Field(2, title="最大重试次数", description="模型请求失败后的最多重试次数。")
    timeout_seconds: PositiveFloat = Field(180.0, title="请求超时", description="单次模型请求超时，单位秒。")
    context_window: NonNegativeInt = Field(0, title="构建上下文窗口", description="上下文 token 预算；0 表示未显式指定。")
    max_output_tokens: NonNegativeInt = Field(0, title="构建输出预算", description="模型输出 token 上限；0 表示未显式指定。")
    classification_batch_limit: PositiveInt = Field(20, title="分类批量上限", description="每次分类请求处理的最多对象数。")


class IndexRequestConfig(TreeRequestConfig):
    """索引工作流及 toolkit 的请求默认值，保留与底层树构建入口的差异。"""

    max_concurrency: PositiveInt = Field(2, title="索引构建并发", description="索引构建模型请求的并发上限。")
    timeout_seconds: PositiveFloat = Field(420.0, title="索引请求超时", description="单次索引构建模型请求超时，单位秒。")
    classification_batch_limit: PositiveInt = Field(32, title="索引分类批量", description="索引分类请求处理的最多对象数。")


class TreeBuildConfig(SymphonyConfigModel):
    """底层分类树构建配置，对应 retrieval/build/tree/schema.py 的配置族。"""

    request: TreeRequestConfig = Field(
        default_factory=TreeRequestConfig, title="请求策略", description="底层构建的并发、重试、上下文和批量限制。",
    )
    postprocess: TreePostprocessConfig = Field(
        default_factory=TreePostprocessConfig, title="树后处理", description="分类树后处理策略。",
    )
    equivalence: TreeEquivalenceConfig = Field(
        default_factory=TreeEquivalenceConfig, title="等价分组", description="等价对象分组策略。",
    )
    profiles: CapabilityProfilesConfig = Field(
        default_factory=CapabilityProfilesConfig, title="对象画像", description="对象画像和选择规则提取策略。",
    )
    prompt: TreePromptConfig = Field(
        default_factory=TreePromptConfig, title="提示策略", description="构建提示的确定性与身份设置。",
    )


class IndexTreeBuildConfig(TreeBuildConfig):
    """索引工作流使用的树构建配置，默认请求预算与底层入口分别保留。"""

    request: IndexRequestConfig = Field(
        default_factory=IndexRequestConfig, title="索引请求策略", description="索引工作流和 toolkit 使用的请求预算。",
    )


class TreeStructureConfig(SymphonyConfigModel):
    """底层树管理器的结构设置，默认分支数保留为 8。"""

    branching_factor: PositiveInt = Field(8, title="底层分支上限", description="底层树管理器每个节点的最大分支数。")
    max_depth: PositiveInt = Field(6, title="底层树深度", description="底层分类树的最大深度。")
    root_categories: dict[str, JsonValue] | None = Field(
        None, title="已解析根分类", description="已归一化的根分类映射；None 表示由构建过程发现分类。",
    )


class IndexTreeStructureConfig(SymphonyConfigModel):
    """索引入口的树结构设置；根分类可传 JSON 数据或配置文件路径文本。"""

    branching_factor: PositiveInt = Field(128, title="索引分支上限", description="索引入口每个分类节点的最大分支数。")
    max_depth: PositiveInt = Field(6, title="索引树深度", description="索引分类树的最大深度。")
    root_categories: RootCategoryInput = Field(
        None, title="根分类输入", description="根分类列表或配置文件路径文本；模型本身不读取路径。",
    )


class TreeManagerConfig(SymphonyConfigModel):
    """底层树管理器配置，由结构和构建策略组合。"""

    structure: TreeStructureConfig = Field(
        default_factory=TreeStructureConfig, title="树结构", description="底层分类树结构设置。",
    )
    build: TreeBuildConfig = Field(default_factory=TreeBuildConfig, title="树构建", description="底层分类树构建策略。")


class TreeExpansionConfig(SymphonyConfigModel):
    """动态树派生阈值的倍率，沿用 schema.py 中的现有常量。"""

    max_capabilities_per_node: PositiveFloat = Field(1.5, title="节点容量倍率", description="节点容量相对分支数的倍率。")
    expand_threshold: PositiveFloat = Field(0.7, title="扩展阈值倍率", description="节点扩展阈值相对分支数的倍率。")
    early_stop: PositiveFloat = Field(1.7, title="提前停止倍率", description="提前停止对象数相对分支数的倍率。")
    lazy_split: PositiveFloat = Field(1.3, title="延迟拆分倍率", description="延迟拆分阈值相对节点容量的倍率。")
    classification_batch: PositiveFloat = Field(6.0, title="分类批量倍率", description="分类批量相对分支数的倍率。")
    structure_sample: PositiveFloat = Field(12.0, title="结构采样倍率", description="结构采样数量相对分支数的倍率。")


class DynamicTreeConfig(SymphonyConfigModel):
    """动态树配置，保留原有可修改配置和派生阈值的行为。"""

    model_config = ConfigDict(frozen=False, validate_assignment=True)
    structure: TreeStructureConfig = Field(
        default_factory=TreeStructureConfig, title="动态树结构", description="动态树的分支、深度和根分类。",
    )
    rebalance_interval: PositiveInt = Field(50, title="重平衡间隔", description="触发结构重平衡的操作计数间隔。")
    expansion: TreeExpansionConfig = Field(
        default_factory=TreeExpansionConfig, title="派生阈值", description="容量、扩展和采样阈值的倍率。",
    )

    @property
    def max_capabilities_per_node(self) -> int:
        """单节点容量，保持现有向下取整规则。"""
        return int(self.structure.branching_factor * self.expansion.max_capabilities_per_node)

    @property
    def expand_threshold(self) -> int:
        """节点扩展阈值。"""
        return int(self.structure.branching_factor * self.expansion.expand_threshold)

    @property
    def early_stop_capability_count(self) -> int:
        """提前停止阈值。"""
        return int(self.structure.branching_factor * self.expansion.early_stop)

    @property
    def lazy_split_threshold(self) -> int:
        """延迟拆分阈值。"""
        return int(self.max_capabilities_per_node * self.expansion.lazy_split)

    @property
    def classification_batch_size(self) -> int:
        """分类批量派生值。"""
        return int(self.structure.branching_factor * self.expansion.classification_batch)

    @property
    def structure_sample_size(self) -> int:
        """结构采样数量派生值。"""
        return int(self.structure.branching_factor * self.expansion.structure_sample)


class TreeBuildSettings(SymphonyConfigModel):
    """已解析树构建设置；由管理器配置生成，保持与原内部派生设置相同的值。"""

    model_config = ConfigDict(frozen=False, validate_assignment=True)
    manager: TreeManagerConfig = Field(title="管理器配置", description="本次构建采用的底层树管理器配置。")
    llm_seed: int | None = Field(title="模型随机种子", description="调用方显式提供的模型随机种子，可为 None。")
    postprocess: TreePostprocessConfig = Field(title="已解析后处理", description="本次构建独立保存的后处理设置。")
    equivalence: TreeEquivalenceConfig = Field(title="已解析等价分组", description="本次构建独立保存的等价分组设置。")
    profiles: CapabilityProfilesConfig = Field(title="已解析对象画像", description="本次构建独立保存的对象画像设置。")
    prompt: TreePromptConfig = Field(title="已解析提示策略", description="本次构建独立保存的提示与缓存观测设置。")
    max_consecutive_failures: PositiveInt = Field(5, title="连续失败上限", description="允许的最大连续构建失败次数。")

    @classmethod
    def from_manager_config(cls, manager_config: TreeManagerConfig, *, llm_seed: int | None) -> Self:
        """仅组合已校验的管理器配置，不持有线程、模型客户端或统计状态。"""
        build = manager_config.build
        return cls(
            manager=manager_config,
            llm_seed=llm_seed,
            postprocess=build.postprocess.model_copy(deep=True),
            equivalence=build.equivalence.model_copy(deep=True),
            profiles=build.profiles.model_copy(deep=True),
            prompt=build.prompt.model_copy(deep=True),
        )


class IndexTaskConfig(SymphonyConfigModel):
    """toolkit 索引任务整体时限；不与单请求超时混用。"""

    total_timeout_seconds: Annotated[float, Field(ge=0)] = Field(
        0.0, title="构建总时限", description="整个索引任务的时限，单位秒；0 表示未配置总时限。",
    )


class IndexOutputConfig(SymphonyConfigModel):
    """toolkit 索引输出与失败处理策略。"""

    generate_tree_html: bool = Field(False, title="生成树预览", description="是否生成分类树 HTML 预览产物。")
    preserve_previous_index_on_failure: bool = Field(
        True, title="失败保留旧索引", description="构建失败时是否保留上一次成功发布的索引。",
    )
    strict_failure: bool = Field(False, title="严格失败模式", description="是否按现有 toolkit 的严格失败方式报告错误。")


class CapabilityIndexBuildConfig(SymphonyConfigModel):
    """agent toolkit 的索引构建配置，分组覆盖原 SkillIndexBuildConfig 的纯配置字段。"""

    structure: IndexTreeStructureConfig = Field(
        default_factory=IndexTreeStructureConfig, title="索引结构", description="分类树深度、分支和根分类输入。",
    )
    build: IndexTreeBuildConfig = Field(
        default_factory=IndexTreeBuildConfig, title="构建策略", description="模型请求、后处理、等价分组和对象画像。",
    )
    incremental: TreeIncrementalConfig = Field(
        default_factory=TreeIncrementalConfig, title="增量更新", description="增量构建的变化比例和分类置信度策略。",
    )
    task: IndexTaskConfig = Field(default_factory=IndexTaskConfig, title="任务时限", description="索引任务的整体时限。")
    output: IndexOutputConfig = Field(
        default_factory=IndexOutputConfig, title="输出策略", description="索引预览和失败处理设置。",
    )


class CapabilityIndexStorageConfig(SymphonyConfigModel):
    """原 SkillIndexRuntimeConfig 的纯存储设置；不推断或创建默认目录。"""

    index_root: ConfigPath = Field(title="索引根目录", description="显式指定索引根目录。", examples=["./index"])
    state_filename: str = Field("state.json", title="状态文件名", description="索引根目录内的状态文件名。", min_length=1)


class OneShotTreeBuildConfig(SymphonyConfigModel):
    """一次性分类树构建预算，保持 one_shot.py 中独立入口的默认值。"""

    max_depth: PositiveInt = Field(4, title="一次性树深度", description="一次性分类构建允许的最大层级。")
    max_output_tokens: PositiveInt = Field(32768, title="一次性输出上限", description="单次完整分类输出的 token 上限。")
    timeout_seconds: PositiveFloat = Field(420.0, title="一次性请求超时", description="单次分类请求超时，单位秒。")
    reasoning_effort: str | None = Field(None, title="推理强度", description="可选的模型推理强度，具体取值由模型适配器解释。")
    seed: int | None = Field(None, title="一次性构建种子", description="可选的模型随机种子。")


# 模型后端：只保留纯数据字段，不接收旧 client、llm_openai_client 或 prompt_cache 句柄。
class RetrievalModelConfig(SymphonyConfigModel):
    """现有 toolkit LLMConfig 和索引工作流模型参数的纯数据部分。"""

    model: str = Field("", title="模型名称", description="模型服务识别的模型名称；空值表示尚未绑定模型。")
    api_key: str = Field("", title="模型访问凭证", description="独立后端的访问凭证；文档只导出空默认值。", repr=False)
    base_url: str = Field("", title="模型服务地址", description="独立模型服务的基础 URL。")
    seed: int | None = Field(None, title="模型随机种子", description="可选的模型随机种子。")


class OpenAIClientConfig(RetrievalModelConfig):
    """OpenAI 兼容检索后端的纯配置；已创建的客户端由后续运行接口注入。"""

    backend: Literal["openai"] = Field("openai", title="后端类型", description="选择 OpenAI 兼容客户端后端。")
    extra_body: dict[str, JsonValue] = Field(
        default_factory=dict, title="额外请求参数", description="后端特定的 JSON 请求体参数，例如禁用 thinking。",
    )


class VLLMClientConfig(SymphonyConfigModel):
    """本地 vLLM 后端的纯配置；不加载模型或创建推理进程。"""

    backend: Literal["vllm"] = Field("vllm", title="后端类型", description="选择本地 vLLM 后端。")
    model_path: str = Field("", title="本地模型目录", description="模型文件目录；空值表示尚未配置。")
    tokenizer_path: str = Field("", title="分词器目录", description="分词器目录；空值由运行适配器沿用模型目录。")
    request_model: str = Field("", title="请求模型名称", description="请求和日志中使用的模型名称。")
    options: dict[str, JsonValue] = Field(
        default_factory=dict, title="后端启动选项", description="vLLM 启动的 JSON 参数，映射旧 vllm_kwargs。",
    )


class TransformersParallelismConfig(SymphonyConfigModel):
    """Transformers 后端的并行度和设备分配设置。"""

    tensor_parallel_size: PositiveInt = Field(1, title="张量并行度", description="单副本内的张量并行规模。")
    data_parallel_size: PositiveInt = Field(1, title="数据并行度", description="模型副本的数据并行规模。")
    device_ids: list[NonNegativeInt] = Field(
        default_factory=list, title="显式设备编号", description="设备编号列表；空列表表示交给运行适配器分配。",
    )
    tensor_parallel_plan: str = Field("", title="张量并行方案", description="可选的模型张量并行方案名称。")


class TransformersOptimizationConfig(SymphonyConfigModel):
    """Transformers 后端的注意力和编译设置。"""

    attn_implementation: str = Field("", title="注意力实现", description="可选的注意力实现名称；空值沿用后端默认。")
    torch_compile: bool = Field(False, title="启用编译", description="是否在运行适配器中启用 torch.compile。")


class TransformersClientConfig(SymphonyConfigModel):
    """本地 Transformers 系列后端的纯配置，默认 dtype 保持 bfloat16。"""

    backend: Literal["transformers", "transformers_prefix_cached", "transformers_prefix_cached_generation"] = Field(
        "transformers", title="后端类型", description="选择普通或前缀缓存 Transformers 后端。",
    )
    model_path: str = Field("", title="本地模型目录", description="模型文件目录；空值表示尚未配置。")
    tokenizer_path: str = Field("", title="分词器目录", description="分词器目录；空值由运行适配器沿用模型目录。")
    device: str = Field("auto", title="设备选择", description="设备选择字符串，例如 auto、cpu 或 cuda:0。")
    dtype: str = Field("bfloat16", title="推理精度", description="模型加载时采用的数据类型名称。")
    parallelism: TransformersParallelismConfig = Field(
        default_factory=TransformersParallelismConfig, title="并行配置", description="张量、数据并行与设备编号。",
    )
    optimization: TransformersOptimizationConfig = Field(
        default_factory=TransformersOptimizationConfig, title="推理优化", description="注意力实现和编译设置。",
    )


LLMClientConfig = Annotated[
    OpenAIClientConfig | VLLMClientConfig | TransformersClientConfig,
    Field(discriminator="backend"),
]


class DistributedGenerationConfig(SymphonyConfigModel):
    """前缀缓存生成后端的分布式拓扑，保留其独立配置和现有 world_size 规则。"""

    tp_size: PositiveInt = Field(1, title="张量并行规模", description="单模型副本的张量并行规模。")
    dp_size: PositiveInt = Field(1, title="数据并行规模", description="模型副本数量。")
    device_type: str = Field("auto", title="设备类型", description="分布式推理使用的设备类型。")
    device_ids: list[NonNegativeInt] = Field(
        default_factory=list, title="分布式设备编号", description="显式设备编号列表；空列表由后端分配。",
    )

    @property
    def world_size(self) -> int:
        """张量并行规模和数据并行规模的乘积。"""
        return self.tp_size * self.dp_size


class TrieConstraintConfig(SymphonyConfigModel):
    """生成时的字典树约束数据；不包含分词器或解码器实例。"""

    allowed_output_ids: list[str] = Field(title="允许输出标识", description="生成时允许选择的对象标识。")
    excluded_output_ids: list[str] = Field(
        default_factory=list, title="排除输出标识", description="生成时必须排除的对象标识。",
    )
    top_k: PositiveInt = Field(1, title="约束选择数量", description="本次约束最多允许选择的标识数。")
    version: str | None = Field(None, title="约束版本", description="可选的约束数据版本。")


class GenerationConstraintsConfig(SymphonyConfigModel):
    """模型生成约束集合；空约束表示不额外限制生成。"""

    trie: TrieConstraintConfig | None = Field(None, title="字典树约束", description="可选的对象标识字典树约束。")


class LLMGenerationConfig(SymphonyConfigModel):
    """底层模型生成参数，区别于检索分支选择配置；默认种子保持 1223。"""

    disable_thinking: bool = Field(True, title="禁用思考输出", description="是否向支持的模型请求关闭 thinking。")
    temperature: Annotated[float, Field(ge=0)] = Field(0.0, title="采样温度", description="模型生成采样温度。")
    top_p: Probability = Field(1.0, title="核采样概率", description="模型生成的 top-p 采样概率。")
    seed: int | None = Field(1223, title="生成随机种子", description="底层模型生成的可选随机种子。")
    constraints: GenerationConstraintsConfig = Field(
        default_factory=GenerationConstraintsConfig, title="生成约束", description="不含运行时缓存句柄的生成约束。",
    )


class IndexArtifactConfig(SymphonyConfigModel):
    """索引工作流的输出设置，区别于 toolkit 的任务失败策略。"""

    generate_tree_html: bool = Field(False, title="生成分类树预览", description="是否输出分类树 HTML 预览。")
    allow_fallback_tree: bool = Field(True, title="允许回退分类树", description="是否允许沿用现有工作流的回退树策略。")


class IndexBuildConfig(SymphonyConfigModel):
    """现有工作流 BuildConfig 的纯配置部分，不含 llm_openai_client 实例。"""

    method: Literal[4] = Field(4, title="索引构建方式", description="保留现有 BuildMethod.TREE 的编码 4。")
    model: RetrievalModelConfig = Field(
        default_factory=RetrievalModelConfig, title="工作流模型参数", description="模型名称、服务地址和可选种子等纯参数。",
    )
    structure: IndexTreeStructureConfig = Field(
        default_factory=IndexTreeStructureConfig, title="工作流树结构", description="树深度、分支和根分类输入。",
    )
    build: IndexTreeBuildConfig = Field(
        default_factory=IndexTreeBuildConfig, title="工作流构建策略", description="索引工作流的模型请求和分类构建策略。",
    )
    incremental: TreeIncrementalConfig = Field(
        default_factory=TreeIncrementalConfig, title="工作流增量策略", description="增量更新的变化比例和置信度阈值。",
    )
    artifacts: IndexArtifactConfig = Field(
        default_factory=IndexArtifactConfig, title="工作流产物策略", description="预览和回退树设置。",
    )


class ResolvedIndexTreeStructureConfig(SymphonyConfigModel):
    """工作流已解析的树结构；根分类必须已加载为数据，不再接受文件路径。"""

    branching_factor: PositiveInt = Field(title="已解析分支上限", description="归一化后的分类节点分支上限。")
    max_depth: PositiveInt = Field(title="已解析树深度", description="归一化后的分类树深度。")
    root_categories: list[str | dict[str, JsonValue]] | None = Field(
        title="已解析根分类", description="已加载的根分类列表，或表示自动发现的 None。",
    )


class ResolvedIndexBuildConfig(SymphonyConfigModel):
    """工作流归一化后的纯配置；各组必须由调用方提供，不执行配置文件加载。"""

    method: Literal[4] = Field(title="已解析构建方式", description="已经确认的树索引构建编码 4。")
    model: RetrievalModelConfig = Field(title="已解析模型参数", description="归一化后的模型纯数据参数。")
    structure: ResolvedIndexTreeStructureConfig = Field(title="已解析树结构", description="已经加载根分类的结构设置。")
    build: IndexTreeBuildConfig = Field(title="已解析构建策略", description="归一化后的请求和分类构建策略。")
    incremental: TreeIncrementalConfig = Field(title="已解析增量策略", description="归一化后的增量更新策略。")
    artifacts: IndexArtifactConfig = Field(title="已解析产物策略", description="归一化后的产物设置。")


# 原检索默认码表按既有顺序保存，避免配置模型反向导入检索实现模块。
# 来源：retrieval/search/runtime/codebooks.py；与旧码表的逐项一致性由配置测试保护。
_DEFAULT_COMPACT_CODES = tuple((
    "IU BK OE NW MU AO HB IW MA VS NI XS NE BH HP RP UC PB LI UK CF PW WO CO "
    "MI ON FS GM CH OA NV AD VC MR TB KA ZH ZR IM HA MO PH VV CN RA DV ST UY "
    "BA DN IA SZ EA LY AC JK WD MY BY XL FM LU WX NF BM MP SV MN SX BT ZX GU "
    "HO AB VM TA QC IK ME JU LL YA LB KN TV NQ EX FK DK PS TD RL GV FH EP NT "
    "BP ZE KC ZO TS NL ZI KD AS OX JP MH GG HC SM BV AL NN CI FN UB AA CL UM "
    "KI TO PY QB ZF ND QP NS KV LK UF IV GT CD SD HG RB CY GP DL WG EL VF WK "
    "WT HR JJ NR QH CC DY CG QU MF OF VL PE DF FE JS KL UG QG YL MJ DO ML FP "
    "JB UE FB AV MG KT CW UI AK OI CX EC SP FR FY RH IH IO WM QL US HF LF VR "
    "BL PU SC MT DU IF PF QQ AI RJ LO LC GA PL OP WF TX BW HX UD XB YG LT DW "
    "MZ SU HT PJ DA VA IQ JA PX KB GE EB AF LG GC XE OT TG OB PD IB JI BD XR "
    "GR HQ WB FC KO KH GO RV BF EU SE RO YZ GY OS TY ID EG UA OR BC SO PR HK "
    "XI RN MK ZY FG GX DX EM FA IS TI TU PI UP LP IR EI VB TH FU TC HU BU NU "
    "LR SS IJ LA PN TW BS KG CV SF ER MM AZ MV OD LE MS JE TK MQ CA EW VO XA "
    "CM TN UN RR UL RY YW FX OK GW HI NM EZ SI IZ SA DR QA JO HN QN HZ MX PK "
    "FI DC RD QS WL AJ VP UV BB AY YN NK UR OM DG XD NY XM DT KF JC KR TL RI "
    "YM EN OU RS DB LD NG AM JT NB BJ OL UT HD WA IE II KS YP VE BR RW AU SQ "
    "JV HW CS IL UU DJ SW AQ JM TE EO LS SB CU QR SH WN DH ZN LN AW SL DI KY "
    "EF ES KW XC OV CK DD IN WS KE PP RU UH EH RE MC ZA SN WW HY OW BE OG AH "
    "BN QE NP RC ZZ QM BX CT WR SG TR BI MB ED RX SJ NA OC DP NO JD FO FL HH "
    "FW TT QT NZ CR AG HL TF VN SK YO LV GI VI SR YT CB HE KP XT PG RG JR TP "
    "HM AR MW BG HS AP GF KM NJ RK YE NX AN XF AX LM PM GB DS IG UX GL GS PT "
    "VD FF OO GD XY IC UZ KU "
).split())


class RetrievalTraversalConfig(SymphonyConfigModel):
    """渐进检索的树分支探索策略，来自 search/service/models.py。"""

    max_branch_choices: PositiveInt = Field(6, title="单节点分支候选数", description="一次最多考虑的子分支数量。")
    max_parallel_branches: PositiveInt = Field(3, title="并行分支数量", description="同时搜索的最多分支数。")
    enable_parallel_branches: bool = Field(True, title="并行分支搜索", description="是否启用多个分支的并行搜索。")
    branch_choice_slack: NonNegativeInt = Field(2, title="分支选择余量", description="相对 top_k 多考虑的分支数量。")
    branch_candidate_slack: NonNegativeInt = Field(1, title="分支候选余量", description="每个分支额外保留的候选数量。")
    round_robin_branch_reduce: bool = Field(True, title="轮询合并分支", description="是否交替从各分支选取候选进行合并。")


class RetrievalRenderConfig(SymphonyConfigModel):
    """独立检索服务的提示渲染配置；保持完整树展开和默认紧凑码表。"""

    compact_codes_enabled: bool = Field(True, title="使用紧凑编码", description="是否以紧凑候选编码向模型展示对象。")
    compact_codebook: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_COMPACT_CODES), title="紧凑编码表",
        description="按既有检索实验顺序排列的候选编码；顺序与旧默认码表一致。",
    )
    flatten_tree: bool = Field(True, title="展开完整树", description="是否将当前候选树展平成提示内容。")
    max_exposure_depth: NonNegativeInt = Field(99, title="提示展示深度", description="一次模型请求最多展示的树层数。")
    exposure_threshold: NonNegativeInt = Field(
        1_000_000_000, title="提示展开阈值", description="控制节点展开的候选数量阈值，保留独立检索服务默认值。",
    )


class DisclosureConfig(RetrievalRenderConfig):
    """底层树片段展示配置，保留与独立检索服务不同的深度、阈值和空码表。"""

    compact_codes_enabled: bool = Field(False, title="片段紧凑编码", description="是否为片段候选使用紧凑编码。")
    compact_codebook: list[str] = Field(
        default_factory=list, title="片段编码表", description="片段展示的自定义码表；默认由运行过程决定编码。",
    )
    flatten_tree: bool = Field(False, title="片段展平", description="是否将展示片段展开为平面内容。")
    max_exposure_depth: NonNegativeInt = Field(2, title="片段展示深度", description="一次最多展示的片段树层数。")
    exposure_threshold: NonNegativeInt = Field(12, title="片段展开阈值", description="片段节点展开的候选数量阈值。")


class AgenticRenderConfig(DisclosureConfig):
    """agent toolkit 的树浏览提示设置，默认只展示一层。"""

    max_exposure_depth: NonNegativeInt = Field(1, title="工具浏览深度", description="一次工具浏览最多展示的树层数。")


class LogitSelectionConfig(SymphonyConfigModel):
    """候选 logit 打分策略，只有选择模式为 logit_selection 时生效。"""

    require_single_token_codes: bool = Field(True, title="要求单 token 编码", description="候选编码是否必须对应单个 token。")
    return_probabilities: bool = Field(True, title="返回候选概率", description="是否在打分结果中返回候选概率。")
    fallback_mode: Literal["error", "generate"] = Field(
        "error", title="打分回退方式", description="不支持候选打分时直接报错，或回退为生成式选择。",
    )
    max_candidates: PositiveInt = Field(512, title="最大打分候选数", description="一次候选打分考虑的最多编码数量。")
    min_probability: Probability | None = Field(
        None, title="最低候选概率", description="可选的候选概率下限；None 表示不按最低概率截断。",
    )
    trace_top_n: NonNegativeInt = Field(10, title="打分追踪数量", description="追踪信息最多记录的高分候选数量。")


class RetrievalGenerationConfig(SymphonyConfigModel):
    """检索分支选择配置，区别于底层模型采样参数 LLMGenerationConfig。"""

    mode: Literal["generate", "logit_selection"] = Field(
        "generate", title="候选选择方式", description="生成候选编码，或使用模型支持的候选 logit 打分。",
    )
    max_tokens: PositiveInt = Field(96, title="选择输出上限", description="分支选择输出的 token 上限。")
    request_timeout_seconds: PositiveFloat | None = Field(
        120.0, title="选择请求超时", description="单次候选选择请求超时，单位秒；None 沿用后端策略。",
    )
    trie_constrained_decoding_enabled: bool = Field(
        False, title="字典树约束解码", description="是否在支持的模型后端启用字典树约束解码。",
    )
    logit: LogitSelectionConfig = Field(
        default_factory=LogitSelectionConfig, title="候选打分", description="logit_selection 模式的候选打分设置。",
    )


class ProgressiveRetrieverConfig(SymphonyConfigModel):
    """底层渐进检索配置，候选上限保留为 5，不与公共检索服务的 10 合并。"""

    top_k: PositiveInt = Field(5, title="底层候选上限", description="底层渐进检索最多返回的候选数量。")
    traversal: RetrievalTraversalConfig = Field(
        default_factory=RetrievalTraversalConfig, title="分支遍历", description="渐进检索的分支探索策略。",
    )
    render: RetrievalRenderConfig = Field(
        default_factory=RetrievalRenderConfig, title="提示渲染", description="渐进检索的树展示和编码策略。",
    )
    generation: RetrievalGenerationConfig = Field(
        default_factory=RetrievalGenerationConfig, title="候选选择", description="渐进检索的生成或打分策略。",
    )
    llm: LLMClientConfig = Field(
        default_factory=OpenAIClientConfig, title="检索模型后端", description="独立检索器的模型后端纯配置。",
    )


class RetrieverConfig(SymphonyConfigModel):
    """独立检索服务的初始化配置，保留服务入口候选上限为 10。"""

    top_k: PositiveInt = Field(10, title="服务候选上限", description="独立检索服务最多返回的候选数量。")
    traversal: RetrievalTraversalConfig = Field(
        default_factory=RetrievalTraversalConfig, title="服务遍历策略", description="服务内部的分支探索策略。",
    )
    render: RetrievalRenderConfig = Field(
        default_factory=RetrievalRenderConfig, title="服务提示渲染", description="服务内部的树展示和编码设置。",
    )
    generation: RetrievalGenerationConfig = Field(
        default_factory=RetrievalGenerationConfig, title="服务候选选择", description="服务内部的生成或打分设置。",
    )
    llm: LLMClientConfig = Field(
        default_factory=OpenAIClientConfig, title="服务模型后端", description="独立检索服务的模型后端纯配置。",
    )


class RetrievalRequestConfig(SymphonyConfigModel):
    """单次检索请求的纯覆盖参数；查询文本和可见范围属于调用输入。"""

    top_k: PositiveInt | None = Field(
        None, title="本次候选上限", description="覆盖本次查询返回数量；None 表示沿用检索器配置。",
    )


class RuntimeRetrieverConfig(SymphonyConfigModel):
    """检索服务内部已解析配置族；本文件不执行服务到运行配置的转换。"""

    top_k: PositiveInt = Field(10, title="内部结果数量", description="独立服务当前使用的结果数量上限。")
    progressive: ProgressiveRetrieverConfig = Field(
        default_factory=ProgressiveRetrieverConfig, title="渐进检索设置",
        description="保留原内部默认对象；服务接入阶段再显式同步本次 top_k。",
    )


class AgenticRequestConfig(SymphonyConfigModel):
    """agent toolkit 的检索请求预算，不添加独立检索器的打分策略。"""

    max_tokens: PositiveInt = Field(96, title="工具检索输出上限", description="检索选择请求的输出 token 上限。")
    request_timeout_seconds: PositiveFloat = Field(120.0, title="工具检索超时", description="单次模型请求超时，单位秒。")


class RetrievalToolNamesConfig(SymphonyConfigModel):
    """已有检索工具的标识；字符串保持原值，配置字段统一使用通用命名。"""

    index_build: str = Field(
        "skill_index_build", title="索引构建工具", description="现有索引构建工具注册名称。", min_length=1,
    )
    branch_explore: str = Field(
        "skill_branch_explore", title="分支探索工具", description="现有分类树分支探索工具注册名称。", min_length=1,
    )
    branch_peek: str = Field(
        "skill_branch_peek", title="分支预览工具", description="现有分类树分支预览工具注册名称。", min_length=1,
    )


class AgenticRetrievalConfig(SymphonyConfigModel):
    """agent toolkit 的检索配置，保持工具浏览与自然语言独立检索的默认差异。"""

    top_k: PositiveInt = Field(10, title="工具候选上限", description="工具检索最多返回的对象数量。")
    render: AgenticRenderConfig = Field(
        default_factory=AgenticRenderConfig, title="工具浏览展示", description="工具浏览的层级和候选展示策略。",
    )
    request: AgenticRequestConfig = Field(
        default_factory=AgenticRequestConfig, title="工具请求预算", description="工具检索的输出和超时预算。",
    )
    tools: RetrievalToolNamesConfig = Field(
        default_factory=RetrievalToolNamesConfig, title="工具注册名称", description="保持现有协议的检索工具标识。",
    )


class ScoreTruncationConfig(SymphonyConfigModel):
    """候选得分截断设置；绝对分数可为负值，以兼容不同评分尺度。"""

    min_score: float | None = Field(None, title="最低绝对分数", description="可选的绝对得分下限；None 表示不按此截断。")
    relative_min_score: Annotated[float, Field(ge=0)] | None = Field(
        None, title="相对分数下限", description="相对最佳候选的分数比例阈值；None 表示不启用。",
    )
    autocut_jumps: NonNegativeInt = Field(0, title="自动截断跳变数", description="允许的得分跳变数；0 表示不启用自动截断。")
    autocut_min_relative_drop: Annotated[float, Field(ge=0)] = Field(
        0.0, title="自动截断相对落差", description="识别得分跳变所需的最小相对落差。",
    )


class TreeBuildOptions(SymphonyConfigModel):
    """原 build_tree 关键字选项中的纯配置；对象列表、客户端与进度数据仍属于调用参数。"""

    dynamic: DynamicTreeConfig | None = Field(None, title="动态树设置", description="可选的动态树配置。")
    manager: TreeManagerConfig | None = Field(None, title="树管理器设置", description="可选的底层树管理器配置。")
    model: RetrievalModelConfig | None = Field(
        None, title="树模型参数", description="可选的模型纯数据覆盖；None 表示不提供本次模型参数覆盖。",
    )
    max_concurrency: PositiveInt | None = Field(None, title="本次构建并发", description="可选的单次构建并发覆盖。")
    verbose: bool = Field(False, title="详细构建输出", description="是否显示现有构建过程的详细信息。")
    show_tree: bool = Field(True, title="显示分类树", description="是否显示构建后的分类树。")
    display_capability_root: ConfigPath | None = Field(
        None, title="展示对象目录", description="输出展示使用的对象目录文本；不影响扫描根目录。",
    )
    capability_type: str = Field("skill", title="构建对象标签", description="底层树构建的对象类型标签。", min_length=1)


# 显式登记模型，后续文档工具可分别导出公共配置树和独立内部配置。
# 类型定义是唯一字段说明来源；注册表只维护覆盖范围，不重复维护字段或默认值。
CONFIG_MODELS: tuple[type[SymphonyConfigModel], ...] = (
    CapabilityConfig, StorageConfig, RetrievalBuildConfig, RetrievalSearchConfig, RetrievalConfig,
    OrchestrationBuildConfig, OrchestrationPlanConfig, OrchestrationConfig, SymphonyConfig,
    ScanLimitsConfig, ManifestParseConfig, DirectoryScanConfig,
    FingerprintExtractionConfig, FingerprintEvaluationConfig, FingerprintCacheConfig,
    FingerprintProtocolConfig, FingerprintConfig,
    GraphPlanningConfig, GraphMatchingConfig, FlowAcceptanceConfig, FlowNarrativeConfig, FlowConfig,
    DiscoveryBudgetConfig, DiscoveryOutputConfig, DiscoveryPromptConfig, DiscoveryConfig,
    ExperienceClusteringConfig, ExperienceExecutionConfig, ExperiencePatternConfig, ExperienceBankBuildConfig,
    TreePostprocessConfig, TreeEquivalenceConfig, CapabilityProfilesConfig, TreePromptConfig,
    TreeIncrementalConfig, TreeRequestConfig, IndexRequestConfig, TreeBuildConfig, IndexTreeBuildConfig,
    TreeStructureConfig, IndexTreeStructureConfig, TreeManagerConfig, TreeExpansionConfig,
    DynamicTreeConfig, TreeBuildSettings, IndexTaskConfig, IndexOutputConfig,
    CapabilityIndexBuildConfig, CapabilityIndexStorageConfig, OneShotTreeBuildConfig,
    RetrievalModelConfig, OpenAIClientConfig, VLLMClientConfig, TransformersParallelismConfig,
    TransformersOptimizationConfig, TransformersClientConfig, DistributedGenerationConfig,
    TrieConstraintConfig, GenerationConstraintsConfig, LLMGenerationConfig,
    IndexArtifactConfig, IndexBuildConfig, ResolvedIndexTreeStructureConfig, ResolvedIndexBuildConfig,
    RetrievalTraversalConfig, RetrievalRenderConfig, DisclosureConfig, AgenticRenderConfig,
    LogitSelectionConfig, RetrievalGenerationConfig, ProgressiveRetrieverConfig, RetrieverConfig,
    RetrievalRequestConfig, RuntimeRetrieverConfig, AgenticRequestConfig, RetrievalToolNamesConfig,
    AgenticRetrievalConfig, ScoreTruncationConfig, TreeBuildOptions,
)

__all__ = ["CONFIG_MODELS", "SymphonyConfigModel", "CapabilityType", "LLMClientConfig"] + [
    model.__name__ for model in CONFIG_MODELS
]

