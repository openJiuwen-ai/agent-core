# openjiuwen.symphony

`openjiuwen.symphony` 是 openJiuwen 的智能体能力资产发现、检索、编排、经验沉淀和评估模块，用于管理 Agent 运行时可用的 subagent、skill 等能力资产。

Symphony 的最终设计围绕 Agent 能力资产提供以下核心能力：

- **能力指纹**：从能力资产中提取语义画像、输入输出语义和能力标签，形成供检索、编排和评估共享的标准化能力指纹。
- **能力检索**：将能力资产组织成可逐层浏览和检索的树结构，帮助 Agent 找到候选能力。
- **能力编排**：将能力视图组织成关系图，并基于候选能力生成可解释的执行路线。
- **经验沉淀**：从会话轨迹中提取能力使用模式，构建可检索的经验知识库。
- **能力评估**：评估智能体能力、技能质量和能力组合效果，为分发、推荐和持续优化提供依据。
- **Agent Toolkit**：向 Agent 提供组合检索和编排的统一调用入口。

## 实施状态

本文档同时描述最终设计和当前实现。除“当前可运行”章节明确列出的接口外，其余代码示例均为最终目标，不代表当前已有可导入的 Python API。

| 能力域 | 当前状态 | 说明 |
| --- | --- | --- |
| 能力指纹 | 编排所需部分已实现 | 已提供能力标识、类型、输入和输出等编排所需模型；完整资产扫描和跨领域画像仍待迁入。 |
| 能力检索 | 待迁入 | 最终提供能力树构建、浏览和自然语言检索。 |
| 能力编排 | 本次已实现 | 已提供关系图构建、版本化产物、Fast/Beam Planner 和执行图。 |
| 经验沉淀 | 待迁入 | 最终提供轨迹评估、经验库构建和经验检索。 |
| 能力评估 | 待迁入 | 最终提供能力、技能质量和能力组合效果评估。 |
| Agent Toolkit | 待迁入 | 最终组合检索和编排，供 Agent 调用。 |
| 公共 `models` | 待迁入 | 待其他能力域迁入后，再统一提取跨领域公共模型；本次不创建该目录。 |

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

Symphony 不直接扫描应用目录、不读取应用配置、不注册或执行使用方的能力，也不依赖具体使用方代码。以 JiuwenSwarm 为例，应用 Adapter 负责把 `skill_id` 转换为公共领域的 `capability_id`，并把应用侧配置、默认模型、禁用项和动态关系映射为 Symphony 输入。

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

- `shared/fingerprint` 抽取能力资产的语义画像、输入输出语义和能力标签。当前只实现编排所需部分。
- `retrieval` 构建并读取能力树索引，向 Agent 渐进披露分支和候选能力。
- `orchestration/graph` 生成关系候选、进行 IO/语义匹配、判断关系并构建能力图。
- `orchestration` 管理图产物，并根据候选能力和任务目标生成执行路线。
- `experience` 评估会话轨迹、沉淀能力使用经验，并支持经验检索。
- `evaluation` 提供能力和技能质量评估。
- `interfaces` 定义使用方需要实现或传入的协议。
- `agent` 提供 Agent-facing toolkit；使用方负责将 toolkit 方法接入自己的调用体系。

能力图是编排领域的内部组成，不是一个独立的 Runtime 服务；公共运行时通过 `SymphonyRuntime.orchestration` 暴露图生命周期和规划能力。

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
│       ├── evaluation                    # 待迁入
│       ├── models                        # 待迁入，本次不创建
│       └── shared
├── tests
│   └── unit_tests
│       └── symphony
├── examples
│   └── symphony
└── docs
```

- Python 源码进入 `openjiuwen/symphony/`。
- 图构建模型和 Matcher 属于编排领域，保留在 `orchestration/graph/`。
- 跨领域公共模型待其他模块迁入后统一提取到 `models/`。
- 单元测试进入 `tests/unit_tests/symphony/`，示例进入 `examples/symphony/`。
- 完整的中英文使用指南进入 agent-core 的 `docs` 文档体系。
- 依赖、构建和发布配置由 agent-core 根目录的 `pyproject.toml` 统一管理。

## 当前可运行接口

当前 `SymphonyRuntime` 只组合 `OrchestrationService`。配置、能力清单、LLM、Matcher 和产物目录都由使用方显式注入。

```python
from openjiuwen.symphony import (
    ArtifactSpec,
    CapabilityFingerprint,
    OrchestrationConfig,
    ParameterSpec,
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
            outputs=[ArtifactSpec(name="text", type="text")],
        ),
        CapabilityFingerprint(
            capability_id="summarize",
            capability_type="subagent",
            name="Summarize",
            description="Summarize text",
            version="1.0.0",
            inputs=[ParameterSpec(name="text", type="text")],
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

## 最终目标接口（当前不可运行）

以下接口展示 Symphony 完整迁入后的目标形态，当前版本尚不存在 `runtime.retrieval`、`runtime.experience`、`runtime.evaluation` 或 `runtime.agent_toolkit(...)`，调用方不应在现阶段依赖它们。

### Adapter 协议

最终设计仍由使用方提供 Adapter，Symphony 不直接耦合使用方的配置系统和资产目录。以下是目标协议的概念示例，具体类型将在对应模块迁入时确定：

```python
# 最终目标示例；当前不可运行。
from pathlib import Path
from typing import Protocol, Sequence

from openjiuwen.symphony import CapabilityFingerprint


class CapabilityInventoryProvider(Protocol):
    def assets_root(self) -> Path: ...

    def list_capabilities(self) -> Sequence[CapabilityFingerprint]: ...


class LLMConfigProvider(Protocol):
    def default_llm_config(self) -> "LLMConfig": ...


class ArtifactPathProvider(Protocol):
    def tree_artifact_root(self) -> Path: ...

    def graph_artifact_root(self) -> Path: ...
```

能力清单使用 `capability_id` 作为统一标识，并通过 `capability_type` 区分不同资产类型。

### 能力指纹

最终能力指纹服务会在当前编排模型基础上补齐资产发现、扫描、语义画像和归一化标签，并将同一份标准化指纹交给检索、编排和评估。使用方仍可以显式提供普通对象或 `CapabilityFingerprint`，而不需要让 Symphony 读取应用内部注册表。

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

### 能力评估

最终评估服务面向单项能力、Agent 能力和能力组合，输出可追踪的指标、结论与改进建议：

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

检索和编排使用调用方指定的独立产物目录：

```text
tree_artifact_root/
├── current.json
└── versions/<version>/tree.json

graph_artifact_root/
├── current.json
├── versions/<version>/graph.json
└── .build_runs/
```

- `tree.json` 最终保存能力树索引、能力资产清单快照和版本信息。
- `graph.json` 当前已保存能力节点、关系边、在线计划 lookup 和版本信息。
- 机器读写产物使用 JSON；YAML 用于配置、prompt 或人工维护的说明文件。

## 开发与验证

Symphony 使用 agent-core 的统一开发环境和质量检查入口：

```powershell
uv sync
make test TESTFLAGS="tests/unit_tests/symphony"
make check
make type-check
```

- `pyproject.toml` 是 Python、依赖和工具配置的唯一事实来源。
- `Makefile` 定义常用测试和检查入口。
- 可选依赖由 agent-core 的依赖体系统一管理。

## 模块约定

- 公开导入路径以 `openjiuwen.symphony` 开头。
- 公共领域模型和示例统一使用 `capability_id`、`capability_type` 和 `candidate_ids`。
- 运行时资源随 `openjiuwen.symphony` 一同打包。
- Symphony 保持使用方无关，不包含使用方专属的 gateway、Web/TUI、卡片类型或 prompt rail。
- 使用方负责将 Symphony 服务或最终 Toolkit 接入自己的调用体系，并将内部标识映射为公共能力标识。
- 初版模块以新的公开路径为准，不提供其他导入路径的兼容层。
