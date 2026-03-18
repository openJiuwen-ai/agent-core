# openjiuwen.core.memory.graph.graph_memory.base

`openjiuwen.core.memory.graph.graph_memory` provides the graph memory core class `GraphMemory`, which maintains a knowledge graph over user conversations and documents: it extracts entities and relations via LLM, merges and deduplicates with existing graph data, and supports configurable semantic search over entities, relations, and episodes with optional reranking.

---

## class GraphMemory

```python
class openjiuwen.core.memory.graph.graph_memory.base.GraphMemory
```

Graph memory that handles addition and retrieval of knowledge graph memory. Manages entities, relations, and episodes: extracts them from content via LLM, merges/deduplicates with existing graph data, and supports configurable search over entities, relations, and episodes with optional reranking.

**Constructor**: `GraphMemory(db_config, llm_client=None, llm_structured_output=True, reranker=None, extraction_strategy=DEFAULT_STRATEGY, db_kwargs=None, llm_extra_kwargs=None, language="cn", debug=False)`

**Parameters**:

* **db_config** ([GraphConfig](../../foundation/store/graph/config.md)): Graph store configuration (storage backend, collections, etc.).
* **llm_client** ([Model](../../foundation/llm/llm.md) | None, optional): LLM client used for entity/relation extraction and merging; when None, must be set or supplied per call. Default: None.
* **llm_structured_output** (bool, optional): Whether to request structured JSON output from the LLM. Default: True.
* **reranker** ([Reranker](../../retrieval/reranker/base.md) | None, optional): Cross-encoder reranker for search when a strategy enables rerank. Default: None.
* **extraction_strategy** ([AddMemStrategy](../config.md), optional): Strategy for recall, merge, and prompt language during extraction. Default: DEFAULT_STRATEGY.
* **db_kwargs** (dict | None, optional): Extra keyword arguments passed to the graph store factory when creating the backend. Default: None.
* **llm_extra_kwargs** (dict | None, optional): Extra arguments merged into every LLM invoke (e.g. temperature). Default: None.
* **language** (Literal["cn", "en"], optional): Default language for prompts and content. Default: "cn".
* **debug** (bool, optional): If True, log template names and LLM request/response for debugging. Default: False.

---

### embedder -> Embedding

Embedding used by the graph backend for indexing and search over entities, relations, and episodes.

**Returns**: The current [Embedding](../../retrieval/embedding/base.md) instance.

---

### attach_embedder

```python
def attach_embedder(self, embedder: Embedding)
```

Set the embedding used by the graph backend for indexing and search.

**Parameters**:

* **embedder** ([Embedding](../../retrieval/embedding/base.md)): Embedding to attach.

---

### attach_reranker

```python
def attach_reranker(self, reranker: Reranker)
```

Set the cross-encoder reranker used when a search strategy has rerank enabled.

**Parameters**:

* **reranker** ([Reranker](../../retrieval/reranker/base.md)): Reranker to attach; must be an implementation of `Reranker`, otherwise a validation error is raised.

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

Register a named search strategy with configs for entity, relation, and episode search; use via `search(..., search_strategy=name)`.

**Parameters**:

* **name** (str): Strategy name (e.g. "default").
* **search_entity** ([SearchConfig](../config.md) | None, optional): Config for entity collection search; None to use default. Default: None.
* **search_relation** ([SearchConfig](../config.md) | None, optional): Config for relation collection search; None to use default. Default: None.
* **search_episode** ([SearchConfig](../config.md) | None, optional): Config for episode collection search; None to use default. Default: None.
* **force** (bool, optional): If True, overwrite an existing strategy with the same name. Default: False.

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

Add a memory episode to the graph: validate and normalize content, extract entities and relations, merge and deduplicate with existing data, then persist.

**Parameters**:

* **src_type** ([EpisodeType](../config.md)): Episode type: conversation / document / json.
* **user_id** (str): User ID.
* **content** (list[BaseMessage | dict] | str): Episode content; either a string or a list of messages (e.g. `[{"role":"user","content":"..."}, ...]` or `BaseMessage` list).
* **content_fmt_kwargs** (dict | None, optional): Formatting arguments such as `{"user": "张三（用户）", "assistant": "智能客服小李"}`; only used when `content` is a message list and `src_type` is CONVERSATION. Default: None.
* **reference_time** (datetime.datetime | None, optional): Reference time for when the episode takes place; omitted means current time. Default: None.

**Returns**:

* **GraphMemUpdate**: Summary of changes (added/updated/removed entities, relations, episodes).

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

Search the graph by query across entity, relation, and/or episode collections; returns a mapping from collection name to list of (score, graph object) tuples.

**Parameters**:

* **query** (str): Query text.
* **user_id** (str | list[str]): User ID(s) to restrict results.
* **search_strategy** (str, optional): Registered strategy name (e.g. "default"). Default: "default".
* **entity** (bool, optional): Whether to search the entity collection. Default: True.
* **relation** (bool, optional): Whether to search the relation collection. Default: True.
* **episode** (bool, optional): Whether to search the episode collection. Default: True.
* **query_embedding** (list[float] | None, optional): Precomputed query embedding; None to embed `query` with the current embedder. Default: None.

**Returns**:

* **dict[str, list[tuple[float, BaseGraphObject]]]**: Keys are collection names ("ENTITY_COLLECTION", "RELATION_COLLECTION", "EPISODE_COLLECTION"); values are lists of (score, graph object) tuples.
