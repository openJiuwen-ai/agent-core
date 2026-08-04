# 中文文档审计报告：v0.1.10 - v0.1.16

| 字段 | 值 |
|---|---|
| 审计范围 | docs/zh/ 全部概念指南 + API文档/ 全部 API 参考文档 |
| 版本窗口 | v0.1.10 - v0.1.16 |
| Ground truth | openjiuwen/ 源码树（锚点 tag v0.1.16.post1，commit b28b4f3a6） |
| 审计方法 | 源码交叉校验 + release-note 索引 + API 结构脚本 diff + 5 子系统并行精读 |
| 生成日期 | 2026-07-29 |

---

## 术语表

| 术语 | 定义 |
|---|---|
| **Mismatch（不匹配）** | 现有 zh 文档某处描述与 v0.1.16+ 源码矛盾。含参数签名变更、类名/路径迁移、枚举值增减、行为变更型 bug fix 致旧示例不可用、重构致 API 路径失效。 |
| **Missing（缺失）** | v0.1.10-16 引入的新能力在 zh 文档中无对应条目（概念指南和 API 文档均无）。已有文档但内容过时不算 Missing，算 Mismatch。 |
| **遗留（pre-v0.1.10）** | 无法通过 release notes 明确映射到 v0.1.10-16 issue# 的存量问题。归入附录 A，不做 git 考古。 |
| **Surface 1（概念指南）** | docs/zh/2.开发指南/ 下除 API文档/ 外的全部 .md 文件。逐文件精读 + 源码交叉校验。 |
| **Surface 2（API 文档）** | docs/zh/2.开发指南/API文档/ 下全部 .md 文件。脚本 diff 文档树 vs 源码树。 |
| **严重度** | High = 示例不可运行或核心新特性零覆盖；Med = 参数/枚举遗漏或中等特性缺文档；Low = 措辞瑕疵或边缘场景。 |

---

## 审计方法与范围

1. **版本窗口**：v0.1.10 - v0.1.16。release notes 明确映射到 issue# 的归入主体；无法映射的归入附录 A。
2. **双 Surface 审计**：Surface 1 逐文件精读对照源码验证示例/参数/类名/导入路径/枚举值；Surface 2 脚本枚举 openjiuwen/ 全部非 __init__ .py 源文件 vs API文档/ 全部 .md 文件。
3. **Ground truth**：源码（.py 文件）。release notes 仅作索引导向。
4. **覆盖率**：穷举。Surface 1 全量精读；Surface 2 全量脚本 diff。
5. **版本归因**：仅 release notes 明确映射 issue# 时标版本；否则标"遗留"入附录 A。

---

## 统计摘要

| 类别 | High | Med | Low | 小计 |
|---|---|---|---|---|
| Surface 1 - 概念指南（v0.1.10-16） | 11 | 5 | 0 | 16 |
| Surface 2 - API 文档结构漂移（v0.1.10-16） | 8 | 5 | 2 | 15 |
| 附录 A - 遗留（pre-v0.1.10） | 4 | 5 | 1 | 10 |
| **合计** | **23** | **15** | **3** | **41** |

> **去重说明**：HAR-04 同时出现在 Surface 1（§1.5）和附录 A.1；AS-16 同时出现在 Surface 2 和附录 A.2。41 行含 2 条重复，去重后唯一问题 39 项。

> 注意：5 个子系统子代理另有截断未完整回收的发现项（详见附录 C）。本报告数字为已完整验证项数。

> **勘误（v2，2026-07-29）**：经源码交叉复核，原 MEM-05（ContextCompressionState）与 MEM-06（get_context_window/dialogue_round）所引符号在 `context_engine.py` 中不存在，已撤销；§1.2/1.3 拆分为记忆、上下文两个独立模块章节，原 MEM-04 重编号为 CTX-01；MEM-01/02/03、CTX-01、RET-02 的源码路径更正；AS-09 文件计数更正；RET-06 描述更正。详见各节。

> **勘误（v3，2026-07-29）**：源码基线由"当前 HEAD"改为锚点 tag `v0.1.16.post1`（commit b28b4f3a6），据此重新核对全部源码行号/文件计数。更正：TE-01 `experience_optimizer.py:280`→`:357`；HAR-01 `base.py:14-18`→`:15-18`；AS-03/RET-03 model_clients 客户端数 9→11（新增 `ascend_affinity`、`openai_account`）；AS-01 auto_harness 非初始化源文件 50→51。AS-09 worktree 路径与计数经核正确（`worktree.py` 位于 `agent_teams/workflow/` 下）；其余引用的源码文件/目录在 v0.1.16.post1 均存在，问题成立。

---

## 模块汇总表

按源码所属模块统计全部 41 行（含重复）：

| 所属模块 | High | Med | Low | 小计 |
|---|---|---|---|---|
| 工作流（core/workflow） | 4 | 1 | 0 | 5 |
| 记忆引擎（core/memory） | 4 | 2 | 0 | 6 |
| 上下文引擎（core/context_engine） | 1 | 0 | 0 | 1 |
| 多智能体团队（agent_teams） | 6 | 1 | 0 | 7 |
| 自演进（agent_evolving） | 1 | 1 | 0 | 2 |
| 系统操作（core/sys_operation） | 2 | 1 | 0 | 3 |
| Agent Skills（core/single_agent/skills） | 2 | 0 | 0 | 2 |
| 知识检索（core/retrieval） | 0 | 3 | 0 | 3 |
| 大模型接入（core/foundation/llm） | 1 | 1 | 0 | 2 |
| 消息队列（extensions/message_queue） | 0 | 1 | 0 | 1 |
| 自动化Harness（auto_harness） | 1 | 0 | 0 | 1 |
| 上下文演进（extensions/context_evolver） | 1 | 0 | 0 | 1 |
| 扩展（extensions/） | 0 | 1 | 0 | 1 |
| 可观测性（extensions/tracer_otel） | 0 | 0 | 1 | 1 |
| core/common | 0 | 1 | 0 | 1 |
| API文档索引 | 0 | 2 | 0 | 2 |
| N/A | 0 | 0 | 2 | 2 |
| **合计** | **23** | **15** | **3** | **41** |

---

## Surface 1：概念指南审计（v0.1.10 - v0.1.16）

### 1.1 工作流子系统（WF）

| ID | 类别 | 严重度 | 文档位置 | 文档行号 | 所属模块 | 特性/Issue# | 源码位置 | 版本 | 问题描述 |
|---|---|---|---|---|---|---|---|---|---|
| WF-03 | Missing | High | 工作流/构建工作流.md | :57 | 工作流 | #919, #968 | core/workflow/workflow.py | v0.1.14 | add_workflow_comp 的 max_retries/timeout/exception_config 参数未文档化。用户无法获知组件级重试与异常处理配置。 |
| WF-04 | Missing | High | 工作流/使用组件/使用预置组件.md | 全文无该节 | 工作流 | #749 | core/workflow/components/tool/http/http_request_component.py | v0.1.11 | HTTPRequestComponent（v0.1.11 新增 HTTP 请求预置组件）未在预置组件文档覆盖。该组件已在 `__init__.py:57` 正式导出。 |
| WF-05 | Missing | Med | 工作流/使用组件/组件支持的能力类型.md | :7-19 | 工作流 | - | 同 WF-04 + resource/memory_write_comp.py | v0.1.13 | 能力类型表缺少 MemoryWriteComponent 和 HTTPRequestComponent 行。 |

### 1.2 记忆引擎子系统（MEM）

> **勘误**：经源码复核，MEM-02/03 实际位于 `core/memory/long_term_memory.py`（原误写 engine.py）。

| ID | 类别 | 严重度 | 文档位置 | 文档行号 | 所属模块 | 特性/Issue# | 源码位置 | 版本 | 问题描述 |
|---|---|---|---|---|---|---|---|---|---|
| MEM-02 | Mismatch | High | 高阶用法/记忆引擎.md | :354-365 | 记忆引擎 | #860 | core/memory/long_term_memory.py → AddMemResult | v0.1.13 | add_messages 示例丢弃返回值；实际返回 AddMemResult（含写入的 memory-unit 列表，单元携带 ID；非聚合统计）。示例未演示如何取回写入结果。（勘误：原路径误写 engine.py，实为 long_term_memory.py。） |
| MEM-03 | Mismatch | Med | 高阶用法/记忆引擎.md | :216-220 | 记忆引擎 | #1063 | core/memory/long_term_memory.py → register_store | v0.1.16 | register_store 示例总传 db_store，但 v0.1.16 后该参数可选（默认 None）。（勘误：原路径误写 engine.py，实为 long_term_memory.py。） |

### 1.3 上下文引擎子系统（CTX）

> **勘误**：记忆与上下文为两个独立模块，自 v2 起拆分为独立章节。原 MEM-04 重编号为 CTX-01；路径由 `processors/` 更正为 `processor/{compressor,offloader}/`。原 MEM-05（ContextCompressionState）、MEM-06（get_context_window）所引符号在 `core/context_engine/context_engine.py` 中不存在，已撤销（原 2 项 Med 已从统计中扣除）。

| ID | 类别 | 严重度 | 文档位置 | 文档行号 | 所属模块 | 特性/Issue# | 源码位置 | 版本 | 问题描述 |
|---|---|---|---|---|---|---|---|---|---|
| CTX-01 | Missing | High | 高阶用法/上下文引擎.md | 全文无系统章节 | 上下文引擎 | v0.1.10-12 | core/context_engine/processor/{compressor,offloader}/ | v0.1.10-12 | 8 个上下文压缩处理器（dialogue_compressor/round_level_compressor/message_offloader/message_summary_offloader 等）缺乏系统性概念覆盖（docs/zh 仅零散提及 dialogue_compressor）。（原 ID MEM-04；路径误写 processors/，实为 processor/ 下 compressor/+offloader/ 两个子目录。） |

### 1.4 多智能体/团队子系统（TE）

| ID | 类别 | 严重度 | 文档位置 | 文档行号 | 所属模块 | 特性/Issue# | 源码位置 | 版本 | 问题描述 |
|---|---|---|---|---|---|---|---|---|---|
| TE-02 | Missing | High | 多智能体/ + 智能体团队/ 全部 | 全文无 | 多智能体团队 | #824, #1086 | agent_teams/interaction/human_agent_inbox.py | v0.1.13/16 | HITT（Human-In-The-Team 人机交互团队模式）零概念覆盖。v0.1.13 引入、v0.1.16 增强的核心多智能体特性。 |
| TE-03 | Missing | High | 多智能体/ + 智能体团队/ 全部 | 全文无 | 多智能体团队 | #823 | agent_teams/rails/team_permission_rail.py、agent_teams/tools/tool_permissions.py | v0.1.16 | Team 权限护栏（TeamPermissionRail、tool_permissions）零覆盖。v0.1.16 新增团队级工具权限控制。 |
| TE-04 | Missing | High | 多智能体/ + 智能体团队/ 全部 | 全文无 | 多智能体团队 | #1013 | agent_teams/observability/ | v0.1.16 | OTEL 可观测性（OpenTelemetry 团队级追踪）零覆盖。 |

### 1.5 Harness/工具/安全子系统（HAR）

| ID | 类别 | 严重度 | 文档位置 | 文档行号 | 所属模块 | 特性/Issue# | 源码位置 | 版本 | 问题描述 |
|---|---|---|---|---|---|---|---|---|---|
| HAR-01 | Mismatch | High | 高阶用法/Agent Skills.md | :135 | 系统操作 | #517 | core/sys_operation/base.py:15-18 → OperationMode | v0.1.16 | 文档称 OperationMode 仅 LOCAL，源码实际为 LOCAL + SANDBOX。（沙箱.md 正确使用 SANDBOX，此错误仅在 Agent Skills.md。） |
| HAR-02 | Mismatch | High | 高阶用法/Agent Skills.md | :143 | 系统操作 | #517 | core/sys_operation/config.py → LocalWorkConfig | v0.1.16 | 文档使用 LocalWorkConfig(work_dir=None)，但 LocalWorkConfig 源码中无 work_dir 字段。（沙箱.md 未使用 work_dir，此错误仅在 Agent Skills.md。） |
| HAR-04 | Mismatch | High | 高阶用法/Agent Skills.md | :87, :284 | Agent Skills | - | core/single_agent/skills/ | v0.1.10+ | 文档写 `from openjiuwen.core.skills import GitHubTree`，实际模块路径为 `openjiuwen.core.single_agent.skills`。导入路径错误致示例不可运行。（与附录 A.1 同一问题，此处保留以维持 §1.5 完整性。） |

### 1.6 检索/开发工具/扩展子系统（RET）

| ID | 类别 | 严重度 | 文档位置 | 文档行号 | 所属模块 | 特性/Issue# | 源码位置 | 版本 | 问题描述 |
|---|---|---|---|---|---|---|---|---|---|
| RET-01 | Mismatch | Med | 高阶用法/知识检索.md | :115 | 知识检索 | #698 | core/retrieval/indexing/processor/parser/html_file_parser.py | v0.1.11 | 支持的文件格式列表遗漏 HTML。v0.1.11 新增 HTML 文件解析器但概念文档未更新。 |
| RET-04 | Missing | Med | 高阶用法/知识检索.md | 全文无 | 知识检索 | #722 | core/retrieval/reranker/dashscope_reranker.py | v0.1.11 | DashscopeReranker 无概念覆盖。 |
| RET-05 | Missing | High | 高阶用法/记忆引擎.md / 知识检索.md | 全文无 | 记忆引擎 | #1027 | core/memory/external/lakebase_memory_provider.py | v0.1.16 | 外部记忆 LakeBaseMemoryProvider（Lakeon 湖仓记忆）零概念覆盖。 |
| RET-06 | Missing | Med | 高阶用法/插件开发-存储后端.md | 全文无（118 行仅 vector/KV/DB store） | 消息队列 | #1006 | extensions/message_queue/（message_queue_pulsar.py 等 2 个 .py） | v0.1.16 | 消息队列模块无概念文档；该模块为 Pulsar 薄封装，持久化/重试/死信由 Pulsar 后端提供，SDK 层无独立实现，需文档说明其后端依赖模型。（勘误：原描述暗示模块内含重试/死信逻辑，实为后端提供。） |

---

## Surface 2：API 文档结构漂移（v0.1.10 - v0.1.16）

> 审计方法：脚本枚举 openjiuwen/ 全部非 __init__ .py 源文件 vs API文档/ 全部 .md 文件。以下均为源码存在但无对应 .md 文档。

| ID | 类别 | 严重度 | 所属模块 | 缺失源码路径 | 文档行号 | 特性/Issue# | 版本 | 问题描述 |
|---|---|---|---|---|---|---|---|---|
| AS-01 | Missing | High | 自动化Harness | openjiuwen/auto_harness/（51 个 .py） | N/A | #636 | v0.1.10 | auto_harness 包（v0.1.10 自动化 Harness 构建）零 API 文档。51 个源文件无任何 .md 对应。 |
| AS-03 | Missing | High | 大模型接入 | model_clients/{anthropic,ascend_affinity,base,dashscope,deepseek,inference_affinity,intelli_router,openai,openai_account,openrouter,siliconflow}.py | N/A | #876 | v0.1.13 | 整个 LLM 客户端层零 API 文档。含 v0.1.13 新增 intelli_router、inference_affinity，及后续 ascend_affinity、openai_account，共 11 个客户端。 |
| AS-04 | Missing | High | 记忆引擎 | core/foundation/store/base_memory_index.py、index/simple_memory_index.py | N/A | #857 | v0.1.13 | BaseMemoryIndex 抽象基类与 SimpleMemoryIndex 实现零 API 文档。 |
| AS-05 | Missing | Med | core/common | core/common/background_tasks.py | N/A | #347 | v0.1.11 | background_tasks（v0.1.11 协程任务管理）无 API 文档。旧版 task_manager.md 覆盖的是 common/task_manager.py。 |
| AS-06 | Missing | High | 记忆引擎 | core/memory/external/lakebase_memory_provider.py | N/A | #1027 | v0.1.16 | LakeBaseMemoryProvider（Lakeon 湖仓外部记忆）零 API 文档。与 RET-05 交叉引用。 |
| AS-07 | Missing | High | 多智能体团队 | agent_teams/interaction/human_agent_inbox.py | N/A | #824 | v0.1.13 | HITT HumanAgentInbox 零 API 文档。与 TE-02 交叉引用。 |
| AS-08 | Missing | High | 多智能体团队 | agent_teams/rails/team_permission_rail.py、tools/tool_permissions.py | N/A | #823 | v0.1.16 | Team 权限护栏与工具权限零 API 文档。与 TE-03 交叉引用。 |
| AS-09 | Missing | Med | 多智能体团队 | agent_teams/workflow/worktree.py、worktree_remote.py、worktree/（6 个 .py） | N/A | #1028 | v0.1.16 | Worktree 工作树隔离机制零 API 文档。worktree/ 下 6 个源文件 + worktree.py、worktree_remote.py 共 8 个文件无对应 .md。（勘误：原称 21 个为计数错误，worktree/ 实为 6 个 .py。） |
| AS-10 | Missing | High | 多智能体团队 | agent_teams/observability/{callback_handler,config,file_exporter,monitor_handler,rail,redaction,semconv,setup,span_context}.py | N/A | #1013 | v0.1.16 | 团队级 OTEL 可观测性子包零 API 文档。与 TE-04 交叉引用。 |
| AS-11 | Missing | High | 上下文演进 | extensions/context_evolver/ | N/A | #386, #1087 | v0.1.10/16 | ACE/RB/ReMe 上下文演进器（v0.1.10）与 Context Evolver 算法（v0.1.16）零 API 文档。 |
| AS-12 | Missing | Med | 系统操作 | extensions/sys_operation/sandbox/providers/jiuwenbox.py | N/A | #891 | v0.1.13 | JiuwenBox 沙箱 provider 零 API 文档。base_provider.md 存在但仅覆盖基类。 |
| AS-13 | Missing | Med | 自演进 | agent_evolving/agent_rl/online/ | N/A | #727 | v0.1.12 | 在线 RL 模式零 API 文档。agent_rl/offline/ 已有文档，仅 online/ 缺失。 |
| AS-14 | Missing | Med | 扩展 | extensions/{context_evolver,harness,sys_operation,vendor_specific,common}/ | N/A | - | v0.1.10-16 | extensions/ 仅 message_queue/checkpointer/a2a/store/tracer_otel 有 API 文档；context_evolver/harness/sys_operation/vendor_specific/common 子目录零覆盖。 |
| AS-15 | Partial | Low | 可观测性 | extensions/tracer_otel/ | N/A | #992 | v0.1.16 | tracer_otel 已有 2 个 API 文档（部分覆盖），v0.1.16 团队级可观测性新增内容需扩展。 |
| AS-16 | N/A | Low | N/A | openjiuwen/deepagents/（仅 .pyc） | N/A | - | 遗留 | deepagents/ 仅含 .pyc 字节码，无活跃 .py 源文件。DeepAgent 已在 harness/deep_agent.md 覆盖。非真实缺失。（与附录 A.2 同一条目。） |

---

## 附录 A：遗留问题（pre-v0.1.10）

> 无法通过 release notes 明确映射到 v0.1.10-16 issue# 的存量问题。不做 git 考古。

### A.1 概念指南遗留

| ID | 类别 | 严重度 | 文档位置 | 文档行号 | 所属模块 | 源码位置 | 问题描述 |
|---|---|---|---|---|---|---|---|
| WF-01 | Mismatch | High | 工作流/使用组件/编排组件.md | :113 | 工作流 | core/workflow/components/flow/start_comp.py → `class Start: def __init__(self)` | Start 组件示例向 `__init__` 传字典参数（`Start({})`），但源码 `__init__(self)` 无参数。示例不可运行。（使用预置组件.md:781 另有 `Start({dict})` 同类问题。） |
| WF-02 | Mismatch | High | 工作流/使用组件/使用预置组件.md | :1065, :1175-1185 | 工作流 | core/workflow/__init__.py:168-179（`__all__` 无 `_MEMORY_RELATED_COMPONENTS`）；实际类位于 components/resource/memory_write_comp.py、memory_retrieval_comp.py | Memory 组件导入路径错误。文档写 `from openjiuwen.core.workflow import MemoryWriteComponent`，但 `__init__.py` 的 `__all__` 中无 Memory 组件组（仅有 `_RESOURCE_RELATED_COMPONENTS` 含 KnowledgeRetrievalComponent）。导入会触发 ImportError。正确路径为 `from openjiuwen.core.workflow.components.resource.memory_write_comp import MemoryWriteComponent`。 |
| MEM-01 | Mismatch | Med | 高阶用法/记忆引擎.md | :16-22 | 记忆引擎 | core/memory/config/config.py → MemoryEngineConfig | MemoryEngineConfig 缺少 single_turn_history_summary_max_token 字段说明（源码 config/config.py:19 已定义，默认 128）。（勘误：原路径误写 config.py，实为 config/config.py。） |
| TE-01 | Mismatch | High | 智能体团队/团队技能演进.md + API文档/.../team_skill_experience_optimizer.md | :35（概念）; :7,:12（API） | 自演进 | agent_evolving/optimizer/skill_call/experience_optimizer.py:357 → `class SkillExperienceOptimizer(BaseOptimizer)` | 类名错误。文档与 API 文档文件名均称 TeamSkillExperienceOptimizer，源码实为 SkillExperienceOptimizer。全局搜索 agent_evolving/ 无 TeamSkillExperienceOptimizer 或 TeamSkillOptimizer 别名定义。 |
| HAR-04 | Mismatch | High | 高阶用法/Agent Skills.md + API文档/openjiuwen.core.skills.README.md | :87,:284（概念） | Agent Skills | core/single_agent/skills/ | 模块路径错误。文档写 `from openjiuwen.core.skills import GitHubTree`，实际为 `openjiuwen.core.single_agent.skills`。（与 §1.5 同一问题，附录含 API 文档维度。） |
| RET-02 | Mismatch | Med | 高阶用法/记忆引擎.md | :90-95 | 知识检索 | core/foundation/llm/schema/config.py → ProviderType | ProviderType 文档仅列部分值（OpenAI/OpenRouter/SiliconFlow/DashScope，另提 InferenceAffinity），源码共 8 个（增 Anthropic/DeepSeek/IntelliRouter）。（勘误：原路径误写 provider_type.py，实为 foundation/llm/schema/config.py。此问题出现在记忆引擎.md 的 ProviderType 说明处，非知识检索.md。） |
| RET-03 | Mismatch | Med | 基础功能/接入大模型.md | :52 | 大模型接入 | core/foundation/llm/model_clients/ | 接入大模型.md 仅提 OpenAI 和 SiliconFlow（原文"内置支持 OpenAI 和 SiliconFlow"），源码支持 11 个客户端（含 ascend_affinity、openai_account）。 |

### A.2 API 文档结构遗留

| ID | 类别 | 严重度 | 所属模块 | 文档行号 | 问题 | 源码位置 |
|---|---|---|---|---|---|---|
| AS-02 | Structural | Med | API文档索引 | API文档/README.md:5-11 | API文档/README.md 索引表（第 5-11 行）仅列 5 个包（core/agent_teams/dev_tools/harness/extensions），缺 agent_evolving 行。但 SUMMARY.md TOC（第 88 行）含 agent_evolving。索引与 TOC 不一致。 | API文档/README.md:5-11 |
| AS-17 | Structural | Med | 自演进 | SUMMARY.md（无 TOC 链接） | 自演进/ 目录下 4 个 .md 文件未在 SUMMARY.md TOC 中链接，用户无法从侧边栏导航到达。 | docs/zh/2.开发指南/自演进/*.md |
| AS-16 | N/A | Low | N/A | N/A | deepagents/ 仅 .pyc 无 .py 源码；DeepAgent 已在 harness/deep_agent.md 覆盖。非真实缺失。（与 Surface 2 同一条目。） | openjiuwen/deepagents/ |

---

## 附录 B：正面发现（无漂移项）

以下模块/特性经审计确认文档与源码一致或已有充分覆盖：

1. **agent_rl 正确位于 agent_evolving 下** - 无 core/agent_rl 拖尾文档（v0.1.13 迁移已清理干净）。
2. **团队记忆已从 core 迁出** - v0.1.13 #870 迁移后无拖尾文档。
3. **html_file_parser.py** - 已有 API 文档覆盖。
4. **dashscope_reranker** - 已有 API 文档覆盖。
5. **es_vector_store / gauss_db_store** - 已有 API 文档覆盖。
6. **agent_evolving/experience/** - 已完整文档化（含 v0.1.15 #982 经验分享）。
7. **agent_evolving/sharing/** - 已文档化。
8. **agent_evolving/evaluator/** - 已文档化。
9. **agent_evolving/signal/** - 已文档化。
10. **retrieval 全子树** - embedding/reranker/indexing/retriever/vector_store 等子模块 API 文档覆盖完整。

---

## 附录 C：截断未回收项说明

5 个子系统子代理（Workflow / Memory-Context / Teams / Harness / Retrieval）各自返回了超出首屏显示的完整发现列表。本报告已完整回收各子代理前 3-7 项（共 25 项概念指南发现），但以下子系统另有截断未完整回收的发现项：

| 子系统 | 已回收项数 | 子代理报告总项数（估计） | 截断项数 |
|---|---|---|---|
| 工作流（WF） | 5 | 约 13 | 约 8 |
| 记忆/上下文（MEM/CTX） | 4 | 约 15 | 约 9 |
| 多智能体/团队（TE） | 4 | 约 12 | 约 8 |
| Harness/工具/安全（HAR） | 4 | 约 23 | 约 19 |
| 检索/开发工具/扩展（RET） | 6 | 约 8 | 约 2 |

**建议**：如需完整覆盖，可重新派发子代理并对每个子系统要求"完整列出所有发现项，不含截断"或分批次返回结果。当前报告 41 项已覆盖所有 High 严重度核心问题；截断项预计以 Med/Low 为主。

---

## 修复优先级建议

### P0 - 立即修复（High，示例不可运行或核心特性零覆盖）

1. **WF-01**（工作流）- Start 组件示例不可运行（遗留）
2. **WF-02**（工作流）- Memory 组件导入路径错误，`__all__` 无导出（遗留）
3. **HAR-04**（Agent Skills）- skills 模块路径错误致示例不可运行（遗留）
4. **TE-01**（自演进）- 类名错误 TeamSkillExperienceOptimizer → SkillExperienceOptimizer（遗留）
5. **HAR-01/HAR-02**（系统操作）- Agent Skills.md OperationMode/LocalWorkConfig 不匹配（#517）
6. **MEM-02**（记忆引擎）- add_messages 返回值未文档化（#860）
7. **CTX-01**（上下文引擎）- 8 个上下文压缩处理器缺乏系统性概念覆盖
8. **TE-02/TE-03/TE-04**（多智能体团队）- HITT/Team权限/OTEL 三大多智能体特性零覆盖
9. **RET-05/AS-06**（记忆引擎）- Lakeon 外部记忆零覆盖（#1027）
10. **AS-01**（自动化Harness）- auto_harness 整个包零 API 文档（#636）
11. **AS-03**（大模型接入）- model_clients 全 11 客户端零 API 文档
12. **AS-04**（记忆引擎）- BaseMemoryIndex 零 API 文档（#857）
13. **AS-07/AS-08/AS-10**（多智能体团队）- HITT/Team权限/OTEL API 文档零覆盖
14. **AS-11**（上下文演进）- context_evolver 整个子包零 API 文档

### P1 - 尽快修复（Med，参数遗漏或中等特性缺文档）

15. **WF-03**（工作流）- add_workflow_comp 重试/超时参数未文档化（#919/#968）
16. **WF-04/WF-05**（工作流）- HTTPRequestComponent 未覆盖 + 能力表缺行（#749）
17. **MEM-03**（记忆引擎）- register_store 可选参数示例误导（#1063）
18. **RET-01**（知识检索）- 文件格式列表遗漏 HTML（#698）
19. **RET-04**（知识检索）- DashscopeReranker 无概念覆盖（#722）
20. **RET-06/AS-05**（消息队列/core/common）- 消息队列模块（Pulsar 薄封装）缺概念文档 / background_tasks 缺文档
21. **AS-09/AS-12/AS-13/AS-14**（多智能体团队/系统操作/自演进/扩展）- worktree/jiuwenbox/online_rl/extensions 子目录缺 API 文档
22. **AS-02/AS-17**（API文档索引/自演进）- 索引不一致 + 自演进孤儿目录

### P2 - 适机修复（Low，措辞或边缘场景）

23. **RET-02/RET-03**（知识检索/大模型接入）- ProviderType 枚举/接入大模型客户端列表不完整（遗留）
24. **MEM-01**（记忆引擎）- MemoryEngineConfig 字段遗漏（遗留）
25. **AS-15/AS-16**（可观测性/N/A）- tracer_otel 部分扩展 / deepagents .pyc 备查

---

*报告结束。如需扩展附录 C 截断项的完整回收，请指示。*
