# openjiuwen.core.foundation.store.graph.graph_object

Graph object data classes: BaseGraphObject, Entity, Relation, Episode for nodes and edges in the graph store.

## class BaseGraphObject

```python
class openjiuwen.core.foundation.store.graph.graph_object.BaseGraphObject(BaseModel)
```

Base class for all graph objects with common fields: uuid, timestamps, user id, language, metadata, text content and vector fields.

**Constructor parameters**:

- **uuid**(str, optional): Unique id; default from `get_uuid`.
- **created_at**(int, optional): Creation timestamp (UTC); default from `get_current_utc_timestamp`.
- **user_id**(str, optional): User id. Default: "default_user".
- **obj_type**(str, optional): Object type. Default: "".
- **language**(str, optional): Language id, "cn" or "en". Default: "cn".
- **metadata**(dict | None, optional): Extra metadata. Default: {}.
- **content**(str, optional): Text for full-text/semantic search. Default: "".
- **content_embedding**(Sequence[float] | None, optional): Dense content vector; set by store. Default: None.
- **content_bm25**(Sequence[float] | None, optional): BM25 sparse vector; typically not set since databases maintain BM25 internally. Default: None.

### property version

```text
version -> int
```

Graph object definition version.

### fetch_embed_task

```python
def fetch_embed_task() -> list[tuple[Self, str, str]]
```

Return list of (self, attribute_name, text_to_embed). Base class only includes content.

**Returns**:

- **list[tuple[Self, str, str]]**: List of tuples.

---

## class NamedGraphObject

```python
class openjiuwen.core.foundation.store.graph.graph_object.NamedGraphObject(BaseGraphObject)
```

Base for graph objects with a name field.

**Additional parameter**:

- **name**(str, optional): Name. Default: "".

---

## class Entity

```python
class openjiuwen.core.foundation.store.graph.graph_object.Entity(NamedGraphObject)
```

Entity node (e.g. person, project) with name/content vectors and related relations and episodes.

**Additional parameters**:

- **obj_type**(str, optional): Set to "Entity". Default: "Entity".
- **name_embedding**(Sequence[float] | None, optional): Name vector; set by store. Default: None.
- **relations**(list[BaseGraphObject | str], optional): Related relation objects or uuids. Default: [].
- **episodes**(list[str], optional): Episode uuids where this entity appears. Default: [].
- **attributes**(dict | None, optional): Entity attributes. Default: {}.

### fetch_embed_task

```python
def fetch_embed_task() -> list[tuple[Self, str, str]]
```

Returns (self, "content_embedding", content) and (self, "name_embedding", name).

**Returns**:

- **list[tuple[Self, str, str]]**: Embedding task list.

---

## class Relation

```python
class openjiuwen.core.foundation.store.graph.graph_object.Relation(NamedGraphObject)
```

Relation edge between two entities (lhs, rhs), with optional validity period.

**Additional parameters**:

- **obj_type**(str, optional): Set to "Relation". Default: "Relation".
- **valid_since**(int, optional): Relation valid from timestamp. Default: -1 (set to created_at in validator).
- **valid_until**(int, optional): Relation valid until timestamp. Default: -1.
- **offset_since**(int, optional): Timezone offset for valid_since. Default: 0.
- **offset_until**(int, optional): Timezone offset for valid_until. Default: 0.
- **lhs**(BaseGraphObject | str): Left-hand entity (object or uuid).
- **rhs**(BaseGraphObject | str): Right-hand entity (object or uuid).

### update_connected_entities

```python
def update_connected_entities() -> Self
```

Update the `relations` list on lhs/rhs entities to include this relation if not already present. Call after all relations are created so that entities reference relations correctly when written to the store.

**Returns**:

- **Self**: This Relation for chaining.

**Example** (see `examples/store/graph_scenario_data.py`):

```python
rel_alice_bob = Relation(
    name="works_with",
    content="Alice and Bob collaborate on ML infrastructure.",
    lhs=alice,
    rhs=bob,
    language="en",
    user_id="user_1",
)
all_relations.append(rel_alice_bob)
for r in all_relations:
    r.update_connected_entities()
```

---

## class Episode

```python
class openjiuwen.core.foundation.store.graph.graph_object.Episode(BaseGraphObject)
```

Episode node (no name field): description text and list of entity uuids.

**Additional parameters**:

- **obj_type**(str, optional): Set to "Episode". Default: "Episode".
- **valid_since**(int, optional): Episode valid from timestamp. Default: -1.
- **entities**(list[str], optional): Entity uuids (or serializable to uuid) in this episode. Default: [].

**Example** (see `examples/store/graph_scenario_data.py`):

```python
episode_weekly = Episode(
    content="Weekly sync: Alice presented ML platform metrics. Bob discussed search API.",
    language="en",
    entities=[alice.uuid, bob.uuid, carol.uuid],
    user_id="user_1",
)
```

> **Reference examples**: For more `Entity`, `Relation`, `Episode` construction and `update_connected_entities` usage, see [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) `examples/store/graph_scenario_data.py`.
