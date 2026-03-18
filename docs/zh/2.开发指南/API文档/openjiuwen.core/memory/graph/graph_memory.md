# openjiuwen.core.memory.graph.graph_memory.base

`openjiuwen.core.memory.graph.graph_memory` 提供图记忆核心类 `GraphMemory`，用于在用户对话与文档上维护知识图谱：通过 LLM 抽取实体与关系、与已有图数据合并去重，并支持在实体、关系与情节（episode）上进行可配置的语义检索与可选重排。

---

## class GraphMemory

```python
class openjiuwen.core.memory.graph.graph_memory.base.GraphMemory
```

图记忆类，负责知识图谱记忆的写入与检索。管理实体、关系与情节：从内容中经 LLM 抽取、与已有图数据合并/去重，并支持按策略在实体、关系、情节上检索，可选使用重排模型。

**构造函数**：`GraphMemory(db_config, llm_client=None, llm_structured_output=True, reranker=None, extraction_strategy=DEFAULT_STRATEGY, db_kwargs=None, llm_extra_kwargs=None, language="cn", debug=False)`

**参数**：

* **db_config**（[GraphConfig](../../foundation/store/graph/config.md)）：图存储配置（存储后端、集合等）。
* **llm_client**（[Model](../../foundation/llm/llm.md) | None，可选）：用于实体/关系抽取与合并的 LLM 客户端；为 None 时需在调用时另行提供或设置。默认值：None。
* **llm_structured_output**（bool，可选）：是否要求 LLM 输出结构化 JSON。默认值：True。
* **reranker**（[Reranker](../../retrieval/reranker/base.md) | None，可选）：检索时可选使用的交叉编码重排模型；当策略启用 rerank 时使用。默认值：None。
* **extraction_strategy**（[AddMemStrategy](../config.md)，可选）：抽取时的召回、合并与提示语言策略。默认值：DEFAULT_STRATEGY。
* **db_kwargs**（dict | None，可选）：创建图存储后端时传给工厂的额外关键字参数。默认值：None。
* **llm_extra_kwargs**（dict | None，可选）：每次调用 LLM 时合并的额外参数（如 temperature）。默认值：None。
* **language**（Literal["cn", "en"]，可选）：提示与内容的默认语言。默认值："cn"。
* **debug**（bool，可选）：为 True 时记录模板名与 LLM 请求/响应便于调试。默认值：False。

---

### embedder -> Embedding

图存储后端用于实体、关系与情节索引与检索的嵌入模型。

**返回**：当前使用的 [Embedding](../../retrieval/embedding/base.md) 实例。

---

### attach_embedder

```python
def attach_embedder(self, embedder: Embedding)
```

设置图存储后端用于索引与检索的嵌入模型。

**参数**：

* **embedder**（[Embedding](../../retrieval/embedding/base.md)）：要挂载的嵌入模型。

---

### attach_reranker

```python
def attach_reranker(self, reranker: Reranker)
```

设置检索策略启用 rerank 时使用的交叉编码重排模型。

**参数**：

* **reranker**（[Reranker](../../retrieval/reranker/base.md)）：要挂载的重排模型；必须为 `Reranker` 的实现，否则会抛出校验错误。

---

### register_search_strategy

```python
def register_search_strategy(
    self,
    name: str,
    search_entity: Optional[SearchConfig] = None,
    search_relation: Optional[SearchConfig] = None,
    search_episode: Optional[SearchConfig] = None,
    force: bool = False,
)
```

注册命名检索策略，可分别配置实体、关系、情节的检索参数；检索时通过 `search(..., search_strategy=name)` 使用。

**参数**：

* **name**（str）：策略名称（如 "default"）。
* **search_entity**（[SearchConfig](../config.md) | None，可选）：实体集合检索配置；None 表示使用默认。默认值：None。
* **search_relation**（[SearchConfig](../config.md) | None，可选）：关系集合检索配置；None 表示使用默认。默认值：None。
* **search_episode**（[SearchConfig](../config.md) | None，可选）：情节集合检索配置；None 表示使用默认。默认值：None。
* **force**（bool，可选）：为 True 时覆盖同名已有策略。默认值：False。

---

### add_memory

```python
async def add_memory(
    self,
    src_type: EpisodeType,
    user_id: str,
    content: list[BaseMessage | dict] | str,
    content_fmt_kwargs: Optional[dict] = None,
    reference_time: Optional[datetime.datetime] = None,
) -> GraphMemUpdate
```

将一段记忆情节写入图记忆：校验并规范化内容，抽取实体与关系，与已有实体/关系合并去重后落库。

**参数**：

* **src_type**（[EpisodeType](../config.md)）：情节类型：对话（conversation）/ 文档（document）/ JSON（json）。
* **user_id**（str）：用户 ID。
* **content**（list[BaseMessage | dict] | str）：情节内容。可为字符串，或消息列表（如 `[{"role":"user","content":"..."}, ...]` 或 `BaseMessage` 列表）。
* **content_fmt_kwargs**（dict | None，可选）：格式化参数，如 `{"user": "张三（用户）", "assistant": "智能客服小李"}`；仅当 `content` 为消息列表且 `src_type` 为 CONVERSATION 时生效。默认值：None。
* **reference_time**（datetime.datetime | None，可选）：情节发生时间的参考时间；不传则使用当前时间。默认值：None。

**返回**：

* **GraphMemUpdate**：本次写入的变更摘要（新增/更新/删除的实体、关系、情节）。

---

### search

```python
async def search(
    self,
    query: str,
    user_id: str | list[str],
    search_strategy: str = "default",
    *,
    entity: bool = True,
    relation: bool = True,
    episode: bool = True,
    query_embedding: Optional[list[float]] = None,
) -> dict[str, list[tuple[float, BaseGraphObject]]]
```

按自然语言或文本 query 在图记忆中进行检索，可同时或分别检索实体、关系、情节集合；结果按集合名映射为 (分数, 图对象) 列表。

**参数**：

* **query**（str）：检索 query 文本。
* **user_id**（str | list[str]）：用于过滤结果的用户 ID，可为单个 ID 或 ID 列表。
* **search_strategy**（str，可选）：已注册的检索策略名（如 "default"）。默认值："default"。
* **entity**（bool，可选）：是否检索实体集合。默认值：True。
* **relation**（bool，可选）：是否检索关系集合。默认值：True。
* **episode**（bool，可选）：是否检索情节集合。默认值：True。
* **query_embedding**（list[float] | None，可选）：预计算的 query 向量；为 None 时使用当前 embedder 对 query 做向量化。默认值：None。

**返回**：

* **dict[str, list[tuple[float, BaseGraphObject]]]**：键为集合名（"ENTITY_COLLECTION"、"RELATION_COLLECTION"、"EPISODE_COLLECTION"），值为 (分数, 图对象) 的列表。
