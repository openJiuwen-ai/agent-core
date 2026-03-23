# openjiuwen.core.memory.config.graph

`openjiuwen.core.memory.config.graph` 定义图记忆相关配置类型：情节来源类型、加记忆策略（召回、合并、语言）以及检索策略参数。

---

## class EpisodeType

```python
class openjiuwen.core.memory.config.graph.EpisodeType(Enum)
```

添加记忆时情节的来源类型。

| 成员         | 值  | 说明         |
|--------------|-----|--------------|
| CONVERSATION | 0   | 对话消息。   |
| DOCUMENT     | 1   | 文档文本。   |
| JSON         | 2   | JSON 内容。  |

---

## class BaseStrategy

```python
class openjiuwen.core.memory.config.graph.BaseStrategy(BaseModel)
```

加记忆时检索策略的基类（top_k、min_score、rank_config）。

**参数**（构造函数）：

* **top_k**（int，可选）：最多召回的条数。默认值：3。
* **min_score**（float，可选）：最低分数阈值。默认值：0.3。
* **rank_config**（BaseRankConfig，可选）：排序配置（如 [RRFRankConfig](../../foundation/store/graph/config.md)、[WeightedRankConfig](../../foundation/store/graph/config.md)）。默认值：RRFRankConfig。

---

## class RetrievalStrategy

```python
class openjiuwen.core.memory.config.graph.RetrievalStrategy(BaseStrategy)
```

加记忆时实体或关系的检索策略。

**参数**（构造函数）：

* **same_kind**（bool，可选）：是否限制为同类型对象。默认值：False。
* 继承 [BaseStrategy](#class-basestrategy) 的 **top_k**、**min_score**、**rank_config**。

---

## class EpisodeRetrievalStrategy

```python
class openjiuwen.core.memory.config.graph.EpisodeRetrievalStrategy(RetrievalStrategy)
```

加记忆时情节的检索策略（如历史上下文）。

**参数**（构造函数）：

* **same_kind**（bool，可选）：是否限制为同类型情节。默认值：False。
* **exclude_future_results**（bool，可选）：是否排除参考时间之后的情节。默认值：True。
* **rank_config**（BaseRankConfig，可选）：排序配置。默认值：RRFRankConfig()。

---

## class AddMemStrategy

```python
class openjiuwen.core.memory.config.graph.AddMemStrategy(BaseModel)
```

图记忆加记忆策略：抽取/去重语言、情节/实体/关系召回配置及合并/过滤开关。

**参数**（构造函数）：

* **chinese_entity**（bool，可选）：实体抽取是否强制使用中文（小体积 Qwen3 建议 True）。默认值：True。
* **chinese_entity_dedupe**（bool，可选）：实体去重是否使用中文。默认值：False。
* **chinese_relation**（bool，可选）：关系抽取是否使用中文（一般不建议）。默认值：False。
* **skip_uuid_dedupe**（bool，可选）：是否跳过 uuid4 去重。默认值：False。
* **recall_episode**（EpisodeRetrievalStrategy，可选）：历史情节召回策略。默认值：EpisodeRetrievalStrategy()。
* **recall_entity**（RetrievalStrategy，可选）：实体召回策略。默认值：WeightedRankConfig(dense_name=0.7, dense_content=0.1, sparse_content=0.2)，min_score=0.1。
* **recall_relation**（RetrievalStrategy，可选）：关系召回策略。默认值：RRFRankConfig()，min_score=0.05。
* **summary_target**（int，可选）：实体摘要目标字数（10–2000）。默认值：250。
* **merge_entities**（bool，可选）：是否进行实体合并。默认值：True。
* **merge_relations**（bool，可选）：是否进行关系合并。默认值：True。
* **merge_filter**（bool，可选）：实体合并后是否过滤关系。默认值：True。

---

## class SearchConfig

```python
class openjiuwen.core.memory.config.graph.SearchConfig(BaseStrategy)
```

图记忆检索配置（实体/关系/情节），用于在 [GraphMemory](./graph_memory.md) 中注册或使用检索策略。

**参数**（构造函数）：

* **bfs_k**（int，可选）：BFS 分支数。默认值：3。
* **bfs_depth**（int，可选）：BFS 深度。默认值：0。
* **filter_expr**（[QueryExpr](../../foundation/store/query/base.md) | None，可选）：额外过滤表达式。默认值：None。
* **output_fields**（List[str] | None，可选）：返回字段。默认值：None。
* **rerank**（bool，可选）：是否使用重排。默认值：False。
* **language**（Literal["cn", "en"]，可选）：检索语言。默认值："en"。
* 继承 [BaseStrategy](#class-basestrategy) 的 **top_k**、**min_score**、**rank_config**。

---

## DEFAULT_STRATEGY

```python
DEFAULT_STRATEGY: AddMemStrategy = AddMemStrategy()
```

默认加记忆策略实例，在未指定策略时由 [GraphMemory](./graph_memory.md) 使用。
