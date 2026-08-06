# Experience 模块接口文档

本模块提供经验知识库的构建、检索与评估能力。典型流程：

1. 将轨迹数据转换为 `TraceRecord`
2. 用 `TraceEvaluator` 评估轨迹
3. 用 `EmbeddingClient` 初始化向量能力
4. 用 `ExperienceBank` 创建持久化知识库
5. 用 `ExperienceBankBuildConfig` 配置经验构建
6. 用 `ExperienceBaseBuilder` 蒸馏并写入知识库
7. 用 `ExperienceRetriever` 在运行时快速检索经验

### 类型索引

| 类型 | 角色 | 章节 |
|------|------|------|
| `EmbeddingClient` | 向量嵌入客户端 | [EmbeddingClient](#embeddingclient) |
| `TraceRecord` | 轨迹记录数据类（评估/构建的输入） | [TraceRecord](#tracerecord) |
| `TraceEvaluator` | LLM 驱动的轨迹评估器 | [TraceEvaluator](#traceevaluator) |
| `ExperienceBank` | 持久化经验知识库 | [ExperienceBank](#experiencebank) |
| `ExperienceItem` | 经验条目数据类（`ExperienceBank` 的单元） | [ExperienceItem](#experienceitem) |
| `ExperienceBankBuildConfig` | 经验库构建配置（喂给 `ExperienceBaseBuilder`） | [ExperienceBankBuildConfig](#experiencebankbuildconfig) |
| `ExperienceBaseBuilder` | 经验库构建器（cluster→distill→persist） | [ExperienceBaseBuilder](#experiencebasebuilder) |
| `ExperienceRetriever` | 运行时轻量检索器 | [ExperienceRetriever](#experienceretriever) |

---

## EmbeddingClient

向量嵌入客户端，支持两种后端：

- **OpenAI-compatible API** — 调用远程嵌入服务（`base_url` 后端）
- **本地 sentence-transformers** — 纯本地推理（`model_name` 后端）

以下参数均为关键字参数（构造函数签名带 `*`）。

### 构造参数

| 参数 | 类型 | 是否必填 | 默认值 | 说明 |
|------|------|----------|--------|------|
| `base_url` | `str \| None` | 二选一 | `None` | Embedding 模型服务地址（API 后端） |
| `api_key` | `str` | 可选 | `""` | apiKey；仅 `base_url` 后端时使用 |
| `model` | `str` | 可选 | `""` | API 模型名；仅 `base_url` 后端时使用 |
| `model_name` | `str` | 二选一 | `""` | 本地 sentence-transformers 模型名 |
| `normalize` | `bool` | 可选 | `True` | 是否 L2 归一化输出向量（归一化后内积 = cosine 相似度） |
| `dimension` | `int \| None` | 可选 | `None` | 输出向量维度；`None` 时 API 不传 `dimensions`，用模型原生维度 |

> `base_url` 与 `model_name` 二选一必填：均未提供或同时提供会抛 `RuntimeError`。

### 属性

| 名称 | 类型 | 说明 |
|------|------|------|
| `dimension` | `int \| None` | 当前配置的向量维度（未设为 `None`） |

### 方法[构建时用户不主动调用]

#### `reset_token_counter() -> int`

重置内部 token 计数器，返回重置前的累计值。无参数。

#### `embed(text: str) -> list[float]`

返回单条文本的归一化向量。`text` 必填。

#### `embed_batch(texts: list[str]) -> list[list[float]]`

批量嵌入。`texts` 必填。

---

## TraceRecord

轨迹记录数据类，表示一次用户交互的完整信息。

### 字段

| 字段 | 类型 | 是否必填 | 默认值 | 说明 |
|------|------|----------|--------|------|
| `trace_id` | `str` | 必填 | — | 轨迹唯一标识 |
| `query` | `str` | 可选 | `""` | 用户原始查询 |
| `skills` | `list[str]` | 可选 | `[]` | 本次使用的 skill ID 列表 |
| `messages` | `list[dict]` | 可选 | `[]` | 交互消息历史（用于增强评估） |
| `result` | `str` | 可选 | `""` | assistant 执行结果文本 |
| `error_type` | `str \| None` | 可选 | `None` | 错误分类（非严格 enum，兜底含 `unevaluated`） |
| `error_detail` | `str \| None` | 可选 | `None` | 错误详情描述 |
| `success` | `bool` | 可选 | `False` | skill 选择是否正确 |

### 方法

#### `to_dict() -> dict`

无参数，将所有字段序列化为可 JSON 化的 dict。

#### `from_dict(data: dict) -> TraceRecord`

`data` 必填；其中 `trace_id` 必须存在，其余字段缺失时用默认值。

### 序列化示例

```python
from openjiuwen.symphony.experience import TraceRecord

record = TraceRecord(
    trace_id="trace_001",
    query="帮我订一张机票",
    skills=["flight_booking"],
    result="已为您预订航班 CA1234",
)

d = record.to_dict()
record2 = TraceRecord.from_dict(d)
```

---

## TraceEvaluator

LLM 驱动的轨迹评估器，判断 skill 选择是否正确。

### 构造参数

| 参数 | 类型 | 是否必填 | 默认值 | 说明 |
|------|------|----------|--------|------|
| `llm_client` | `Any \| None` | 可选 | `None` | OpenAI-compatible chat client|
| `llm_model` | `str` | 可选 | `""` | 评估用模型名 |

### 方法

#### `evaluate(records: list[TraceRecord]) -> list[TraceRecord]`

`records` 必填。跳过 `skills` 为空的记录，对剩余记录就地填充 `success` / `error_type` / `error_detail`，返回处理后的新列表。

##### 兜底行为

当 `llm_client` / `llm_model` 未配置，或 LLM 调用失败、返回空 content、JSON 不可解析时，走保守兜底（不再用结果长度判定）：

- 无 `query` → `success=False`，`error_type="empty"`；
- `result` 为空/纯空白 → `success=False`，`error_type="empty"`；
- 记录已带上游 `error_type`（如执行异常）→ `success=False`，沿用原 `error_type`；
- 其余一律 `success=False`，`error_type="unevaluated"`，**不作为成功样本写入经验库**（宁缺毋滥，避免污染聚类与 pattern 蒸馏）。

> 即：LLM 不可用时，经验库不会因兜底而增长；`success=True` 只可能来自 LLM 的明确判定。

---

## ExperienceBank

持久化经验知识库，基于 FAISS 向量索引实现快速语义检索。

### 存储结构

```
experience_kb/
├── meta.json            # 完整性校验清单（含 vector_dimension）
├── scalar/
│   └── metadata.jsonl   # 条目元数据（不含向量）
└── vector/
    ├── faiss_index.bin  # FAISS 索引
    └── embeddings.npy   # 向量矩阵
```

### 构造参数

| 参数 | 类型 | 是否必填 | 默认值 | 说明 |
|------|------|----------|--------|------|
| `index_dir` | `str \| Path` | 必填 | — | 知识库存储目录，已有数据会自动加载 |
| `embedding_client` | `EmbeddingClient` | 必填 | — | 嵌入客户端（查询时用） |

### 仅关键字参数（`*` 之后）

| 参数 | 类型 | 是否必填 | 默认值 | 说明 |
|------|------|----------|--------|------|
| `vector_algorithm` | `str` | 可选 | `"Flat"` | FAISS `index_factory` 算法描述串；IVF 索引自动设 `nprobe=10` |

> IVF 类索引需要 `train`，向量数少于 `nlist` 时自动降级为 Flat。

### 属性

| 名称 | 类型 | 说明 |
|------|------|------|
| `items` | `list[ExperienceItem]` | 内存中条目的浅拷贝列表 |
| `count` | `int` | 条目数量 |

### 方法[构建时无需主动调用]

#### `exist(skills: list[str]) -> bool`

判断是否已有相同 skill_ids 集合的条目。`skills` 必填。

#### `add(item: ExperienceItem) -> None`

追加单条并重建 FAISS 索引、持久化。`item` 必填。

#### `add_batch(items: list[ExperienceItem]) -> None`

批量追加，仅重建一次索引、持久化一次。`items` 必填；空列表为 no-op。

#### `remove(item_id: str) -> bool`

按 id 删除条目，返回是否找到并删除。`item_id` 必填。

#### `search_by_embedding(query: str, top_k: int = 1, threshold: float = 0.80) -> list[tuple[float, ExperienceItem]]`

FAISS 语义检索，返回 `(相似度, 条目)` 列表，按相似度降序；低于 `threshold` 的剔除。

| 参数 | 类型 | 是否必填 | 默认值 | 说明 |
|------|------|----------|--------|------|
| `query` | `str` | 必填 | — | 查询文本 |
| `top_k` | `int` | 可选 | `1` | 返回前 k 个匹配 |
| `threshold` | `float` | 可选 | `0.80` | 最低相似度阈值 |

#### `search_with_skill_ids(query: str, top_k: int = 1, threshold: float = 0.80) -> list[str]`

`search_by_embedding` 的便捷封装，返回去重后的 skill_ids（保持插入顺序）。参数同上。

#### `persist() -> None`

原子写入 `metadata.jsonl` / `faiss_index.bin` / `embeddings.npy` / `meta.json`。无参数。

> `add` / `add_batch` / `remove` 内部已自动调用 `persist`，通常无需手动调用。

#### `generate_id() -> str`

生成 `exp_NNNN` 形式的递增 ID。无参数。

#### `create_item(query_pattern: str, query_examples: list[str], skill_ids: list[str], success_count: int = 1) -> ExperienceItem`

构造带自动嵌入与 ID 的 `ExperienceItem`。嵌入**仅来自 `query_pattern`**——通用模板承载 skill 的意图更干净，拼接原始 `query_examples` 会引入具体实体、稀释模板语义，反而让同领域但错误的 skill 越过阈值。`query_examples` 仍会存到 item 上供展示/调试，但不参与嵌入。

| 参数 | 类型 | 是否必填 | 默认值 | 说明 |
|------|------|----------|--------|------|
| `query_pattern` | `str` | 必填 | — | 通用查询模式 |
| `query_examples` | `list[str]` | 必填 | — | 代表性查询示例（仅存储，不嵌入） |
| `skill_ids` | `list[str]` | 必填 | — | 关联的 skill ID 列表 |
| `success_count` | `int` | 可选 | `1` | 成功命中次数 |

---

## ExperienceItem

经验库的一条目，表示"一类映射到某 skill 集合的查询"。通常由 `ExperienceBank.create_item()` 构造，无需手动 new。

### 字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `id` | `str` | — | 条目唯一标识（`exp_NNNN` 形式，`create_item` 自动生成） |
| `query_pattern` | `str` | `""` | 通用查询模式（嵌入来源） |
| `query_examples` | `list[str]` | `[]` | 代表性查询示例（仅展示/调试，不参与嵌入） |
| `skill_ids` | `list[str]` | `[]` | 关联的 skill ID 列表 |
| `success_count` | `int` | `1` | 成功命中次数 |
| `embedding` | `list[float]` | `[]` | 向量（持久化时不落盘，加载时从 `embeddings.npy` 回填） |
| `created_at` | `float` | `0.0` | 创建时间戳 |
| `last_hit_at` | `float` | `0.0` | 最近命中时间戳 |

### 方法

#### `to_dict() -> dict` / `from_dict(data: dict) -> ExperienceItem`

序列化/反序列化。`to_dict` 不含 `embedding`（向量由 `ExperienceBank` 单独管理）。

---

## ExperienceBankBuildConfig

经验库构建配置，封装 `ExperienceBaseBuilder` 的管线参数。纯 dataclass，所有字段均可选。

### 字段

| 字段 | 类型 | 是否必填 | 默认值 | 说明 |
|------|------|----------|--------|------|
| `min_cluster_size` | `int` | 可选 | `1` | 最小聚类大小 |
| `max_workers` | `int` | 可选 | `8` | LLM 蒸馏并发线程数 |
| `max_success_examples` | `int` | 可选 | `20` | 每聚类提供给 LLM 的最大成功样例数 |
| `pending_flush_threshold` | `int` | 可选 | `20` | 增量模式自动 flush 的 pending 阈值 |
| `min_hits_for_pattern` | `int` | 可选 | `1` | 增量模式形成模式所需最小记录数 |
| `skill_cluster_num` | `int \| None` | 可选 | `None` | FAISS K-Means `k` 覆盖值；`None` 时自动选取 |
| `pattern_merge_threshold` | `float` | 可选 | `0.9` | 合并相似模式的 cosine 阈值 |
| `query_examples_count` | `int` | 可选 | `5` | 每条 `ExperienceItem` 保存的示例查询上限 |

---

## ExperienceBaseBuilder

经验库构建器，执行 cluster → distill → persist 全流程。

### 构造参数

| 参数 | 类型 | 是否必填 | 默认值 | 说明 |
|------|------|----------|--------|------|
| `kb` | `ExperienceBank` | 必填 | — | 目标知识库 |
| `embedding_client` | `EmbeddingClient` | 必填 | — | 嵌入客户端 |
| `llm_client` | `Any` | 必填 | — | LLM client（用于蒸馏）；为空抛 `ValueError` |
| `llm_model` | `str` | 必填 | — | LLM 模型名；为空抛 `ValueError` |

### 仅关键字参数（`*` 之后）

| 参数 | 类型 | 是否必填 | 默认值 | 说明 |
|------|------|----------|--------|------|
| `skills_info` | `list[dict[str, str]] \| None` | 可选 | `None` | skill 描述信息（`{"name":..., "description":...}`），辅助 LLM 蒸馏 |
| `build_config` | `ExperienceBankBuildConfig \| None` | 可选 | `None` | 管线配置，缺省时用默认实例 |

### 方法

#### `build(traces: list[TraceRecord]) -> int`

`traces` 必填。执行 cluster→distill→persist 全流程，返回创建的经验条目数。

**异常**：目标 KB 非空时抛 `ValueError`（全量构建会覆盖数据，请用空目录或先清空）。

#### `add(trace: TraceRecord) -> None`

`trace` 必填。记录成功的查询-skill 映射到 pending 缓冲区，达到 `pending_flush_threshold` 时自动 flush。

> `trace.success` 为 `False` 或 `skills` 为空时静默丢弃并记 error 日志。

#### `flush() -> int`

无参数。手动 flush pending 缓冲区，返回新增条目数（pending 为空时返回 0）。建议优雅关闭时调用。

---

## ExperienceRetriever

轻量检索器，封装 `ExperienceBank` 的语义搜索，返回 skill_ids 列表。

### 构造参数

| 参数 | 类型 | 是否必填 | 默认值 | 说明 |
|------|------|----------|--------|------|
| `kb` | `ExperienceBank` | 必填 | — | 经验知识库实例 |

### 仅关键字参数（`*` 之后）

| 参数 | 类型 | 是否必填 | 默认值 | 说明 |
|------|------|----------|--------|------|
| `threshold` | `float` | 可选 | `0.80` | 最低 cosine 相似度阈值 |
| `top_k` | `int` | 可选 | `1` | 返回前 k 个匹配 |

### 方法

#### `search(query: str) -> list[str]`

`query` 必填。命中返回去重后的 skill_ids 列表（保持插入顺序），未命中或命中但 skill_ids 为空时返回 `[]`（调用方可凭空列表判断是否回退到其他检索策略）。

---

## 完整使用示例

### 全量构建 + 检索

```python
import json
from pathlib import Path
from openjiuwen.symphony.experience import (
    EmbeddingClient,
    ExperienceBank,
    ExperienceBaseBuilder,
    ExperienceBankBuildConfig,
    ExperienceRetriever,
    TraceEvaluator,
    TraceRecord,
)

# 1. 初始化 EmbeddingClient（本地模型，使用原生维度）
embedder = EmbeddingClient(model_name="BAAI/bge-small-zh-v1.5")

# 2. 创建空知识库（全量构建要求目录为空）
kb = ExperienceBank(index_dir="experience_kb", embedding_client=embedder)

# 3. 加载轨迹数据
raw = json.loads(Path("traces.json").read_text(encoding="utf-8"))
traces = [TraceRecord.from_dict(d) for d in raw]

# 4. 评估轨迹（可选但推荐）
from openai import OpenAI
openai_client = OpenAI(base_url="...", api_key="...")
evaluator = TraceEvaluator(llm_client=openai_client, llm_model="qwen3-32b")
evaluated = evaluator.evaluate(traces)

# 5. 构建经验库
skills_info = json.loads(Path("skills_list.json").read_text(encoding="utf-8"))
builder = ExperienceBaseBuilder(
    kb=kb,
    embedding_client=embedder,
    llm_client=openai_client,
    llm_model="qwen3-32b",
    skills_info=skills_info,
)
created = builder.build(evaluated)
print(f"经验库构建完成: {created} 条条目")

# 6. 运行时检索
retriever = ExperienceRetriever(kb=kb, threshold=0.6, top_k=1)

skills = retriever.search("帮我订机票去北京")
if skills:
    print(f"经验推荐: {skills}")
else:
    print("经验未命中，回退到树检索")
```

### 增量添加 + 检索

```python
kb = ExperienceBank(index_dir="experience_kb", embedding_client=embedder)
builder = ExperienceBaseBuilder(
    kb=kb, embedding_client=embedder,
    llm_client=openai_client, llm_model="qwen3-32b",
    build_config=ExperienceBankBuildConfig(pending_flush_threshold=10),
)

builder.add(TraceRecord(
    trace_id="t_new_001",
    query="翻译这段中文到英文",
    skills=["translation"],
    result="Here is the translation...",
    success=True,
))

builder.flush()
```

---

## 轨迹解析（可选）

`symphony.experience.trace` 子模块把 agent 运行时产生的 session 文件（每个 session 一个目录，含 `metadata.json` / `history.json`）解析成 `TraceRecord` 列表，作为评估/构建的输入。它**不假定 session 目录的位置**——所有函数都要求调用方通过 `sessions_dir` 参数注入目录路径。

| 函数 | 签名 | 说明 |
|------|------|------|
| `list_session_ids` | `(sessions_dir: Path) -> list[str]` | 列出 sessions 目录下所有 session ID（排除 `heartbeat_` 前缀） |
| `parse_session` | `(session_id: str, sessions_dir: Path) -> list[TraceRecord]` | 解析单个 session，按 `request_id` 切分轨迹段，从 `skill_tool`/`symphony_compose_graph` 抽取 skill 名 |
| `parse_all_sessions` | `(sessions_dir: Path) -> list[TraceRecord]` | 解析全部 session，跳过无法解析的 |
| `parse_and_store` | `(sessions_dir: Path) -> list[TraceRecord]` | 仅解析新（未处理）session，追加到 `sessions_dir.parent/trace_store/records.jsonl`，返回新增记录 |
| `load_all_records` | `(sessions_dir: Path) -> list[TraceRecord]` | 从 `trace_store` 加载已解析的全部 `TraceRecord` |
| `clear_store` | `(sessions_dir: Path) -> None` | 清空 `trace_store` 的缓存与 processed index |

> `sessions_dir` 为**必传参数**，无默认值——调用方负责指明 agent session 文件所在目录，trace 模块对路径零假设、不依赖任何全局目录函数。

---

## 依赖

| 包 | 说明 | 必需 |
|----|------|------|
| `faiss-cpu` | 向量索引与聚类 | 是 |
| `numpy` | 向量运算 | 是 |
| `sentence-transformers` | 本地嵌入后端 | 可选（使用本地模型时需要） |
| `openai` | API 嵌入后端 + LLM 评估/蒸馏 | 可选（使用 API 时需要） |
