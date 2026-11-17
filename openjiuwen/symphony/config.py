# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Symphony 运行配置，供 SymphonyRuntime(model, config) 及内部消费者统一使用。

公共入口为 SymphonyConfig，按 capability、storage、retrieval、orchestration、
flow 组合可调策略；能力来源列表和产物目录必填，其余策略由默认值补全。
指纹协议和一次性构树内部预算单独定义；解析后的运行快照不另设策略默认值。
公共树模型与内部模型分别登记；便利导出只保留核心配置类，供验证和文档读取完整登记。
本文件不读取环境、扫描目录或创建模型客户端；目录检查和服务组装由 Runtime 负责。

字段的中文 title、description 和约束用于后续生成配置文档。同义策略只在公共树定义一次；
宿主产品默认值通过本配置显式覆盖；capability 命名不修改现有对象文件格式或工具标识。
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
    """各能力来源共用的目录扫描配置。"""

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
    max_body_chars: PositiveInt = Field(16_000, title="正文字符上限", description="送入语义提取的对象正文字符数上限。")
    max_output_tokens: PositiveInt | None = Field(
        2_048, title="提取输出上限", description="提取模型单次输出的 token 数上限；None 表示不传此参数，沿用注入模型的配置。",
    )
    timeout_seconds: PositiveFloat | None = Field(
        None, title="提取请求超时", description="单次提取请求超时，单位秒；None 表示沿用模型适配器策略。",
    )


class FingerprintEvaluationConfig(SymphonyConfigModel):
    """指纹评估参数；静态评估不依赖模型评估开关。"""

    llm_enabled: bool = Field(False, title="模型评估", description="是否追加模型辅助评估。")
    max_evidence_chars: PositiveInt = Field(512, title="证据文本长度", description="单条评估证据文本的字符数上限。")


class FingerprintCacheConfig(SymphonyConfigModel):
    """指纹缓存策略；真实缓存读写仍由现有服务负责。"""

    enabled: bool = Field(True, title="启用指纹缓存", description="是否允许复用符合身份与内容校验的指纹缓存。")


class FingerprintProtocolConfig(SymphonyConfigModel):
    """指纹版本与签名输入；仅记录现有默认值，不在此生成运行缓存签名。"""

    schema_version: str = Field("1.0", title="指纹结构版本", description="现有指纹产物的结构版本。", min_length=1)
    extraction_version: str = Field(
        "symphony-capability-extraction-v3", title="提取协议版本", description="现有语义提取协议标识。", min_length=1,
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


# 独立模块：保留 orchestration/config.py、discovery/config.py 的默认值。
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

    min_successes: PositiveInt = Field(3, title="最少成功次数", description="接纳能力包所需的成功记录数。")
    min_pack_success_rate: Probability = Field(0.8, title="最低成功率", description="能力包整体成功率下限。")


class FlowNarrativeConfig(SymphonyConfigModel):
    """Flow 经验叙述的示例预算。"""

    max_examples: PositiveInt = Field(5, title="叙述示例上限", description="每个能力包叙述保留的最多示例数。")


class OrchestrationExperienceConfig(SymphonyConfigModel):
    """执行观测与组合能力包的经验沉淀策略。"""

    enabled: bool = Field(False, title="启用经验沉淀", description="编排开启时接收执行观测并沉淀组合能力包。")
    acceptance: FlowAcceptanceConfig = Field(
        default_factory=FlowAcceptanceConfig, title="接纳条件", description="能力包的成功次数和成功率要求。",
    )
    narrative: FlowNarrativeConfig = Field(
        default_factory=FlowNarrativeConfig, title="叙述预算", description="经验叙述示例的数量限制。",
    )


class DiscoveryOutputConfig(SymphonyConfigModel):
    """Discovery 返回内容和列表分页的预算。"""

    max_chars: PositiveInt = Field(12_000, title="输出字符上限", description="一次发现结果的最多输出字符数。")
    max_results: PositiveInt = Field(10, title="结果数量上限", description="一次发现请求返回的最多结果数。")
    max_list_entries: PositiveInt = Field(40, title="列表条目上限", description="一次列表展示的最多条目数。")
    incremental_notice_max_chars: PositiveInt = Field(
        4_000, title="增量提示字符上限", description="增量变化提示的最多字符数。",
    )


class DiscoveryPromptConfig(SymphonyConfigModel):
    """Discovery 候选提示的容量与优先对象；不推断模型上下文，现有对象标识保持原值。"""

    candidate_budget_ratio: Annotated[float, Field(gt=0, le=1)] = Field(
        0.01, title="候选预算比例", description="候选提示可占用的上下文比例，范围大于 0 且不超过 1。",
    )
    context_window_tokens: PositiveInt = Field(
        200_000, title="上下文窗口", description="用于计算候选预算的上下文容量，单位 token。",
    )
    preferred_capability_ids: list[str] = Field(
        default_factory=lambda: ["skill-creator", "swarmskill-creator", "symphony-assistant"],
        title="优先对象标识", description="提示优先展示的对象标识列表，顺序有意义；不表示已授权执行。",
    )

    @property
    def candidate_budget_tokens(self) -> int:
        """按现有规则计算候选 token 预算，最少为 1；只读派生值不进入配置输入或序列化。"""
        return max(1, int(self.context_window_tokens * self.candidate_budget_ratio))


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
    max_description_chars: Annotated[int, Field(ge=40)] = Field(
        140, title="画像描述长度", description="对象画像描述的字符数上限。",
    )
    max_rule_chars: Annotated[int, Field(ge=40)] = Field(120, title="选择规则长度", description="单条画像选择规则的字符数上限。")


class TreePromptPolicyConfig(SymphonyConfigModel):
    """用户可调的提示确定性和结构发现种子。"""

    deterministic: bool = Field(True, title="确定性提示", description="是否使用稳定排序和确定性提示构造。")
    discovery_seed: int = Field(42, title="结构发现种子", description="分类结构发现过程的随机种子。")


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
    max_retries: NonNegativeInt = Field(2, title="最大重试次数", description="模型请求失败后的最多重试次数。")
    timeout_seconds: PositiveFloat = Field(420.0, title="索引请求超时", description="单次索引构建模型请求超时，单位秒。")
    context_window_tokens: NonNegativeInt = Field(0, title="构建上下文窗口", description="上下文 token 预算；0 表示未显式指定。")
    max_output_tokens: NonNegativeInt = Field(0, title="构建输出预算", description="模型输出 token 上限；0 表示未显式指定。")
    classification_batch_limit: PositiveInt = Field(32, title="索引分类批量", description="索引分类请求处理的最多对象数。")


class IndexTaskConfig(SymphonyConfigModel):
    """索引任务的整体时限、回退与失败处理；不与单请求超时混用。"""

    total_timeout_seconds: Annotated[float, Field(ge=0)] = Field(
        0.0, title="构建总时限", description="整个索引任务的时限，单位秒；0 表示未配置总时限。",
    )
    preserve_previous_index_on_failure: bool = Field(
        True, title="失败保留旧索引", description="构建失败时是否保留上一次成功发布的索引。",
    )
    allow_fallback_tree: bool = Field(
        True, title="允许回退分类树",
        description="是否允许构建降级为回退树；False 对应原严格失败策略，缺少模型配置时应提前失败。"
        "启用旧索引保留且存在可用旧索引时，不以回退树覆盖旧索引；运行接入阶段落实这些策略。",
    )


class IndexOutputConfig(SymphonyConfigModel):
    """索引构建的输出与诊断设置，分别控制详细输出、分类树展示、HTML 预览和缓存指标。"""

    generate_tree_html: bool = Field(False, title="生成树预览", description="是否生成分类树 HTML 预览产物。")
    verbose: bool = Field(False, title="详细构建输出", description="是否显示现有构建过程的详细信息。")
    show_tree: bool = Field(True, title="显示分类树", description="是否显示构建后的分类树；区别于生成 HTML 预览文件。")
    display_capability_root: ConfigPath | None = Field(
        None, title="展示对象目录", description="输出展示使用的对象目录文本；不影响扫描根目录。",
    )
    cache_metrics_enabled: bool = Field(
        True, title="缓存指标观测",
        description="是否记录并展示树构建模型请求的缓存命中和提示指纹统计；不控制缓存是否启用。",
    )


class OneShotTreeBuildConfig(SymphonyConfigModel):
    """一次性分类树构建预算，保持 one_shot.py 中独立入口的默认值。"""

    max_depth: PositiveInt = Field(4, title="一次性树深度", description="一次性分类构建允许的最大层级。")
    max_output_tokens: PositiveInt = Field(32768, title="一次性输出上限", description="单次完整分类输出的 token 上限。")
    timeout_seconds: PositiveFloat = Field(420.0, title="一次性请求超时", description="单次分类请求超时，单位秒。")
    reasoning_effort: str | None = Field(None, title="推理强度", description="可选的模型推理强度，具体取值由模型适配器解释。")
    seed: int | None = Field(None, title="一次性构建种子", description="可选的模型随机种子。")


# 公共组合在子模型定义之后声明，默认工厂不需要运行模块或前向引用修复。
class CapabilitySourceConfig(SymphonyConfigModel):
    """能力集合中的一个本地目录及其对象定义格式。"""

    type: CapabilityType = Field(
        title="对象类型", description="对象定义格式；枚举值表达目标支持范围，不表示对应读取器已经实现。",
        examples=["skill", "expert"],
    )
    root: ConfigPath = Field(
        title="对象根目录", description="静态对象定义所在的本地目录；运行层负责解析相对路径。",
        examples=["./capabilities"],
    )


class CapabilityConfig(SymphonyConfigModel):
    """一个 Runtime 的对象定义集合及共享的扫描、指纹策略。"""

    sources: list[CapabilitySourceConfig] = Field(
        min_length=1, title="能力来源目录", description="按目录声明对象定义格式；同一类型可以配置多个目录。",
    )
    scan: DirectoryScanConfig = Field(
        default_factory=DirectoryScanConfig, title="目录扫描", description="目录深度、资源预算和入口文档解析限制。",
    )
    fingerprint: FingerprintPolicyConfig = Field(
        default_factory=FingerprintPolicyConfig, title="指纹策略", description="对象指纹的提取、评估和缓存策略。",
    )

    def require_single_source(self) -> CapabilitySourceConfig:
        """Return the sole source for runtime consumers that require one directory."""
        if len(self.sources) != 1:
            raise ValueError("The current Symphony runtime requires exactly one capability.sources entry")
        return self.sources[0]


class StorageConfig(SymphonyConfigModel):
    """Runtime 产物存储位置；路径检查和目录创建由运行层负责。"""

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
    max_capabilities_per_node_multiplier: PositiveFloat = Field(
        1.5, title="节点容量倍率",
        description="递归构树时，叶节点容量阈值相对 branching_factor 的倍率；"
        "对象数不超过该阈值时可停止细分。阈值取乘积的整数部分，由递归构树运行层计算。",
    )


class RetrievalBuildConfig(SymphonyConfigModel):
    """公共分类树构建策略；直接组合各子策略，默认值沿用索引 toolkit 入口。"""

    structure: RetrievalTreeStructureConfig = Field(
        default_factory=RetrievalTreeStructureConfig, title="树结构", description="分类树深度、分支、根分类输入和叶节点容量倍率。",
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
        default_factory=TreePromptPolicyConfig, title="构建提示", description="提示确定性和结构发现种子。",
    )
    incremental: TreeIncrementalConfig = Field(
        default_factory=TreeIncrementalConfig, title="增量更新", description="变化比例、分类置信度和结构不均衡策略。",
    )
    task: IndexTaskConfig = Field(
        default_factory=IndexTaskConfig, title="构建任务", description="整体时限、回退和失败处理；区别于单次请求超时。",
    )
    output: IndexOutputConfig = Field(
        default_factory=IndexOutputConfig, title="输出与诊断", description="构建输出、分类树展示、预览产物和缓存指标设置。",
    )


class RetrievalConfig(SymphonyConfigModel):
    """公共检索配置；组合分类树构建和对象发现，启用不会自动构建索引。"""

    prompt: DiscoveryPromptConfig = Field(
        default_factory=DiscoveryPromptConfig, title="提示策略", description="候选提示的上下文容量、预算比例和优先对象设置。",
    )
    output: DiscoveryOutputConfig = Field(
        default_factory=DiscoveryOutputConfig, title="输出预算", description="发现结果、列表和增量提示的限制。",
    )
    use_existing_index: bool = Field(False, title="使用已有索引", description="是否使用已经构建的检索索引辅助发现。")
    enabled: bool = Field(True, title="启用检索", description="允许构建和读取分类树、创建发现会话；独立于编排开关。")
    index_build: RetrievalBuildConfig = Field(
        default_factory=RetrievalBuildConfig, title="索引构建", description="显式构建分类树时使用的策略。",
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
    experience: OrchestrationExperienceConfig = Field(
        default_factory=OrchestrationExperienceConfig, title="编排经验",
        description="从执行证据更新动态图并沉淀组合能力包。",
    )

    @property
    def experience_enabled(self) -> bool:
        """Whether this orchestration runtime accepts new experience."""
        return self.enabled and self.experience.enabled


class SymphonyConfig(SymphonyConfigModel):
    """SymphonyRuntime 使用的公共运行配置。"""

    capability: CapabilityConfig = Field(title="对象集合", description="显式声明来源目录列表及共享的预处理策略。")
    storage: StorageConfig = Field(title="产物存储", description="显式声明本集合的产物根目录。")
    retrieval: RetrievalConfig = Field(
        default_factory=RetrievalConfig, title="检索能力", description="分类树构建与对象发现的默认配置。",
    )
    orchestration: OrchestrationConfig = Field(
        default_factory=OrchestrationConfig, title="编排能力", description="关系图构建、计划生成和经验沉淀的默认配置。",
    )


# 登记范围用于 Schema 覆盖和文档；只有根模型可达的配置属于公共树。
PUBLIC_CONFIG_MODELS: tuple[type[SymphonyConfigModel], ...] = (
    SymphonyConfig, CapabilityConfig, CapabilitySourceConfig,
    DirectoryScanConfig, ScanLimitsConfig, ManifestParseConfig,
    FingerprintPolicyConfig, FingerprintExtractionConfig, FingerprintEvaluationConfig, FingerprintCacheConfig,
    StorageConfig, RetrievalConfig, RetrievalBuildConfig, RetrievalTreeStructureConfig, IndexRequestConfig,
    TreePostprocessConfig, TreeEquivalenceConfig, CapabilityProfilesConfig, TreePromptPolicyConfig,
    TreeIncrementalConfig, IndexTaskConfig, IndexOutputConfig,
    DiscoveryOutputConfig, DiscoveryPromptConfig,
    OrchestrationConfig, OrchestrationBuildConfig, GraphMatchingPolicyConfig, OrchestrationPlanConfig,
    OrchestrationExperienceConfig, FlowAcceptanceConfig, FlowNarrativeConfig,
)

INTERNAL_CONFIG_MODELS: tuple[type[SymphonyConfigModel], ...] = (
    FingerprintProtocolConfig, OneShotTreeBuildConfig,
)

CONFIG_MODELS: tuple[type[SymphonyConfigModel], ...] = PUBLIC_CONFIG_MODELS + INTERNAL_CONFIG_MODELS

# 保留核心配置类的便利导出；子模型可通过嵌套字典配置，内部工具显式读取登记表。
__all__ = [
    "CapabilityType", "CapabilityConfig", "StorageConfig", "RetrievalBuildConfig",
    "RetrievalConfig", "OrchestrationBuildConfig", "OrchestrationPlanConfig", "OrchestrationConfig",
    "OrchestrationExperienceConfig", "SymphonyConfig",
]
