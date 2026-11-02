# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Symphony 目标配置，尚未接入运行链路。

公共入口为 SymphonyConfig，按 capability、storage、retrieval、orchestration、
flow、experience 组合可调策略；子配置均有默认值，仅对象目录、类型和产物目录必填。
协议身份、模型后端及具有独立用途的算法和调用参数仍单独定义；解析设置仅由原运行模块维护。
公共树模型与内部模型分别登记；便利导出只保留核心配置类，供验证和文档读取完整登记。
现有模块配置仍是运行行为的依据；本文件不读取环境、扫描目录或创建模型客户端。

字段的中文 title、description 和约束用于后续生成配置文档。同义策略只在公共树定义一次；
旧入口的不同默认值由原业务模块保留，配置中的 capability 命名不修改现有产物字段或工具标识。
客户端实例、线程池、取消/进度回调、扫描结果和前缀缓存句柄属于运行参数，
不进入这些可序列化模型。文件中的路径只保存文本，不检查存在性或解析绝对路径。
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Annotated, Literal

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


# 公共规划策略；关系阈值统一由所属 orchestration 配置提供。
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


class FingerprintPolicyConfig(SymphonyConfigModel):
    """用户可调指纹策略；协议身份由内部服务维护。"""

    extraction: FingerprintExtractionConfig = Field(
        default_factory=FingerprintExtractionConfig, title="语义提取", description="对象元数据与模型提取策略。",
    )
    evaluation: FingerprintEvaluationConfig = Field(
        default_factory=FingerprintEvaluationConfig, title="指纹评估", description="评估方式及证据文本上限。",
    )
    cache: FingerprintCacheConfig = Field(
        default_factory=FingerprintCacheConfig, title="缓存策略", description="独立指纹缓存的开关。",
    )


# 独立模块：保留 orchestration/config.py、discovery/config.py、experience/models.py 的默认值。
class GraphMatchingPolicyConfig(SymphonyConfigModel):
    """关系匹配策略；公共入口统一使用所属构图任务的并发上限。"""

    batch_size: PositiveInt = Field(12, title="关系匹配批量", description="单批关系匹配的处理数量。")
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
    """组合能力包沉淀策略，默认值对应现有 SymphonyFlowConfig；配置构造不启动服务。"""

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


class TreePromptPolicyConfig(SymphonyConfigModel):
    """用户可调的提示确定性、结构发现种子和缓存观测策略。"""

    deterministic: bool = Field(True, title="确定性提示", description="是否使用稳定排序和确定性提示构造。")
    discovery_seed: int = Field(42, title="结构发现种子", description="分类结构发现过程的随机种子。")
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


class IndexRequestConfig(SymphonyConfigModel):
    """统一索引构建请求策略，默认并发 2、超时 420 秒、分类批量 32。"""

    max_concurrency: PositiveInt = Field(2, title="索引构建并发", description="索引构建模型请求的并发上限。")
    caching: bool = Field(False, title="树构建缓存", description="是否启用底层树构建缓存。")
    max_retries: NonNegativeInt = Field(2, title="最大重试次数", description="模型请求失败后的最多重试次数。")
    timeout_seconds: PositiveFloat = Field(420.0, title="索引请求超时", description="单次索引构建模型请求超时，单位秒。")
    context_window: NonNegativeInt = Field(0, title="构建上下文窗口", description="上下文 token 预算；0 表示未显式指定。")
    max_output_tokens: NonNegativeInt = Field(0, title="构建输出预算", description="模型输出 token 上限；0 表示未显式指定。")
    classification_batch_limit: PositiveInt = Field(32, title="索引分类批量", description="索引分类请求处理的最多对象数。")


class TreeExpansionConfig(SymphonyConfigModel):
    """动态树派生阈值的倍率，沿用 schema.py 中的现有常量。"""

    max_capabilities_per_node: PositiveFloat = Field(1.5, title="节点容量倍率", description="节点容量相对分支数的倍率。")
    expand_threshold: PositiveFloat = Field(0.7, title="扩展阈值倍率", description="节点扩展阈值相对分支数的倍率。")
    early_stop: PositiveFloat = Field(1.7, title="提前停止倍率", description="提前停止对象数相对分支数的倍率。")
    lazy_split: PositiveFloat = Field(1.3, title="延迟拆分倍率", description="延迟拆分阈值相对节点容量的倍率。")
    classification_batch: PositiveFloat = Field(6.0, title="分类批量倍率", description="分类批量相对分支数的倍率。")
    structure_sample: PositiveFloat = Field(12.0, title="结构采样倍率", description="结构采样数量相对分支数的倍率。")


class DynamicTreeConfig(SymphonyConfigModel):
    """动态树调整策略；结构统一由 retrieval.build.structure 提供，派生阈值留给运行层计算。"""

    model_config = ConfigDict(frozen=False, validate_assignment=True)
    rebalance_interval: PositiveInt = Field(50, title="重平衡间隔", description="触发结构重平衡的操作计数间隔。")
    expansion: TreeExpansionConfig = Field(
        default_factory=TreeExpansionConfig, title="派生阈值", description="容量、扩展和采样阈值的倍率。",
    )


class IndexTaskConfig(SymphonyConfigModel):
    """索引任务的整体时限、回退与失败处理；不与单请求超时混用。"""

    total_timeout_seconds: Annotated[float, Field(ge=0)] = Field(
        0.0, title="构建总时限", description="整个索引任务的时限，单位秒；0 表示未配置总时限。",
    )
    preserve_previous_index_on_failure: bool = Field(
        True, title="失败保留旧索引", description="构建失败时是否保留上一次成功发布的索引。",
    )
    strict_failure: bool = Field(False, title="严格失败模式", description="是否按现有 toolkit 的严格失败方式报告错误。")
    allow_fallback_tree: bool = Field(True, title="允许回退分类树", description="是否允许沿用现有工作流的回退树策略。")


class IndexOutputConfig(SymphonyConfigModel):
    """索引构建过程与产物的展示设置，分别控制详细输出、分类树展示和 HTML 预览。"""

    generate_tree_html: bool = Field(False, title="生成树预览", description="是否生成分类树 HTML 预览产物。")
    verbose: bool = Field(False, title="详细构建输出", description="是否显示现有构建过程的详细信息。")
    show_tree: bool = Field(True, title="显示分类树", description="是否显示构建后的分类树；区别于生成 HTML 预览文件。")
    display_capability_root: ConfigPath | None = Field(
        None, title="展示对象目录", description="输出展示使用的对象目录文本；不影响扫描根目录。",
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


class AgenticRenderConfig(RetrievalRenderConfig):
    """工具浏览复用检索渲染字段，保留一层展示、空码表和片段展开阈值。"""

    compact_codes_enabled: bool = Field(False, title="片段紧凑编码", description="是否为片段候选使用紧凑编码。")
    compact_codebook: list[str] = Field(
        default_factory=list, title="片段编码表", description="片段展示的自定义码表；默认由运行过程决定编码。",
    )
    flatten_tree: bool = Field(False, title="片段展平", description="是否将展示片段展开为平面内容。")
    max_exposure_depth: NonNegativeInt = Field(1, title="工具浏览深度", description="一次工具浏览最多展示的树层数。")
    exposure_threshold: NonNegativeInt = Field(12, title="片段展开阈值", description="片段节点展开的候选数量阈值。")


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


class RetrievalSearchConfig(SymphonyConfigModel):
    """公共自然语言检索策略；模型实例由 Runtime 注入，配置只控制检索行为。"""

    top_k: PositiveInt = Field(
        10, title="候选数量上限", description="每次检索最多返回的对象数；可在调用时覆盖，不影响已有索引。",
        examples=[5, 10],
    )
    traversal: RetrievalTraversalConfig = Field(
        default_factory=RetrievalTraversalConfig, title="分支遍历", description="渐进检索的分支探索策略。",
    )
    render: RetrievalRenderConfig = Field(
        default_factory=RetrievalRenderConfig, title="提示渲染", description="渐进检索的树展示和编码策略。",
    )
    generation: RetrievalGenerationConfig = Field(
        default_factory=RetrievalGenerationConfig, title="候选选择", description="渐进检索的生成或打分策略。",
    )


class RetrievalRequestConfig(SymphonyConfigModel):
    """单次检索请求的纯覆盖参数；查询文本和可见范围属于调用输入。"""

    top_k: PositiveInt | None = Field(
        None, title="本次候选上限", description="覆盖本次查询返回数量；None 表示沿用检索器配置。",
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


# 公共组合在子模型定义之后声明，默认工厂不需要运行模块或前向引用修复。
class CapabilityConfig(SymphonyConfigModel):
    """一个 Runtime 所管理的对象定义集合及扫描、指纹策略；不同类型使用独立集合。"""

    root: ConfigPath = Field(
        title="对象根目录", description="静态对象定义所在目录；相对路径的解析由后续 Runtime 接入负责。",
        examples=["./capabilities"],
    )
    type: CapabilityType = Field(
        title="对象类型", description="对象定义格式；枚举值表达目标支持范围，不表示对应读取器已经实现。",
        examples=["skill", "expert"],
    )
    scan: DirectoryScanConfig = Field(
        default_factory=DirectoryScanConfig, title="目录扫描", description="目录深度、资源预算和入口文档解析限制。",
    )
    fingerprint: FingerprintPolicyConfig = Field(
        default_factory=FingerprintPolicyConfig, title="指纹策略", description="对象指纹的提取、评估和缓存策略。",
    )


class StorageConfig(SymphonyConfigModel):
    """Runtime 产物存储位置；路径检查和目录创建由后续运行层负责。"""

    artifact_root: ConfigPath = Field(
        title="产物根目录", description="保存 inventory、缓存、tree 和 graph 的根目录；模型校验不会创建目录。",
        examples=["./.symphony"],
    )


class RetrievalTreeStructureConfig(SymphonyConfigModel):
    """公共分类树结构；沿用索引入口默认值，并保持分支数至少为 2 的约束。"""

    branching_factor: Annotated[int, Field(ge=2)] = Field(
        128, title="分类分支上限", description="每个分类节点的最大分支数；公共入口至少允许两个分支。",
    )
    max_depth: PositiveInt = Field(6, title="索引树深度", description="索引分类树的最大深度。")
    root_categories: RootCategoryInput = Field(
        None, title="根分类输入", description="根分类列表或配置文件路径文本；模型本身不读取路径。",
    )


class RetrievalBuildConfig(SymphonyConfigModel):
    """公共分类树构建策略；直接组合各子策略，默认值沿用索引 toolkit 入口。"""

    structure: RetrievalTreeStructureConfig = Field(
        default_factory=RetrievalTreeStructureConfig, title="树结构", description="分类树深度、分支和根分类输入。",
    )
    dynamic: DynamicTreeConfig = Field(
        default_factory=DynamicTreeConfig, title="动态树调整",
        description="重平衡间隔和扩展倍率；共用 structure 的树结构，不执行派生阈值计算。",
    )
    request: IndexRequestConfig = Field(
        default_factory=IndexRequestConfig, title="构建请求", description="模型请求的并发、超时、重试和分类批量。",
    )
    postprocess: TreePostprocessConfig = Field(
        default_factory=TreePostprocessConfig, title="树后处理", description="分类完成后的后处理策略。",
    )
    equivalence: TreeEquivalenceConfig = Field(
        default_factory=TreeEquivalenceConfig, title="等价分组", description="父分类内的等价对象分组策略。",
    )
    profiles: CapabilityProfilesConfig = Field(
        default_factory=CapabilityProfilesConfig, title="对象画像", description="对象画像和选择规则提取设置。",
    )
    prompt: TreePromptPolicyConfig = Field(
        default_factory=TreePromptPolicyConfig, title="构建提示", description="提示确定性、结构发现种子和缓存观测。",
    )
    incremental: TreeIncrementalConfig = Field(
        default_factory=TreeIncrementalConfig, title="增量更新", description="变化比例、分类置信度和结构不均衡策略。",
    )
    task: IndexTaskConfig = Field(
        default_factory=IndexTaskConfig, title="构建任务", description="整体时限、回退和失败处理；区别于单次请求超时。",
    )
    output: IndexOutputConfig = Field(
        default_factory=IndexOutputConfig, title="产物展示", description="构建输出、分类树展示和预览产物设置。",
    )


class RetrievalConfig(SymphonyConfigModel):
    """公共检索配置；构建、查询、发现和工具交互分别配置，启用不会自动构建索引。"""

    enabled: bool = Field(True, title="启用检索", description="允许使用检索入口；独立于编排开关。")
    build: RetrievalBuildConfig = Field(
        default_factory=RetrievalBuildConfig, title="索引构建", description="显式构建分类树时使用的策略。",
    )
    search: RetrievalSearchConfig = Field(
        default_factory=RetrievalSearchConfig, title="候选检索", description="查询已有分类树时使用的默认策略。",
    )
    discovery: DiscoveryConfig = Field(
        default_factory=DiscoveryConfig, title="对象发现", description="对象发现的候选、输出和提示预算。",
    )
    toolkit: AgenticRetrievalConfig = Field(
        default_factory=AgenticRetrievalConfig, title="工具交互", description="工具浏览、请求预算和工具标识；区别于自然语言检索。",
    )


class OrchestrationBuildConfig(SymphonyConfigModel):
    """公共关系图构建策略；提取方式由 capability.fingerprint 配置，匹配共识只有一个配置位置。"""

    max_concurrency: PositiveInt = Field(
        1, title="构图并发上限",
        description="目标单次图构建各阶段共用的模型请求上限；指纹阶段取此值与提取并发上限的较小值。",
    )
    matching: GraphMatchingPolicyConfig = Field(
        default_factory=GraphMatchingPolicyConfig, title="关系匹配", description="关系批量、共识要求和候选数量上限。",
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


class ExperienceConfig(SymphonyConfigModel):
    """经验库策略；仅声明构建参数，不自动评估轨迹、创建经验库或触发构建。"""

    build: ExperienceBankBuildConfig = Field(
        default_factory=ExperienceBankBuildConfig, title="经验构建", description="经验记录的聚类、构建执行和模式提炼策略。",
    )


class SymphonyConfig(SymphonyConfigModel):
    """Symphony 目标公共配置；尚未被现有 SymphonyRuntime 接收。"""

    capability: CapabilityConfig = Field(title="对象集合", description="显式声明对象目录、类型及预处理策略。")
    storage: StorageConfig = Field(title="产物存储", description="显式声明本集合的产物根目录。")
    retrieval: RetrievalConfig = Field(
        default_factory=RetrievalConfig, title="检索能力", description="分类树构建、候选检索、对象发现和工具交互的默认配置。",
    )
    orchestration: OrchestrationConfig = Field(
        default_factory=OrchestrationConfig, title="编排能力", description="关系图构建和计划生成的默认配置。",
    )
    flow: FlowConfig = Field(
        default_factory=FlowConfig, title="组合能力沉淀", description="能力包接纳与叙述策略；配置存在或启用不表示服务已启动。",
    )
    experience: ExperienceConfig = Field(
        default_factory=ExperienceConfig, title="经验库", description="经验库构建的默认配置；不隐式读取或写入经验。",
    )


# 登记范围用于 Schema 覆盖和文档；只有根模型可达的配置属于公共树。
PUBLIC_CONFIG_MODELS: tuple[type[SymphonyConfigModel], ...] = (
    SymphonyConfig, CapabilityConfig, DirectoryScanConfig, ScanLimitsConfig, ManifestParseConfig,
    FingerprintPolicyConfig, FingerprintExtractionConfig, FingerprintEvaluationConfig, FingerprintCacheConfig,
    StorageConfig, RetrievalConfig, RetrievalBuildConfig, RetrievalTreeStructureConfig, IndexRequestConfig,
    DynamicTreeConfig, TreeExpansionConfig,
    TreePostprocessConfig, TreeEquivalenceConfig, CapabilityProfilesConfig, TreePromptPolicyConfig,
    TreeIncrementalConfig, IndexTaskConfig, IndexOutputConfig,
    RetrievalSearchConfig, RetrievalTraversalConfig, RetrievalRenderConfig, RetrievalGenerationConfig, LogitSelectionConfig,
    DiscoveryConfig, DiscoveryBudgetConfig, DiscoveryOutputConfig, DiscoveryPromptConfig,
    AgenticRetrievalConfig, AgenticRenderConfig, AgenticRequestConfig, RetrievalToolNamesConfig,
    OrchestrationConfig, OrchestrationBuildConfig, GraphMatchingPolicyConfig, OrchestrationPlanConfig,
    FlowConfig, FlowAcceptanceConfig, FlowNarrativeConfig,
    ExperienceConfig, ExperienceBankBuildConfig, ExperienceClusteringConfig, ExperienceExecutionConfig, ExperiencePatternConfig,
)

INTERNAL_CONFIG_MODELS: tuple[type[SymphonyConfigModel], ...] = (
    FingerprintProtocolConfig,
    CapabilityIndexStorageConfig, OneShotTreeBuildConfig,
    RetrievalModelConfig, OpenAIClientConfig, VLLMClientConfig, TransformersParallelismConfig,
    TransformersOptimizationConfig, TransformersClientConfig, DistributedGenerationConfig,
    TrieConstraintConfig, GenerationConstraintsConfig, LLMGenerationConfig,
    RetrievalRequestConfig, ScoreTruncationConfig,
)

CONFIG_MODELS: tuple[type[SymphonyConfigModel], ...] = PUBLIC_CONFIG_MODELS + INTERNAL_CONFIG_MODELS

# 保留核心配置类的便利导出；子模型可通过嵌套字典配置，内部工具显式读取登记表。
__all__ = [
    "CapabilityType", "CapabilityConfig", "StorageConfig", "RetrievalBuildConfig", "RetrievalSearchConfig",
    "RetrievalConfig", "OrchestrationBuildConfig", "OrchestrationPlanConfig", "OrchestrationConfig",
    "FlowConfig", "ExperienceConfig", "SymphonyConfig",
]
