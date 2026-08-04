# 文档修复汇总（v0.1.10–v0.1.16，杨泽渠 29 项）

> 基线：tag `v0.1.16.post1`（commit `b28b4f3a6`），分支 `dev_doc_v0.1.16.post1`
> 审计依据：`docs/dev/zh-doc-audit-v0.1.10-16.md`（v3 校正版）
> 修复范围：审计中责任人 杨泽渠 的 29 项（Batch A/B/C/D）
> 原则：仅改文档，不改代码（假定代码正确）；存疑即停，不臆造。
> **审阅修正（commit `7c2d7b1ac`，新增提交非 amend）**：依据审阅反馈纠正 4 项——WF-04（HTTPRequestComponent 实为导出，修正导入）、AS-02（回退 agent_evolving 行，仅保留 auto_harness）、AS-11（整体回退以避免冲突）、AS-01（加深为按子包的详细 `.md`，进行中）。详见 §七。

## 一、总体统计

| 批次 | 类别 | 应修 | 已修 | 跳过 | 备注 |
|---|---|---|---|---|---|
| A | Mismatch（内容与源码不符） | 11 | 11 | 0 | 全部完成 |
| B | Missing（向已有文档补内容） | 9 | 7 | 2 | AS-02 审阅回退、AS-15 跳过 |
| C | Missing（新建 API 文档） | 8 | 5 | 3 | AS-06 跳过、AS-11 审阅回退、AS-14 部分（计入跳过列） |
| D | N/A（非真实缺失） | 1 | 0 | 1 | AS-16 确认非 gap |
| **合计** | | **29** | **23** | **6** | AS-14 部分应用计入跳过列 |

跳过/回退项均有依据（见 §四、§七）。

## 二、修复明细（按 issue）

### Batch A — Mismatch（11 项，全部完成）

| ID | 严重度 | 文件 | 修改内容 |
|---|---|---|---|
| WF-01 | High | 编排组件.md:113、使用预置组件.md:781 | `Start({})` → `Start()`：源码 `__init__(self)` 无参数，示例不可运行。 |
| WF-02 | High | 使用预置组件.md:1065-1077、:1186-1189 | Memory 组件导入路径修正：`from openjiuwen.core.workflow import MemoryWriteComponent` → `from openjiuwen.core.workflow.components.resource.memory_write_comp import MemoryWriteComponent`（`__init__.__all__` 无 Memory 组件组）。 |
| HAR-01 | High | Agent Skills.md:135 | OperationMode 补全 SANDBOX（源码为 LOCAL + SANDBOX，非仅 LOCAL）。 |
| HAR-02 | High | Agent Skills.md:143 | 移除 `LocalWorkConfig(work_dir=...)` 中不存在的 `work_dir` 字段。 |
| HAR-04 | High | Agent Skills.md:87、:283 | 导入路径修正：`from openjiuwen.core.skills` → `from openjiuwen.core.single_agent.skills`。 |
| MEM-01 | High | 记忆引擎.md:22-23 | 补全 MemoryEngineConfig 遗漏字段。 |
| MEM-02 | High | 记忆引擎.md:354-365 | `add_messages` 示例演示取回返回值 `AddMemResult`（含写入的 memory-unit 列表，非聚合统计）。 |
| MEM-03 | Med | 记忆引擎.md:216-220 | `register_store` 的 `db_store` 参数标注 v0.1.16 后可选（默认 None）。 |
| RET-01 | Med | 知识检索.md:115 | 支持的文件格式列表补 HTML（v0.1.11 新增 html_file_parser）。 |
| RET-02 | Med | 记忆引擎.md:90-95 | ProviderType 枚举补全（与 AS-03 联动，详见 §三）。 |
| RET-03 | Med | 接入大模型.md:52 | 客户端列表补全（原文仅提 OpenAI/SiliconFlow，源码支持 11 个）。 |

### Batch B — Missing（向已有文档补内容，9 项）

| ID | 严重度 | 文件 | 修改内容 | 状态 |
|---|---|---|---|---|
| WF-03 | High | 构建工作流.md:57 | 补 `add_workflow_comp` 的 `max_retries`/`timeout`/`exception_config`（`ExceptionConfig.handle_type`）参数文档。 | ✓ |
| WF-04 | High | 使用预置组件.md | 补 HTTPRequestComponent 节。**审阅修正**：原误判为「未在 `__init__` 导出，需直引模块路径」；实证 `_HTTP_RELATED_COMPONENTS`（`workflow/__init__.py:138-151`）含 HTTPRequestComponent + 11 个 HTTP 符号并合并入 `__all__`，正确导入为 `from openjiuwen.core.workflow import HTTPRequestComponent, HttpComponentConfig`（§七）。 | ✓（已修正） |
| WF-05 | Med | 组件支持的能力类型.md:7-19 | 能力类型表补 `MemoryWriteComponent`、`HTTPRequestComponent` 两行。 | ✓ |
| CTX-01 | High | 上下文引擎.md | 补上下文处理器系统章节：10 个处理器（6 压缩器 + 4 offloader）。 | ✓ |
| RET-04 | Med | 知识检索.md | 补 DashscopeReranker 概念覆盖。 | ✓ |
| RET-05 | High | 记忆引擎.md / 知识检索.md | 补 LakeBaseMemoryProvider（Lakeon 湖仓记忆）概念覆盖。 | ✓ |
| RET-06 | Med | 插件开发-存储后端.md | 补消息队列模块概念文档（Pulsar 后端依赖模型；导入为可选依赖，失败需说明）。 | ✓ |
| AS-02 | Med | API文档/README.md:5-11 | 原索引表补 `agent_evolving` 行。**审阅修正**：回退 `agent_evolving` 行（保留 `auto_harness` 行）；总索引由同事后续统一。 | 回退 |
| AS-15 | Low | extensions/tracer_otel/ | tracer_otel 部分覆盖需扩展。 | 跳过 |

### Batch C — Missing（新建 API 文档，8 项）

| ID | 严重度 | 新建/扩展文件 | 修改内容 | 状态 |
|---|---|---|---|---|
| AS-01 | High | `openjiuwen.auto_harness.README.md`（新建索引）+ `openjiuwen.auto_harness/*.md`（15 个详细子包文档，进行中） | 初版子包索引（13 子目录、约 90 个类/函数一句话说明，AST 抽取）。**审阅加深**：按子包输出详细 `.md`（类签名 / `__init__` 参数 / 方法 / 返回值 / 示例，参照 `llm.md` 深度），写入 `openjiuwen.auto_harness/` 子目录，README 更新为链接索引（§七）。 | 进行中 |
| AS-03 | High | `llm.md`（扩展，+167 行） | 补 6 个缺失 ModelClient（Anthropic/DeepSeek/AscendAffinity/InferenceAffinity/OpenRouter/IntelliRouter）+ `IntelliRouterClientConfig`；更新尾部 ProviderType 说明。 | ✓ |
| AS-04 | High | `foundation/store/memory_index.md`（新建） | BaseMemoryIndex ABC 逐方法 + MemoryDoc + StorageCodec Protocol + SimpleMemoryIndex（含 DEPRECATED 标注）。 | ✓ |
| AS-05 | Med | `common/background_tasks.md`（新建） | 协程任务管理 API 文档（旧 task_manager.md 仅覆盖 task_manager.py）。 | ✓ |
| AS-06 | High | — | LakeBaseMemoryProvider 零 API 文档。 | 跳过 |
| AS-11 | High | `extensions/context_evolver/README.md`（原新建） | 原子包索引：ACE/Cognition/ReasoningBank/ReMe 四算法 retrieve+summary+service+ReAct agent。**审阅修正**：整体回退（删除文件、移除 SUMMARY 与 extensions.README 行），避免冲突（§七）。 | 回退 |
| AS-12 | Med | `extensions/sys_operation/sandbox/providers/jiuwenbox.md` + `sys_operation/README.md`（新建） | 3 provider + 5 helper + mixin 实现细节 + 6-provider 索引表。 | ✓ |
| AS-14 | Med | `extensions/vendor_specific/README.md`（新建） | `AliyunReranker` 弃用别名文档；`harness`/`common` 跳过。 | 部分 |

### Batch D — N/A（1 项）

| ID | 严重度 | 说明 | 状态 |
|---|---|---|---|
| AS-16 | Low | `deepagents/` 仅含 `.pyc` 字节码（52 个），无活跃 `.py` 源文件；DeepAgent 已在 `harness/deep_agent.md` 覆盖。非真实缺失（与附录 A.2 同条）。 | 跳过（已复核确认） |

## 三、AS-03 / RET-02 联动说明

`RET-02`（记忆引擎.md 的 ProviderType 说明）与 `AS-03`（llm.md 客户端层）同源同因，合并处理：

- **ProviderType 枚举**（`foundation/llm/schema/config.py:13-24`，共 10 值）：OpenAI / OpenAIAccount / OpenRouter / Anthropic / SiliconFlow / DashScope / DeepSeek / InferenceAffinity / AscendAffinity / IntelliRouter（`intelli_router`）。
- **llm.md** 补 6 个原缺失客户端 + `IntelliRouterClientConfig`（896 → 1063 行）：Anthropic（进程级 AsyncAnthropic 缓存池）、DeepSeek（`reasoning_content` 重写）、AscendAffinity（KV-cache 亲和）、InferenceAffinity（vLLM `release()`）、OpenRouter（attribution 头保护 + 提示词缓存）、IntelliRouter（ReliableRouter 封装 + 委托 DashScope）。
- **记忆引擎.md** ProviderType 说明处同步补全枚举值。

## 四、跳过项依据

| ID | 跳过原因 |
|---|---|
| AS-06 | 前提错误：`memory/external/lakebase_memory_provider.md`（512 行）已存在，与 RET-05 交叉。非真实缺失。 |
| AS-15 | 用户决定跳过（Low；tracer_otel 团队级可观测性扩展属多智能体团队 TE-04 范畴）。 |
| AS-14（harness 子集） | `extensions/harness/__init__.py` 为 0 字节空命名空间，无任何公共 API。 |
| AS-14（common 子集） | `extensions/common/` 仅含 `__pycache/` 下 `.pyc` 字节码（configs/、log/），无 `.py` 源文件，与 AS-16 同类遗留。 |
| AS-16 | `deepagents/` 仅 `.pyc`，无活跃 `.py`；DeepAgent 已在 `harness/deep_agent.md` 覆盖。审计已标注「非真实缺失」。 |

> AS-14 的 `harness`/`common` 跳过依据已写入 `vendor_specific/README.md` 的「备注」节，保持可追溯。

## 五、文件清单

### 新建文档（6 个，已提交）

| 文件 | 对应 issue |
|---|---|
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness.README.md` | AS-01（索引；详细子包文档见下「进行中」） |
| `docs/zh/2.开发指南/API文档/openjiuwen.core/foundation/store/memory_index.md` | AS-04 |
| `docs/zh/2.开发指南/API文档/openjiuwen.core/common/background_tasks.md` | AS-05 |
| `docs/zh/2.开发指南/API文档/openjiuwen.extensions/sys_operation/README.md` | AS-12 |
| `docs/zh/2.开发指南/API文档/openjiuwen.extensions/sys_operation/sandbox/providers/jiuwenbox.md` | AS-12 |
| `docs/zh/2.开发指南/API文档/openjiuwen.extensions/vendor_specific/README.md` | AS-14 |

> AS-11 原新建的 `extensions/context_evolver/README.md` 已随审阅修正删除，不再列入。

### 进行中（AS-01 加深，待提交）

| 文件 | 内容 |
|---|---|
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/orchestrator.md` | 顶层入口：AutoHarnessOrchestrator / create_auto_harness_orchestrator / load_auto_harness_config 等 |
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/schema.md` | ~30 数据类/枚举 + AutoHarnessConfig（~40 字段） |
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/agents.md` | 11 个 agent 工厂函数 |
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/artifacts.md` | ArtifactStore |
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/contexts.md` | TaskRuntime / BaseExecutionContext / SessionContext / TaskContext / task_key |
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/experience.md` | ExperienceStore / ActiveContextSynthesizer |
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/infra.md` | 17 模块：CI 门控 / git+worktree / 运行时扩展 / 解析选择 / PR 模板 / 编辑范围 |
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/pipelines.md` | BasePipeline / MetaEvolve / ExtendedEvolve / PRTask / ExtensionTask |
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/prompts.md` | build_auto_harness_sections |
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/rails.md` | 7 个护栏 |
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/registry.md` | BaseRegistry / Stage/PipelineRegistry + 4 注册函数 |
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/stages.md` | 11 个 stage 模块（base/assess/plan/implement/verify/commit/publish/learnings/activate/merge/select_pipeline） |
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/tools.md` | ExperienceSearchTool / ExperienceSearchMetadataProvider |
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/resources.md` | resources/ 下 2 个 YAML 配置模板参考 |
| `docs/zh/2.开发指南/API文档/openjiuwen.auto_harness/skills.md` | skills/ 下 10 个 SKILL.md 提示词技能参考 |

### 修改文档（18 个）

| 文件 | 对应 issue |
|---|---|
| `docs/zh/SUMMARY.md` | AS-02、AS-04、AS-05、AS-12、AS-01、AS-14（TOC 条目）；AS-11 行已随回退移除 |
| `docs/zh/2.开发指南/API文档/README.md` | AS-01（auto_harness 行）；AS-02 行已随回退移除 |
| `docs/zh/2.开发指南/API文档/openjiuwen.extensions.README.md` | AS-12、AS-14（MODULE 表行）；AS-11 行已随回退移除 |
| `docs/zh/2.开发指南/API文档/openjiuwen.core/common.README.md` | AS-05（索引项） |
| `docs/zh/2.开发指南/API文档/openjiuwen.core/foundation/store.md` | AS-04（intro bullet） |
| `docs/zh/2.开发指南/API文档/openjiuwen.core/foundation/store.README.md` | AS-04（Classes 表 4 行） |
| `docs/zh/2.开发指南/API文档/openjiuwen.core/foundation/llm/llm.md` | AS-03（+167 行） |
| `docs/zh/2.开发指南/工作流/使用组件/编排组件.md` | WF-01 |
| `docs/zh/2.开发指南/工作流/使用组件/使用预置组件.md` | WF-01、WF-02、WF-04（审阅修正导入） |
| `docs/zh/2.开发指南/工作流/构建工作流.md` | WF-03 |
| `docs/zh/2.开发指南/工作流/使用组件/组件支持的能力类型.md` | WF-05 |
| `docs/zh/2.开发指南/智能体/Agent Skills.md` | HAR-01、HAR-02、HAR-04 |
| `docs/zh/2.开发指南/高阶用法/记忆引擎.md` | MEM-01、MEM-02、MEM-03、RET-02、RET-05 |
| `docs/zh/2.开发指南/高阶用法/知识检索.md` | RET-01、RET-04 |
| `docs/zh/2.开发指南/基础功能/接入大模型.md` | RET-03 |
| `docs/zh/2.开发指南/高阶用法/上下文引擎.md` | CTX-01 |
| `docs/zh/2.开发指南/高阶用法/插件开发-存储后端.md` | RET-06 |

## 六、未尽事项

- **AS-01 加深（进行中）**：6 个并行子代理正基于权威 AST 抽取（`auto_harness_api_rich.txt`，1235 行、65 文件、12 子包）撰写 15 个详细子包 `.md`；完成后更新 README 链接索引与 SUMMARY 子条目并提交。
- **AS-15**（tracer_otel 团队级可观测性扩展）：用户已决定跳过；如后续需补，属多智能体团队 TE-04 范畴。
- **多智能体团队项**（TE-02/03/04、AS-07/08/09/10、AS-13、AS-17）：非杨泽渠职责，不在本次修复范围。
- 本次未改动任何 `.py` 源码；未执行 `make check`/`make type-check`（无 Python 改动，这两者作用于暂存 Python 文件）。

## 七、审阅修正（commit `7c2d7b1ac`）

依据审阅反馈对 4 项做纠正，均以新增提交 `7c2d7b1ac`（非 amend）落地，保留审计轨迹：

| ID | 原处理 | 审阅修正 | 依据 |
|---|---|---|---|
| WF-04 | 原注明「HTTPRequestComponent 未在 `__init__` 导出，需直引模块路径」 | 修正：`_HTTP_RELATED_COMPONENTS`（`workflow/__init__.py:138-151`）含 HTTPRequestComponent + 11 个 HTTP 符号并合并入 `__all__`（line 168+）；正确导入为 `from openjiuwen.core.workflow import HTTPRequestComponent, HttpComponentConfig`。 | 源码 `openjiuwen/core/workflow/__init__.py:56-68, 138-151, 168+` 实证 |
| AS-02 | 原 `API文档/README.md` 补 `agent_evolving` 行 | 回退 `agent_evolving` 行（保留 `auto_harness` 行）；同事后续统一总索引。 | 审阅反馈：总索引由同事统一处理 |
| AS-11 | 原新建 `context_evolver/README.md` + SUMMARY/extensions 行 | 整体回退：删除文件、移除 SUMMARY 与 extensions.README 行，避免冲突。 | 审阅反馈：避免合并冲突 |
| AS-01 | 原仅子包索引（一句话表） | 加深为按子包的详细 `.md`（类签名 / `__init__` 参数 / 方法 / 返回值 / 示例，参照 `llm.md` 深度）；15 个子包文档写入 `openjiuwen.auto_harness/` 子目录，README 更新为链接索引。 | 审阅反馈：用户选择「加深为按子包的详细 .md」 |

> **WF-04 同类错误排查**：WF-02（Memory 组件）经核实确未导出——`_RESOURCE_RELATED_COMPONENTS`（line 132-136）仅含 ComponentKBConfig/KnowledgeRetrievalComponent/config，无 Memory 组件，深路径正确；HAR-04（GitHubTree）位于 `openjiuwen.core.single_agent.skills`（`__init__.py:13,19` 重导出自 `remote_skill_util.py:20`），正确。未发现同类错误。
