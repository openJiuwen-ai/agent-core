# openjiuwen.core.memory.config.graph

`openjiuwen.core.memory.config.graph` defines configuration types for graph memory: episode source types, add-memory strategies (recall, merge, language), and search strategy parameters.

---

##  EpisodeType

```python
class openjiuwen.core.memory.config.graph.EpisodeType(Enum)
```

Type of possible episode sources when adding memory.

| Member         | Value | Description        |
|----------------|-------|--------------------|
| CONVERSATION   | 0     | Dialogue messages. |
| DOCUMENT       | 1     | Document text.    |
| JSON           | 2     | JSON content.     |

---

## class BaseStrategy

```python
class openjiuwen.core.memory.config.graph.BaseStrategy(BaseModel)
```

Base retrieval strategy used during add-memory (top_k, min_score, rank_config).

**Parameters** (constructor):

* **top_k** (int, optional): Maximum number of results to retrieve. Default: 3.
* **min_score** (float, optional): Minimum score threshold. Default: 0.3.
* **rank_config** (BaseRankConfig, optional): Ranking configuration (e.g. [RRFRankConfig](../../foundation/store/graph/config.md), [WeightedRankConfig](../../foundation/store/graph/config.md)). Default: RRFRankConfig.

---

## class RetrievalStrategy

```python
class openjiuwen.core.memory.config.graph.RetrievalStrategy(BaseStrategy)
```

Retrieval strategy during add memory (entities or relations).

**Parameters** (constructor):

* **same_kind** (bool, optional): Whether to restrict to the same object kind. Default: False.
* Inherits **top_k**, **min_score**, **rank_config** from [BaseStrategy](#class-basestrategy).

---

## class EpisodeRetrievalStrategy

```python
class openjiuwen.core.memory.config.graph.EpisodeRetrievalStrategy(RetrievalStrategy)
```

Retrieval strategy for episodes when adding memory (e.g. for history context).

**Parameters** (constructor):

* **same_kind** (bool, optional): Restrict to same episode type. Default: False.
* **exclude_future_results** (bool, optional): Exclude episodes after reference time. Default: True.
* **rank_config** (BaseRankConfig, optional): Ranking config. Default: RRFRankConfig().
* **min_score** (float, optional): Minimum score threshold. Default: 0.025 (overrides the **min_score** default 0.3 from [BaseStrategy](#class-basestrategy)).

---

## class AddMemStrategy

```python
class openjiuwen.core.memory.config.graph.AddMemStrategy(BaseModel)
```

Strategy for adding graph memory: language options for extraction/dedupe, recall configs for episode/entity/relation, and merge/filter flags.

**Parameters** (constructor):

* **chinese_entity** (bool, optional): Use Chinese for entity extraction regardless of episode language (recommended True for small Qwen3 models). Default: True.
* **chinese_entity_dedupe** (bool, optional): Use Chinese for entity deduplication. Default: False.
* **chinese_relation** (bool, optional): Use Chinese for relation extraction (usually not recommended). Default: False.
* **skip_uuid_dedupe** (bool, optional): Skip uuid4 de-duplication. Default: False.
* **recall_episode** (EpisodeRetrievalStrategy, optional): Strategy for recalling past episodes. Default: EpisodeRetrievalStrategy().
* **recall_entity** (RetrievalStrategy, optional): Strategy for recalling entities. Default: WeightedRankConfig(dense_name=0.7, dense_content=0.1, sparse_content=0.2), min_score=0.1.
* **recall_relation** (RetrievalStrategy, optional): Strategy for recalling relations. Default: RRFRankConfig(), min_score=0.02.
* **summary_target** (int, optional): Target word/character count for entity summaries (10–2000). Default: 250.
* **merge_entities** (bool, optional): Whether to perform entity merging. Default: True.
* **merge_relations** (bool, optional): Whether to perform relation merging. Default: True.
* **merge_filter** (bool, optional): Whether to filter relations after entity merging. Default: True.

---

## class SearchConfig

```python
class openjiuwen.core.memory.config.graph.SearchConfig(BaseStrategy)
```

Config for searching graph memory (entities, relations, or episodes). Used when registering or using a search strategy in [GraphMemory](./graph_memory.md).

**Parameters** (constructor):

* **bfs_k** (int, optional): BFS branching factor. Default: 3.
* **bfs_depth** (int, optional): BFS depth. Default: 0.
* **filter_expr** ([QueryExpr](../../foundation/store/query/base.md) | None, optional): Extra filter expression. Default: None.
* **output_fields** (List[str] | None, optional): Fields to return. Default: None.
* **rerank** (bool, optional): Whether to use reranker. Default: False.
* **language** (Literal["cn", "en"], optional): Query language. Default: "en".
* Inherits **top_k**, **min_score**, **rank_config** from [BaseStrategy](#class-basestrategy).

---

## DEFAULT_STRATEGY

```python
DEFAULT_STRATEGY: AddMemStrategy = AddMemStrategy()
```

Default add-memory strategy instance used by [GraphMemory](./graph_memory.md) when no strategy is specified.
