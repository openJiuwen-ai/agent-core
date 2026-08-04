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

本文档同时描述最终设计和当前实现。除“当前可运行接口”章节明确列出的接口外，其余代码示例均为后续目标，
不代表当前已有可导入的 Python API。

| 能力域 | 当前状态 | 说明 |
| --- | --- | --- |
| 能力指纹 | 已实现 | 已提供普通能力清单、显式 Skill 扫描、语义画像、IO 归一化、增量 cache 和带 schema 版本的 `fingerprint.json`。 |
| 能力检索 | 待迁入 | 最终提供能力树构建、浏览和自然语言检索。 |
| 能力编排 | 已实现 | 已提供关系图构建、版本化产物、Fast/Beam Planner 和执行图。 |
| 经验沉淀 | 待迁入 | 最终提供轨迹评估、经验库构建和经验检索。 |
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

能力图是编排领域的内部组成，不是一个独立的 Runtime 服务；公共运行时当前通过
`SymphonyRuntime.orchestration` 暴露图生命周期和规划能力。`FingerprintService` 与 `EvaluationSuite` 已可独立使用，
但尚未组合进 `SymphonyRuntime`。

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
│       │   │   └── matcher
│       │   └── planning
│       ├── experience                    # 待迁入
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
- 图构建模型和 Matcher 属于编排领域，保留在 `orchestration/graph/`。
- 指纹与评估公共模型位于 `models/`；graph 内部模型仍保留在 `orchestration/graph/`。
- 单元测试进入 `tests/unit_tests/symphony/`，示例进入 `examples/symphony/`。
- 完整的中英文使用指南进入 agent-core 的 `docs` 文档体系。
- 依赖、构建和发布配置由 agent-core 根目录的 `pyproject.toml` 统一管理。

## 当前可运行接口

当前 `SymphonyRuntime` 只组合 `OrchestrationService`。配置、能力清单、LLM、Matcher 和产物目录都由使用方显式注入。

```python
from openjiuwen.symphony import (
    CapabilityFingerprint,
    CapabilityIO,
    OrchestrationConfig,
    SymphonyRuntime,
)


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
    llm_client=llm_client,
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
    llm_client=llm_client,
)
```

`capability_provider` 可以直接传入能力序列，也可以是返回能力序列的同步或异步函数。

### FingerprintService

指纹构建使用带稳定快照的异步 `CapabilityProvider`。推荐通过 `inventory_snapshot()` 原子返回快照与能力集合；
未实现该扩展时，pipeline 会在读取能力前后分别检查 `source_snapshot()`，并拒绝构建期间发生变化的 inventory。

```python
from pathlib import Path

from openjiuwen.symphony import CapabilityDescriptor, FingerprintService, SourceSnapshot


class FingerprintCapabilityProvider:
    async def inventory_snapshot(self):
        capabilities = await self.capabilities()
        return SourceSnapshot(snapshot_id="inventory-1", capability_count=len(capabilities)), capabilities

    async def capabilities(self):
        return [
            CapabilityDescriptor(
                capability_id="document-summary",
                capability_type="skill",
                name="Document summary",
                description="Summarize a supplied document.",
                source="example-adapter",
            ),
            CapabilityDescriptor(
                capability_id="research-agent",
                capability_type="agent",
                name="Research agent",
                description="Research a topic and return supported findings.",
                source="example-adapter",
            ),
        ]

    async def source_snapshot(self):
        return SourceSnapshot(snapshot_id="inventory-1", capability_count=2)


fingerprints = FingerprintService(
    capability_provider=FingerprintCapabilityProvider(),
    artifact_root=Path(".artifacts/symphony-fingerprint"),
)
artifact = await fingerprints.build()
loaded = fingerprints.read()
```

`SkillFolderScanner("./skills")` 是显式根目录的扫描便利实现。它只将 `SKILL.md` 作为语义输入，完整资产只
用于安全 hash；扫描不跟随 symlink，并排除 `.env`、凭据、版本控制和缓存目录。目录、文件、字节数及
manifest 深度都有显式上限，不支持安全 anchored no-follow open 的平台会 fail closed。

额外模型调用默认关闭。打开 `FingerprintSettings.enable_llm_extraction` 或
`enable_llm_evaluation` 时，调用方必须显式注入实现 `SymphonyLLM.invoke(...)` 的适配器；缺少模型时返回
明确配置错误，不会默认判定通过。agent-core 异步 `Model` 可直接注入。

### 能力评估

`EvaluationSuite` 支持注册同步或异步 evaluator。内置静态指标包括结构规范性、描述质量和分类一致性；
轨迹指标包括成功率、时延、准确性、完整性、能力选择和组合效果。调用轨迹由使用方以
`EvaluationCase` / `CapabilityCall` 显式传入，Symphony 不执行真实能力。

每个 metric 保留 `status`、可选 `[0, 1]` score、reason、脱敏 evidence、failure 和 suggestion。
默认不生成跨指标 composite score，也不固化准入阈值；没有目标值的时延只输出 raw observation。

### 指纹产物生命周期

指纹产物目录为：

```text
fingerprint_artifact_root/
├── fingerprint.json
├── .fingerprint-cache.json
└── .fingerprint.lock
```

`fingerprint.json` 顶层包含 `schema_version="1.0"`、UTC `generated_at`、`source_snapshot` 和
`fingerprints`。每个公开指纹使用 `capability_id` / `capability_type`，包含语义画像、归一化 IO、分类、
标签、内容 hash、质量结果、失败原因、脱敏证据引用和改进建议；不公开旧 `id` / `type` 字段。

- 缺少 schema 版本或遇到不支持的主版本会明确失败；同一主版本允许忽略未知扩展字段。
- JSON 严格拒绝 NaN/Infinity。
- 发布使用同目录临时文件、file fsync、原子替换和 directory fsync；失败或取消不覆盖最近成功版本。
- 私有 cache 同时绑定 schema、抽取/评估 protocol、配置签名、descriptor/content hash 和相关 trace，
  并完整复用 diagnostics、脱敏 normalization audit、动态 IO-name vocabulary 和质量 evidence。
- 单项抽取或评估失败转为该能力的结构化 failure；无效 inventory、缺少必需 LLM 或产物不可写等全局错误终止发布。

### 复用 agent-core LLM

Symphony 保留最小的 `LLMClient` 协议，便于测试或接入其他模型运行时；使用 agent-core 时，推荐通过
`OpenJiuwenLLMClient` 复用统一的 `Model`、provider、连接池、回调和 usage metadata：

```python
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.symphony import OpenJiuwenLLMClient

model = Model(
    model_client_config=ModelClientConfig(
        client_provider="OpenAI",
        api_base="https://example.com/v1",
        api_key="...",
    ),
    model_config=ModelRequestConfig(
        model="example-model",
        temperature=0,
    ),
)
llm_client = OpenJiuwenLLMClient(model)
```

该 Adapter 只负责将 Symphony 的 JSON completion 请求转换为 `Model.invoke()`，并保留 timeout、请求覆盖、
错误上下文和 JSON 修复；模型账号及默认模型的选择仍由使用方显式注入。

### OrchestrationService

当前服务接口为：

```python
status = service.status()
build_result = await service.build(force=False, progress=on_progress)
cancel_status = await service.cancel_build()
graph = service.read()
plan = await service.plan(
    query,
    candidate_ids,
    language="cn",
    progress=on_progress,
)
```

- `status()` 返回 `GraphArtifactStatus`。同步 provider 会参与 source snapshot 新鲜度判断；异步 provider 可通过 `expected_snapshot=...` 查询新鲜度。
- `build()` 返回 `GraphBuildResult`，完成暂存后才原子切换 `current.json`。
- `cancel_build()` 请求取消当前构建，并返回取消请求后的图产物状态。
- `read()` 返回映射兼容的 `CapabilityGraph`。
- `plan()` 返回映射兼容的 `OrchestrationPlan`。
- `progress` 接收 `OrchestrationProgress`；该类型保持字典兼容。旧参数名 `progress_callback` 仍可使用。

### 图产物生命周期

图产物目录结构为：

```text
graph_artifact_root/
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
- 同一服务实例的构建互斥，避免并发发布互相覆盖。

### 图构建配置

构造服务或 Runtime 时可通过 `graph_config` 控制默认 LLM Matcher 和候选生成器：

```python
service = OrchestrationService(
    graph_artifact_root=".artifacts/symphony-graph",
    capability_provider=list_capabilities,
    llm_client=llm_client,
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

`max_workers` 是 `workers` 的等价显式名称，并在两者同时出现时优先。上述配置会写入产物，也实际驱动关系候选生成和 Matcher 构建行为。

调用方还可以从 `openjiuwen.symphony` 导入并显式注入 `OntologyMatcher`、`CachedOntologyMatcher` 或 `OpenAICompatibleOntologyMatcher`。

### Fast、Beam 与运行时过滤

`OrchestrationConfig(mode="fast")` 使用一次性 LLM 规划；`mode="beam"` 使用双向 beam search。两种模式读取同一版本化能力图，并支持：

- 通过 `candidate_ids` 限定候选能力；
- 通过 `disabled_capability_ids` 过滤禁用能力；
- 选择中英文摘要；
- 分析缺失输入并生成稳定的执行图；
- 通过进度回调报告构建、Matcher 和规划阶段事件。

动态 overlay 默认关闭。只有 `OrchestrationConfig(dynamic_graph_enabled=True)` 时，传给 `plan(dynamic_overlay=...)` 的运行时边权覆盖才会参与 Fast 规划；overlay 不改写离线图产物。

## 后续目标接口（当前不可运行）

以下接口展示 retrieval、experience 和 Agent Toolkit 迁入后的目标形态。当前版本尚不存在
`runtime.retrieval`、`runtime.experience` 或 `runtime.agent_toolkit(...)`，调用方不应在现阶段依赖它们。

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
    retrieval_settings=adapter.retrieval_settings(),
    orchestration_settings=adapter.orchestration_settings(),
    llm_config=adapter.default_llm_config(),
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

最终经验模块从会话轨迹中提取能力使用模式，构建可检索的经验知识库，为能力分发提供历史依据：

```python
# 最终目标示例；当前不可运行。
records = await symphony.experience.evaluate(traces)
await symphony.experience.build(records)
experience_result = await symphony.experience.search(
    "读取 PDF 内容并生成摘要",
)
candidate_ids = experience_result.candidate_ids
```

目标领域对象包括轨迹记录、轨迹评估器、经验库、经验库构建器和经验检索器；具体接口将在模块迁入时以实现和测试为准。

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

## 产物布局

指纹、检索和编排使用调用方指定的独立产物目录：

```text
fingerprint_artifact_root/
├── fingerprint.json
├── .fingerprint-cache.json
└── .fingerprint.lock

tree_artifact_root/
├── current.json
└── versions/<version>/tree.json

graph_artifact_root/
├── current.json
├── versions/<version>/graph.json
└── .build_runs/
```

- `fingerprint.json` 当前保存能力指纹、质量评估、来源快照和 schema 版本。
- `tree.json` 最终保存能力树索引、能力资产清单快照和版本信息。
- `graph.json` 当前已保存能力节点、关系边、在线计划 lookup 和版本信息。
- 机器读写产物使用 JSON；YAML 用于配置、prompt 或人工维护的说明文件。

## 开发与验证

Symphony 使用 agent-core 的统一开发环境和质量检查入口：

```bash
uv sync
make test TESTFLAGS="tests/unit_tests/symphony"
make check
make type-check
```

- `pyproject.toml` 是 Python、依赖和工具配置的唯一事实来源。
- `Makefile` 定义常用测试和检查入口。
- Python 模块、README 和 YAML vocabulary 均随 `openjiuwen` wheel 打包；不依赖 `jiuwenswarm`，也没有独立的 Symphony wheel。

## 模块约定

- 公开导入路径以 `openjiuwen.symphony` 开头。
- 公共领域模型和示例统一使用 `capability_id`、`capability_type` 和 `candidate_ids`。
- 运行时资源随 `openjiuwen.symphony` 一同打包。
- Symphony 保持使用方无关，不包含使用方专属的 gateway、Web/TUI、卡片类型或 prompt rail。
- 使用方负责将 Symphony 服务或最终 Toolkit 接入自己的调用体系，并将内部标识映射为公共能力标识。
- 当前 `SymphonyRuntime` 只组合 orchestration；指纹与评估通过独立服务组合，避免宣称尚未接通的 Runtime API。
