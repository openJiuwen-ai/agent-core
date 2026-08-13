# openjiuwen.symphony

`openjiuwen.symphony` 是 openJiuwen 的能力资产发现、指纹、评估、检索、编排和经验沉淀领域模块，
用于组织 Agent 运行时可用的 `skill`、`agent` 及其他可扩展能力类型。

Symphony 的最终设计围绕 Agent 能力资产提供以下核心能力：

- **能力指纹**：从能力资产中提取语义画像、输入输出语义和能力标签，形成供检索、编排和评估共享的标准化能力指纹。
- **能力检索**：将能力资产组织成可逐层浏览和检索的树结构，帮助 Agent 找到候选能力。
- **能力编排**：将能力视图组织成关系图，并基于候选能力生成可解释的执行路线。
- **经验沉淀**：从会话轨迹中提取能力使用模式，构建可检索的经验知识库。
- **能力评估**：评估智能体能力、技能质量和能力组合效果，为分发、推荐和持续优化提供依据。
- **Agent Toolkit**：向 Agent 提供组合检索和编排的统一调用入口。

## 实施状态

本文档同时描述最终设计和当前实现。“当前可运行接口”描述当前可直接导入的能力；
“最终 Runtime 与统一接口蓝图”中明确标注为目标形态的代码示例不代表当前已有对应 facade。

| 能力域 | 当前状态 | 说明 |
| --- | --- | --- |
| 能力指纹 | 已实现 | 已提供普通能力清单、显式 Skill 扫描、语义画像、IO 归一化、增量 cache 和带 schema 版本的 `fingerprint.json`。 |
| 能力检索 | 待迁入 | 最终提供能力树构建、浏览和自然语言检索。 |
| 能力编排 | 已实现 | 已提供关系图构建、版本化产物、Fast/Beam Planner 和执行图。 |
| 经验沉淀 | 部分实现 | 独立模块已提供轨迹解析、评估、经验库构建和检索；尚未接入 `SymphonyRuntime`。 |
| 能力评估 | 已实现 | 已提供可注册的静态与轨迹指标，并保留 reason、evidence、failure 和 suggestion。 |
| Agent Toolkit | 待迁入 | 最终组合检索和编排，供 Agent 调用。 |
| 公共 `models` | 已实现 | 提供 capability、fingerprint、evaluation 和 normalization 的不可变公开模型。 |

## 功能边界

Symphony 处理能力资产的发现、检索、关系建模、编排、经验沉淀和评估。使用方负责提供配置和能力资产，通过 Adapter 将运行时信息转换为 Symphony 可消费的普通对象，并负责能力安装或注册、模型账号、会话状态、权限控制、能力调用入口和 UI 展示。

| 核心功能 | 说明 | 典型输出 |
| --- | --- | --- |
| 能力指纹 | 提取并归一化能力资产的语义画像、输入输出和标签。 | 能力清单、能力指纹、归一化标签 |
| 能力检索 | 建立树索引，支持按任务逐层探索候选能力。 | 分支摘要、候选能力、能力 ID 列表 |
| 能力编排 | 根据候选能力及其关系图生成执行路线。 | 编排计划、执行图、缺失输入说明 |
| 经验沉淀 | 从执行轨迹提炼可复用的能力使用模式。 | 经验条目、候选能力 ID 列表 |
| 能力评估 | 评估智能体能力、技能质量和能力组合效果。 | 评估结果、指标汇总、改进建议 |

使用方通过 Adapter 接入 Symphony：

```text
使用方配置 / 能力资产目录 / LLM 配置
  ↓
Adapter
  ↓
openjiuwen.symphony
```

Symphony 不隐式猜测或扫描应用目录、不读取应用配置、不注册或执行使用方的能力，也不依赖具体使用方代码。
`SkillFolderScanner` 只扫描调用方显式传入的根目录。以 JiuwenSwarm 为例，应用 Adapter 负责把内部能力标识
转换为公共领域的 `capability_id`，并把应用侧配置、默认模型、禁用项和动态关系映射为 Symphony 输入。

## 架构总览

Symphony 的核心结构是“检索用树，编排用图”。能力指纹作为检索和编排共享的语义视图：

```text
capability_assets_root
  ↓
inventory
  ↓
shared/fingerprint
  ├── retrieval tree
  │     ↓
  │   AgenticSymphonyToolkit.retrieval
  │     ↓
  │   candidate capability_id
  │
  └── orchestration/graph
        ↓
      graph.json
        ↓
      orchestration planner
        ↓
      execution_graph
```

- `shared/fingerprint` 扫描或消费能力清单，抽取语义画像，归一化 IO，并原子发布带 schema 版本的能力指纹。
- `retrieval` 构建并读取能力树索引，向 Agent 渐进披露分支和候选能力。
- `orchestration/graph` 生成关系候选、进行 IO/语义匹配、判断关系并构建能力图。
- `orchestration` 管理图产物，并根据候选能力和任务目标生成执行路线。
- `experience` 评估会话轨迹、沉淀能力使用经验，并支持经验检索。
- `evaluation` 提供可注册的静态质量和调用方轨迹评估，不固化业务准入阈值。
- `interfaces` 定义使用方需要实现或传入的协议。
- `agent` 提供 Agent-facing toolkit；使用方负责将 toolkit 方法接入自己的调用体系。

能力图是编排领域的内部组成，不是一个独立的 Runtime 服务；公共运行时通过
`SymphonyRuntime.orchestration` 暴露图生命周期和规划能力。`FingerprintService`、`EvaluationSuite` 和
experience 领域能力已可独立使用，但尚未组合进 `SymphonyRuntime`。

### 检索树中的 Skill 等价群

显式开启等价群后，检索树构建会先在同一分类分支内召回候选 Skill，再逐对判断核心能力是否相同或相近，并用
确定性合并生成等价群节点。平台、供应商、API/CLI、输入形式等实现差异可以归为一组；宽能力包含窄
能力时，只要共享能力是双方的主要能力，也可以归为一组。仅有关键词重合、附带功能重合或属于互补
步骤的 Skill 仍保持分开；任意一对明确不相似的 Skill 不会被传递关系合并到同一群。

该能力由 `SkillIndexBuildConfig.equivalence_enabled`（离线构建对应
`BuildConfig.tree_equiv_grouping_enabled`）控制，默认关闭，需要时显式开启。词面相似度二次拆分默认关闭；需要更保守的
确定性拆分时，可显式设置 `equivalence_min_lexical_similarity`（离线构建对应
`tree_equiv_min_lexical_similarity`）为大于 `0` 的值。

`AgenticSkillRetrievalToolkit.build_index()` 自动执行增量构建时，成功结果的
`data.capability_category_paths` 会返回本批新增或更新 Skill 的最终分类路径，例如
`[{"capability_id": "weather", "category_path": ["Information", "Weather"]}]`。`category_path` 按层级
保存分类节点 ID，长度与实际能力树一致，不包含 Skill 叶子节点；删除、复用已有索引、首次构建和显式
强制全量构建时返回空列表。异步构建启动结果仍只返回 `build_id`，完成后可通过现有
`check_build_status(build_id)` 的同名字段取得结果。

## 最终目录蓝图

Symphony 作为 agent-core 的原生模块，源码直接位于 `openjiuwen/symphony`，不增加重复的包目录层级：

```text
agent-core
├── openjiuwen
│   └── symphony
│       ├── README.md
│       ├── __init__.py
│       ├── runtime.py
│       ├── interfaces
│       ├── agent                         # 待迁入
│       ├── retrieval                     # 待迁入
│       ├── orchestration
│       │   ├── graph
│       │   │   ├── candidates
│       │   │   └── matcher              # 内部实现，不作为公共 API
│       │   └── planning
│       ├── experience                    # 独立模块已实现，Runtime 接入待完成
│       ├── evaluation
│       ├── models
│       └── shared
│           └── fingerprint
├── tests
│   └── unit_tests
│       └── symphony
├── examples
│   └── symphony
└── docs
```

- Python 源码进入 `openjiuwen/symphony/`。
- 图构建模型和内部关系匹配实现属于编排领域，保留在 `orchestration/graph/`。
- 指纹与评估公共模型位于 `models/`；graph 内部模型仍保留在 `orchestration/graph/`。
- 单元测试进入 `tests/unit_tests/symphony/`，示例进入 `examples/symphony/`。
- 完整的中英文使用指南进入 agent-core 的 `docs` 文档体系。
- 依赖、构建和发布配置由 agent-core 根目录的 `pyproject.toml` 统一管理。

## 当前可运行接口

当前 `SymphonyRuntime` 只组合 `OrchestrationService`。配置、能力清单、LLM 和产物目录都由使用方显式注入；关系匹配由 Runtime 内部完成。`openjiuwen.symphony.experience` 已提供可单独导入的领域能力，但尚未组合到 Runtime。

```python
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.symphony import (
    CapabilityFingerprint,
    CapabilityIO,
    OrchestrationConfig,
    SymphonyRuntime,
)


model = Model(
    model_client_config=ModelClientConfig(
        client_provider="OpenAI",
        api_base="https://example.com/v1",
        api_key="...",
    ),
    model_config=ModelRequestConfig(model="example-model", temperature=0),
)


async def on_progress(event):
    pass


def list_capabilities():
    return [
        CapabilityFingerprint(
            capability_id="extract-text",
            capability_type="skill",
            name="Extract text",
            description="Extract text from a document",
            version="1.0.0",
            outputs=(CapabilityIO(name="text", type="text"),),
        ),
        CapabilityFingerprint(
            capability_id="summarize",
            capability_type="agent",
            name="Summarize",
            description="Summarize text",
            version="1.0.0",
            inputs=(CapabilityIO(name="text", type="text"),),
        ),
    ]


symphony = SymphonyRuntime(
    graph_artifact_root=".artifacts/symphony-graph",
    capability_provider=list_capabilities,
    model=model,
    orchestration_config=OrchestrationConfig(mode="fast"),
)

build_result = await symphony.orchestration.build(
    force=False,
    progress=on_progress,
)
graph = symphony.orchestration.read()
plan = await symphony.orchestration.plan(
    query="Extract and summarize this document",
    candidate_ids=["extract-text", "summarize"],
    language="en",
    progress=on_progress,
)
```

也可以直接构造 `OrchestrationService`：

```python
from openjiuwen.symphony import OrchestrationService

service = OrchestrationService(
    graph_artifact_root=".artifacts/symphony-graph",
    capability_provider=list_capabilities,
    model=model,
)
```

`capability_provider` 可以直接传入能力序列，也可以是返回能力序列的同步或异步函数。
构造器还可以通过 `model_response_observer` 观测模型响应，通过 `source_snapshot` 补充使用方的构建身份，并通过 `prepare_artifact` 在原子发布前准备版本目录中的附加产物。

### FingerprintService

指纹构建使用带稳定快照的异步 `CapabilityProvider`。推荐通过 `inventory_snapshot()` 原子返回快照与能力集合；
未实现该扩展时，pipeline 会在读取能力前后分别检查 `source_snapshot()`，并拒绝构建期间发生变化的 inventory。

```python
from pathlib import Path

from openjiuwen.symphony import CapabilityDescriptor, FingerprintService, SourceSnapshot


class FingerprintCapabilityProvider:
    async def inventory_snapshot(self):
        capabilities = await self.capabilities()
        snapshot = SourceSnapshot(
            snapshot_id="inventory-1",
            capability_count=len(capabilities),
        )
        return snapshot, capabilities

    async def capabilities(self):
        return [
            CapabilityDescriptor(
                capability_id="document-summary",
                capability_type="skill",
                name="Document summary",
                description="Summarize a supplied document.",
                source="example-adapter",
            ),
        ]

    async def source_snapshot(self):
        return SourceSnapshot(snapshot_id="inventory-1", capability_count=1)


fingerprints = FingerprintService(
    capability_provider=FingerprintCapabilityProvider(),
    artifact_root=Path(".artifacts/symphony-fingerprint"),
)
artifact = await fingerprints.build()
loaded = fingerprints.read()
```

`SkillFolderScanner("./skills")` 是显式根目录的扫描便利实现。它只将 `SKILL.md` 作为语义输入，完整资产只
用于安全 hash；调用方提供的扫描根目录被视为可信本地目录，所有平台都使用普通路径 I/O。扫描不主动遍历
symlink、junction 或其他 reparse point，并排除凭据、版本控制和缓存目录。目录、文件、字节数及 manifest
深度都有显式上限。

额外模型调用默认关闭。打开 `FingerprintSettings.enable_llm_extraction` 或
`enable_llm_evaluation` 时，调用方必须显式注入实现 `SymphonyLLM.invoke(...)` 的对象；缺少模型时返回
明确配置错误，不会默认判定通过。

### 能力评估

`EvaluationSuite` 支持注册同步或异步 evaluator。内置静态指标包括结构规范性、描述质量和分类一致性；
轨迹指标包括成功率、时延、准确性、完整性、能力选择和组合效果。调用轨迹由使用方以
`EvaluationCase` / `CapabilityCall` 显式传入，Symphony 不执行真实能力。

每个 metric 保留 `status`、可选 `[0, 1]` score、reason、脱敏 evidence、failure 和 suggestion。
默认不生成跨指标 composite score，也不固化准入阈值；没有目标值的时延只输出 raw observation。
同一窗口内部分 evaluator 或 LLM 解析异常时，`EvaluationSuite` 只使用 `score is not None` 的可用评分样本
计算均值和 pass/fail，异常样本不进入分母，但 `error_count` 与对应诊断仍会保留；只有所有样本都无法评分且
存在异常时，聚合结果才为 `error`。没有评分且没有异常时，仍按原有 `observed`/`not_applicable` 规则返回。

LLM judge 接受裸 JSON，或完整包裹整个响应的 Markdown 三反引号代码块；代码块语言标签可以省略或使用
`json`、`arduino` 等任意标签，但块内仍须为标准 JSON 对象。外围说明文字、多个代码块、JSON5、单引号、
尾逗号和不完整 JSON 不会被本地启发式修复。JSON 解析或 `score`/`reason` 字段校验失败时，evaluator 会将
安全的具体校验原因与脱敏、限长的原始坏输出回传给同一个 LLM，并固定重做一次；重试成功后按正常评分返回，
重试仍失败才返回 `error`。首次调用的网络、超时、限流或鉴权异常不会在 Evaluation 层再次重试，以免与模型
传输层重试叠加。原始坏输出和完整异常文本不会写入最终评估结果。

### 指纹产物生命周期

```text
fingerprint_artifact_root/
├── fingerprint.json
├── .fingerprint-cache.json
└── .fingerprint.lock
```

`fingerprint.json` 顶层包含 schema 版本、UTC 生成时间、source snapshot 和 fingerprints。每个公开指纹使用
`capability_id` / `capability_type`，包含语义画像、归一化 IO、分类、标签、内容 hash、质量结果、失败原因、
脱敏证据引用和改进建议。构建采用原子发布，失败或取消不会覆盖最近成功版本。

### 复用 agent-core Model

Symphony 直接使用上例中 agent-core 的 `Model`，复用统一的 provider、连接池和回调，不增加额外 LLM 包装层。Symphony 内部统一将编排请求转换为 `Model.invoke()`，并处理 timeout、请求覆盖、错误上下文和 JSON 修复；模型账号及默认模型的选择仍由使用方显式注入。

### OrchestrationService

当前服务接口为：

```python
status = service.status(expected_snapshot=None)
build_result = await service.build(
    force=False,
    progress=on_progress,
    prepare_artifact=None,
)
cancel_status = await service.cancel_build()
graph = service.read(version=None)
plan = await service.plan(
    query,
    candidate_ids,
    language="cn",
    progress=on_progress,
    disabled_capability_ids=None,
    dynamic_overlay=None,
    mode=None,
)
```

- `status()` 返回 `GraphArtifactStatus`。同步 provider 会参与 source snapshot 新鲜度判断；异步 provider 可通过 `expected_snapshot=...` 查询新鲜度。
- `build()` 返回 `GraphBuildResult`，完成暂存后才原子切换 `current.json`。
- `cancel_build()` 请求取消当前构建，并返回取消请求后的图产物状态。
- `read()` 返回映射兼容的 `CapabilityGraph`；传入 `version` 可读取指定的不可变版本。
- `plan()` 返回映射兼容的 `OrchestrationPlan`。
- `progress` 接收 `OrchestrationProgress`；该类型保持字典兼容。旧参数名 `progress_callback` 仍可使用。
- `model=None` 时仍可查询状态和读取已发布图；构建或规划会明确报错。

### 图产物生命周期

图产物目录结构为：

```text
graph_artifact_root/
├── cache/
│   └── relation_matches.json
├── current.json
├── versions/
│   └── <version>/
│       └── graph.json
└── .build_runs/
```

- `graph.json` 包含 `schema_version`、能力快照、节点、边、lookup、诊断信息和构建配置。
- 每次成功构建先在 `.build_runs/` 写入临时目录，再原子移动到不可变版本目录，最后原子发布 `current.json`。
- 读取时校验 schema 主版本；不支持的版本会被拒绝。
- 构建失败或取消不会切换当前指针，最后一次成功发布的版本保持可读。
- `force=False` 且 source snapshot 未变化时复用当前产物；能力清单变化后状态会标记为不新鲜。
- `force=False` 时可复用 `cache/relation_matches.json` 中身份匹配的关系判断；`force=True` 完全绕过该缓存。缓存不属于已发布的版本化图产物。
- 同一服务实例的构建互斥，避免并发发布互相覆盖。

### 图构建配置

构造服务或 Runtime 时可通过 `graph_config` 控制内部关系匹配和候选生成器：

```python
service = OrchestrationService(
    graph_artifact_root=".artifacts/symphony-graph",
    capability_provider=list_capabilities,
    model=model,
    graph_config={
        "batch_size": 8,
        "workers": 4,
        "require_consensus": True,
        "max_candidates_per_skill_relation": 24,
        "max_port_mappings_per_candidate": 12,
        "max_exact_io_pair_fanout": 64,
    },
)
```

`max_workers` 是 `workers` 的等价显式名称，并在两者同时出现时优先。上述配置会写入产物，也实际驱动关系候选生成和内部匹配行为。调用方只需注入 agent-core `Model`；Runtime 会在构建时创建内部关系匹配器，并在非强制构建中复用 `cache/relation_matches.json`。

### Fast、Beam 与运行时过滤

`OrchestrationConfig(mode="fast")` 使用一次性 LLM 规划；`mode="beam"` 使用双向 beam search。两种模式读取同一版本化能力图，并支持：

- 通过 `candidate_ids` 限定候选能力；
- 通过 `disabled_capability_ids` 过滤禁用能力；
- 选择中英文摘要；
- 分析缺失输入并生成稳定的执行图；
- 通过进度回调报告构建、关系匹配和规划阶段事件。

动态 overlay 默认关闭。只有 `OrchestrationConfig(dynamic_graph_enabled=True)` 时，传给 `plan(dynamic_overlay=...)` 的运行时边权覆盖才会参与 Fast 规划；overlay 不改写离线图产物。

## 最终 Runtime 与统一接口蓝图（部分尚未接入）

以下接口展示 Symphony 多能力域统一接入 Runtime 后的目标形态。当前版本尚不存在 `runtime.retrieval`、
`runtime.experience`、`runtime.evaluation` 或 `runtime.agent_toolkit(...)`；指纹、评估和经验能力当前通过各自的
公开服务或领域包独立使用。

### Adapter 协议

最终设计仍由使用方提供 Adapter，Symphony 不直接耦合使用方的配置系统和资产目录。以下是目标协议的概念示例，具体类型将在对应模块迁入时确定：

```python
# 最终目标示例；当前不可运行。
from pathlib import Path
from typing import Protocol, Sequence

from openjiuwen.core.foundation.llm import Model
from openjiuwen.symphony import CapabilityFingerprint


class CapabilityInventoryProvider(Protocol):
    def assets_root(self) -> Path: ...

    def list_capabilities(self) -> Sequence[CapabilityFingerprint]: ...


class ModelProvider(Protocol):
    def model(self) -> Model: ...


class ArtifactPathProvider(Protocol):
    def tree_artifact_root(self) -> Path: ...

    def graph_artifact_root(self) -> Path: ...

    def experience_artifact_root(self) -> Path: ...
```

能力清单使用 `capability_id` 作为统一标识，并通过 `capability_type` 区分不同资产类型。

### 能力指纹

当前 `FingerprintService` 已提供显式资产扫描、语义画像、IO 归一化和版本化产物。最终 Runtime 将组合该
服务，并把同一份标准化指纹交给检索、编排和评估；使用方仍可显式提供普通对象或
`CapabilityFingerprint`，无需让 Symphony 读取应用内部注册表。

### 能力检索

最终检索服务将已注册能力组织成树索引，并向 Agent 按需披露分支和候选能力：

```python
# 最终目标示例；当前不可运行。
status = symphony.retrieval.status()
if not status.exists or not status.fresh:
    await symphony.retrieval.build(force=False)

result = await symphony.retrieval.search(
    "读取 PDF 内容并生成摘要",
)
candidate_ids = [
    capability.capability_id
    for capability in result.capabilities
]
```

目标服务包括索引状态查询、构建与取消、树摘要读取、分支探索及自然语言检索；检索结果包含候选能力、命中分支、排序信息和产物版本。

### 多服务 Runtime

最终 `SymphonyRuntime` 将组合检索、编排、经验和评估子服务：

```python
# 最终目标示例；当前不可运行。
from openjiuwen.symphony import SymphonyRuntime

symphony = SymphonyRuntime(
    inventory_provider=adapter.capability_inventory_provider(),
    tree_artifact_root=adapter.tree_artifact_root(),
    graph_artifact_root=adapter.graph_artifact_root(),
    experience_artifact_root=adapter.experience_artifact_root(),
    retrieval_settings=adapter.retrieval_settings(),
    orchestration_settings=adapter.orchestration_settings(),
    model=adapter.model(),
)

retrieval_result = await symphony.retrieval.search(
    "读取 PDF 内容并生成摘要",
)
candidate_ids = [
    capability.capability_id
    for capability in retrieval_result.capabilities
]
plan = await symphony.orchestration.plan(
    query="读取 PDF 内容并生成摘要",
    candidate_ids=candidate_ids,
)
```

### 经验沉淀

当前独立的 [`openjiuwen.symphony.experience`](experience/API.md) 包已提供轨迹解析、评估、经验库构建和检索。下例展示未来统一 Runtime facade 的目标形态，当前不可运行：

```python
# 最终目标示例；当前不可运行。
records = await symphony.experience.evaluate(traces)
await symphony.experience.build(records)
experience_result = await symphony.experience.search(
    "读取 PDF 内容并生成摘要",
)
candidate_ids = experience_result.candidate_ids
```

现有领域对象包括轨迹记录、轨迹评估器、经验库、经验库构建器和经验检索器；统一 Runtime 接口将在集成时以现有实现和测试为准。

### 能力评估

当前 `EvaluationSuite` 已支持独立的静态和轨迹评估。以下示例是未来 Runtime facade 面向单项能力、Agent
能力和能力组合的目标形态：

```python
# 最终目标示例；当前不可运行。
evaluation = await symphony.evaluation.evaluate_capabilities(
    capability_ids=candidate_ids,
    traces=traces,
)
```

### Agent Toolkit

最终 `AgenticSymphonyToolkit` 组合检索和编排能力，使用方可以将其方法接入自己的 Agent 调用体系：

```python
# 最终目标示例；当前不可运行。
toolkit = symphony.agent_toolkit(language="cn")

retrieval_result = await toolkit.retrieval.search(
    "读取 PDF 内容，总结后发送邮件",
)
candidate_ids = [
    capability.capability_id
    for capability in retrieval_result.capabilities
]
plan = await toolkit.orchestration.plan(
    query="读取 PDF 内容，总结后发送邮件",
    candidate_ids=candidate_ids,
)
```

- `toolkit.retrieval` 最终提供根分类渲染、分支预览、分支展开和自然语言检索。
- `toolkit.orchestration` 最终提供关系图读取、刷新和在线计划生成。
- Toolkit 只提供 Agent-facing 领域方法；工具注册、权限、进度展示和 UI 由使用方负责。

## 最终运行时产物

指纹、检索、编排和当前独立的经验模块使用调用方指定的产物目录：

```text
fingerprint_artifact_root/
├── fingerprint.json
├── .fingerprint-cache.json
└── .fingerprint.lock

tree_artifact_root/
├── current.json
└── versions/<version>/tree.json

graph_artifact_root/
├── cache/relation_matches.json
├── current.json
├── versions/<version>/graph.json
└── .build_runs/

experience_kb/
├── meta.json
├── scalar/metadata.jsonl
└── vector/
    ├── faiss_index.bin
    └── embeddings.npy

<session-parent>/trace_store/
├── processed_index.json
└── records.jsonl
```

- `fingerprint.json` 当前保存标准化能力指纹、质量结果、诊断和 source snapshot。
- `tree.json` 最终保存能力树索引、能力资产清单快照和版本信息。
- `graph.json` 当前已保存能力节点、关系边、在线规划 lookup 和版本信息。
- `experience_kb` 是当前独立经验库的调用方指定目录；`trace_store` 位于调用方传入的 session 目录同级。
- 机器读写产物使用 JSON；YAML 用于配置、prompt 或人工维护的说明文件。

## 开发与验证

Symphony 使用 agent-core 的统一开发环境和质量检查入口：

```bash
uv sync
make test TESTFLAGS="tests/unit_tests/symphony"
make check COMMITS=1
make type-check COMMITS=1
```

- `pyproject.toml` 是 Python、依赖和工具配置的唯一事实来源。
- `Makefile` 定义常用测试和检查入口。
- `COMMITS=1` 选择最近一个提交中变更的 Python 文件；不传时默认检查已暂存的 Python 文件。
- 可选依赖由 agent-core 的依赖体系统一管理。

## 模块约定

- 公开导入路径以 `openjiuwen.symphony` 开头。
- 公共 capability、fingerprint 和 evaluation 模型位于 `models/`；编排 graph 的构建模型属于内部实现。
- 编排公共领域模型及 Symphony 最终统一契约使用 `capability_id`、`capability_type` 和 `candidate_ids`。当前独立 experience API 中的 `skills` 和 `skill_ids` 是待 Runtime 集成时统一的过渡命名。
- 运行时资源随 `openjiuwen.symphony` 一同打包。
- Symphony 保持使用方无关，不包含使用方专属的 gateway、Web/TUI、卡片类型或 prompt rail。
- 使用方负责将 Symphony 服务或最终 Toolkit 接入自己的调用体系，并将内部标识映射为公共能力标识。
- 初版模块以新的公开路径为准，不提供其他导入路径的兼容层。
