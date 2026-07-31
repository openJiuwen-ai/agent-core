# openjiuwen.extensions.context_evolver

Context Evolver 扩展 —— 面向任务记忆（task memory）管理的记忆演化框架。

本子包提供一套可演化的任务记忆系统：从任务轨迹（trajectory）中总结、提炼记忆，并在后续检索时注入 agent 上下文。内置四套记忆算法，每套均提供 retrieve（检索）与 summary（总结/更新）两类操作：

- **ACE**：基于 Playbook（结构化条目）的上下文记忆，通过反思与策展迭代更新。
- **Cognition**：基于动态属性（attribute）分类的认知记忆，支持检索与重排。
- **ReasoningBank**：从推理轨迹中蒸馏策略性记忆，支持 MaTTS（Memory-aware Test-Time Scaling）并行/串行扩展。
- **ReMe**：从成功/失败轨迹对比中抽取反思记忆，支持去重与校验。

公共 API 通过包根 `__init__` 再导出：`TaskMemoryService` / `AddMemoryRequest` / `ContextEvolvingReActAgent` / `MemoryAgentConfigInput` / `summarize_trajectories` / `wikipedia_tool` / `JSONFileConnector` / `MemoryVectorStore` 以及各算法的记忆 schema 类。

> 说明：本页为子包级索引，按源码结构给出各类与函数的一句话说明；完整签名与实现细节请参见 `openjiuwen/extensions/context_evolver/` 下对应源文件。带 `_` 前缀的内部类型不在此列。

## 顶层入口（公共再导出）

| 类 / 函数 | 说明 |
|---|---|
| `TaskMemoryService` | 任务记忆检索与总结服务，封装各算法的 summarize / retrieve。 |
| `AddMemoryRequest` | 添加记忆的请求结构。 |
| `ContextEvolvingReActAgent` | 集成记忆检索能力的 ReActAgent。 |
| `MemoryAgentConfigInput` | 创建记忆 agent 配置的输入参数。 |
| `SummarizeTrajectoriesInput` | `summarize_trajectories` 的参数。 |
| `summarize_trajectories` | 总结轨迹并将结果存入记忆服务。 |
| `wikipedia_tool` | 维基百科检索工具。 |
| `JSONFileConnector` | 通用 JSON 文件读写连接器。 |
| `safe_model_dump` | 将 Pydantic 模型安全序列化为字典。 |
| `MemoryVectorStore` | 基于 numpy 相似度检索的内存向量库（开发/测试用）。 |

## ReAct Agent

定义于 `context_evolving_react_agent.py`。

| 类 | 说明 |
|---|---|
| `ContextEvolvingReActAgent` | 集成记忆检索能力的 ReActAgent。 |
| `MemoryAgentConfigInput` | 创建记忆 agent 配置的输入参数。 |

## core（核心基础设施）

### 配置与上下文

| 类 / 函数 | 说明 |
|---|---|
| `load`（config） | 从 .env 与 YAML 文件加载配置。 |
| `get` / `set_value` / `delete` | 读取 / 设置 / 删除配置值。 |
| `snapshot` / `restore` | 配置快照与恢复（测试用）。 |
| `reload` | 强制从文件重新加载配置。 |
| `RuntimeContext` | 操作执行期间传递的运行时上下文。 |
| `ServiceContext` | 管理 LLM、embeddings、向量库等共享服务的单例上下文。 |

### 操作系统（op）

| 类 | 说明 |
|---|---|
| `BaseOp` | 所有操作的抽象基类。 |
| `ParallelOp` | 操作的并行组合。 |
| `SequentialOp` | 操作的串行组合。 |

### 持久化连接器

| 类 / 函数 | 说明 |
|---|---|
| `MilvusConnector` | 通过 Milvus 保存/加载 `VectorNode` 数据的连接器。 |
| `JSONFileConnector` | 通用 JSON 文件读写连接器。 |
| `safe_model_dump` | 将 Pydantic 模型安全序列化为字典。 |
| `MemoryPersistenceHelper` | 处理 `{node_id: node_dict}` 数据到后端持久化的共享助手。 |
| `MemoryVectorStore` | 基于 numpy 相似度检索的内存向量库。 |

### 核心数据 schema

| 类 | 说明 |
|---|---|
| `Role` | 消息角色枚举。 |
| `Message` | 带 role / content / 可选 metadata 的聊天消息。 |
| `VectorNode` | 用于向量库序列化的节点。 |

## schema（记忆系统 schema）

### io_schema（记忆系统 schema）

定义于 `schema/io_schema.py`，按算法组织请求/响应与记忆模型。

| 类 | 说明 |
|---|---|
| `BaseMemory` | 所有记忆类型的抽象基类。 |
| `ACEMemory` / `ACESummarizeRequest` / `ACESummarizeResponse` / `ACERetrieveRequest` / `ACERetrievedMemory` / `ACERetrieveResponse` | ACE 记忆及 summarize/retrieve 请求响应。 |
| `ReasoningBankMemory` / `ReasoningBankMemoryItem` / `ReasoningBankSummarize*` / `ReasoningBankRetrieve*` | ReasoningBank 记忆及请求响应。 |
| `ReMeMemory` / `ReMeMemoryMetadata` / `ReMeSummarize*` / `ReMeRetrieve*` | ReMe 记忆及请求响应。 |
| `CognitionMemory` / `CognitionSummarize*` / `CognitionRetrieve*` | Cognition 记忆及请求响应。 |
| `OursMemory` / `OursSummarize*` / `OursRetrieve*` | 与 ReMe 同结构的自定义记忆 schema。 |
| `SummarizeResponse` / `RetrieveResponse` | 跨算法通用的 summarize / retrieve 响应。 |

### io_fallback（跨算法解码）

定义于 `schema/io_fallback.py`，处理 VectorNode 与各算法记忆模型间的跨算法字段映射。

| 函数 | 说明 |
|---|---|
| `infer_stored_memory_algorithm` | 依据 node id 前缀推断写入算法。 |
| `deserialization_target_algorithm` | 返回待反序列化记忆模型对应的算法名。 |
| `use_cross_algorithm_fallback` | 是否启用跨算法字段映射。 |
| `reasoning_bank_item_dicts_from_metadata` | 从 VectorNode metadata 构建 ReasoningBank 记忆条目。 |
| `reasoning_bank_query_from_metadata` | ReasoningBank 的 embedding / 主查询串。 |
| `ace_section_and_content_from_metadata` | 将 RB/ReMe/Cognition 源映射为 ACE section + content。 |
| `reme_when_and_content_from_metadata` | 将 RB/ACE/Cognition 源映射为 ReMe when_to_use + content。 |
| `cognition_fields_from_metadata` | 将 RB/ACE/ReMe 源映射为 Cognition 各字段。 |

### memory / trajectory

| 类 / 函数 | 说明 |
|---|---|
| `BaseMemory`（memory） | 所有记忆类型的抽象基类。 |
| `TaskMemory` | 存储 when/how to use 的任务记忆。 |
| `PersonalMemory` | 关于用户偏好与上下文的个人记忆。 |
| `ReasoningBankMemory`（memory） | 蒸馏推理策略的 ReasoningBank 记忆。 |
| `vector_node_to_memory` | 将向量节点转回对应记忆类型。 |
| `FeedbackType` | 轨迹反馈类型枚举。 |
| `Trajectory` | 带反馈的任务执行轨迹。 |
| `TrajectoryBatch` | 用于批量处理的轨迹批次。 |

## service（服务层）

| 类 / 函数 | 说明 |
|---|---|
| `TaskMemoryService` | 任务记忆检索与总结服务。 |
| `AddMemoryRequest` | 添加记忆的请求。 |
| `LLMWrapper` | 兼容 OpenAI/DeepSeek 等 OpenAI 兼容 API 的 LLM 包装器。 |
| `EmbeddingWrapper` | 提供与旧 OpenAIEmbedding 同接口的 embedding 包装器。 |
| `SummarizeTrajectoriesInput` | `summarize_trajectories` 的参数。 |
| `RunTrialsInput` | `run_trials` 的参数。 |
| `TrialOutput` | 单次试验运行结果。 |
| `format_trajectory` | 将消息列表格式化为干净的轨迹字符串。 |
| `summarize_trajectories` | 总结轨迹并存入记忆服务。 |
| `evaluate_trial` | 评估单次试验并返回 `(feedback, score)`。 |
| `run_trials` | 运行 MaTTS 试验并总结结果轨迹。 |

## retrieve（记忆检索操作）

各算法的检索操作均继承 `BaseOp`。

### ACE

| 类 | 说明 |
|---|---|
| `RecallMemoryOp` | 从向量库检索全部 ACE 记忆（playbook 条目）。 |

### Cognition

| 类 | 说明 |
|---|---|
| `LoadSchemaOp` | 从向量库加载动态属性 schema。 |
| `ClassifyQueryOp` | 用 LLM 将用户查询分类到属性。 |
| `RecallCognitionOp` | 依据属性与语义召回相关记忆。 |
| `RerankCognitionOp` | 用 LLM 对召回记忆重排。 |
| `RewriteMemoryOp` | 格式化最终记忆串并构造 `RetrievedMemory`。 |
| `CognitionPrompt` | Cognition 检索 prompt 构造。 |

### ReasoningBank

| 类 | 说明 |
|---|---|
| `RecallMemoryOp` | 从 ReasoningBank 检索相关推理策略。 |
| `ParallelScalingOp` | 并行扩展：生成多条轨迹并用 Best-of-N 选优。 |
| `SequentialScalingOp` | 串行扩展：带自检的迭代精化。 |
| `BestOfNOp` | 用 LLM 评估从 N 个候选中选最优轨迹。 |
| `SelfContrastMemoryOp` | 用自对比推理从多条轨迹中抽取记忆。 |

### ReMe

| 类 / 函数 | 说明 |
|---|---|
| `RecallMemoryOp` | 从 ReMe 检索相关记忆。 |
| `RerankMemoryOp` | 按相关度对记忆重排。 |
| `RewriteMemoryOp` | 用记忆重写。 |
| `ReMePrompt` | ReMe 检索 prompt 构造。 |
| `parse_json_list_response` | 从 LLM 响应解析整数列表。 |
| `parse_json_field` | 从 JSON 响应解析指定字符串字段。 |

## summary（记忆总结/更新操作）

各算法的总结操作均继承 `BaseOp`。

### ACE

| 类 | 说明 |
|---|---|
| `LoadPlaybookOp` | 从向量库加载已有 playbook。 |
| `ReflectOp` | 用 ACE reflector prompt 从单条轨迹生成反思。 |
| `ParallelReflectOp` | 从多条轨迹生成反思（MaTTS 并行模式）。 |
| `CurateOp` | 用 ACE curator prompt 从反思生成 playbook 操作。 |
| `ParallelCurateOp` | 从并行反思生成 playbook 操作（MaTTS 并行模式）。 |
| `ApplyDeltaOp` | 应用 playbook 操作并持久化到向量库。 |
| `PersistMemoryOp` | 将 ACE 记忆从内存向量库持久化到 JSON/Milvus。 |
| `Playbook` | ACE 定义的结构化上下文存储。 |
| `Bullet` / `BulletTag` | 单个 playbook 条目及其标签。 |
| `DeltaOperation` / `DeltaBatch` | 单个 playbook 变更与一批策展操作。 |
| `ACEPrompt` | ACE 总结 prompt 构造。 |

### Cognition

| 类 / 函数 | 说明 |
|---|---|
| `SolutionClassifyOp` | 基于完整轨迹重新分类属性。 |
| `GenerateExperienceOp` | 从轨迹抽取 Description 与 Experience 洞察。 |
| `UpdateVectorStoreOp` | 将生成信息转为 CognitionMemory 节点并 upsert。 |
| `PersistMemoryOp` | 将 Cognition 记忆持久化到磁盘/Milvus。 |
| `CognitionSummaryPrompt` | Cognition 总结 prompt 构造。 |
| `safe_json_loads` | 安全地从 LLM 响应提取并加载 JSON。 |

### ReasoningBank

| 类 / 函数 | 说明 |
|---|---|
| `SummarizeMemoryOp` | 将轨迹总结为 ReasoningBank 记忆。 |
| `SummarizeMemoryParallelOp` | 并行模式下的 ReasoningBank 记忆总结。 |
| `UpdateVectorStoreOp` | 将去重后记忆持久化到向量库。 |
| `PersistMemoryOp` | 将 ReasoningBank 记忆持久化到 JSON/Milvus。 |
| `LabelDeterminator` | 用 LLM-as-judge 判定轨迹成功/失败标签。 |
| `MemoryItemParser` | 将 LLM markdown 响应解析为结构化记忆条目。 |
| `messages_to_text` | 将消息列表转为格式化文本。 |
| `ReasoningBankPrompt` | ReasoningBank 总结 prompt 构造。 |

### ReMe

| 类 / 函数 | 说明 |
|---|---|
| `TrajectoryPreprocessOp` | 为总结预处理轨迹。 |
| `SuccessExtractionOp` | 从成功轨迹抽取洞察。 |
| `FailureExtractionOp` | 从失败轨迹抽取洞察。 |
| `ComparativeExtractionOp` | 对比成功与失败轨迹抽取洞察。 |
| `ComparativeAllExtractionOp` | 对比全部成功与失败轨迹抽取洞察。 |
| `MemoryValidationOp` | 校验抽取记忆的质量。 |
| `MemoryDeduplicationOp` | 依据 embedding 相似度去除重复记忆。 |
| `UpdateVectorStoreOp` | 将去重后 ReMe 记忆持久化到向量库。 |
| `PersistMemoryOp` | 将 ReMe 记忆持久化到 JSON/Milvus。 |
| `ReMePrompt` | ReMe 总结 prompt 构造。 |
| `parse_json_experience_response` | 解析 JSON 格式的经验响应。 |
| `calculate_cosine_similarity` | 计算两个 embedding 间的余弦相似度。 |

## tool（工具）

| 类 / 函数 | 说明 |
|---|---|
| `WikipediaSearchParams` | 维基百科检索参数。 |
| `search_wikipedia` | 检索维基百科并返回顶部结果的摘要。 |
