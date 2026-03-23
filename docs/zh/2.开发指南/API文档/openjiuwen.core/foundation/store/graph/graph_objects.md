# openjiuwen.core.foundation.store.graph.graph_object

图对象数据类：BaseGraphObject、Entity、Relation、Episode，用于在图存储中表示节点与边。

## class BaseGraphObject

```python
class openjiuwen.core.foundation.store.graph.graph_object.BaseGraphObject(BaseModel)
```

所有图对象的基类，提供通用字段：uuid、时间戳、用户 id、语言、元数据、文本内容及向量字段。

**参数**（构造函数）：

- **uuid**(str, 可选)：唯一标识，默认由 `get_uuid` 生成。
- **created_at**(int, 可选)：创建时间戳（UTC），默认由 `get_current_utc_timestamp` 生成。
- **user_id**(str, 可选)：用户 id。默认值："default_user"。
- **obj_type**(str, 可选)：对象类型。默认值：""。
- **language**(str, 可选)：语言标识，支持 "cn"、"en"。默认值："cn"。
- **metadata**(dict | None, 可选)：扩展元数据。默认值：{}。
- **content**(str, 可选)：用于全文/语义检索的文本。默认值：""。
- **content_embedding**(Sequence[float] | None, 可选)：内容稠密向量，由存储层填充。默认值：None。
- **content_bm25**(Sequence[float] | None, 可选)：内容 BM25 稀疏向量，通常无需设置、数据库自行维护。默认值：None。

### property version

```text
version -> int
```

图对象定义的版本号。

### fetch_embed_task

```python
def fetch_embed_task() -> list[tuple[Self, str, str]]
```

返回需要做嵌入的 (self, 属性名, 待嵌入文本) 列表。基类仅包含 content。

**返回**：

- **list[tuple[Self, str, str]]**：元组列表。

---

## class NamedGraphObject

```python
class openjiuwen.core.foundation.store.graph.graph_object.NamedGraphObject(BaseGraphObject)
```

带名称的图对象基类，增加 `name` 字段。

**参数**（继承 BaseGraphObject，并增加）：

- **name**(str, 可选)：名称。默认值：""。

---

## class Entity

```python
class openjiuwen.core.foundation.store.graph.graph_object.Entity(NamedGraphObject)
```

图中实体节点，对应人物、项目等，可有 name/content 的双路向量检索及关联的 relations、episodes。

**参数**（继承 NamedGraphObject，并增加）：

- **obj_type**(str, 可选)：固定为 "Entity"。默认值："Entity"。
- **name_embedding**(Sequence[float] | None, 可选)：名称向量，由存储层填充。默认值：None。
- **relations**(list[BaseGraphObject | str], 可选)：关联的关系对象或关系 uuid 列表。默认值：[]。
- **episodes**(list[str], 可选)：出现该实体的情节 uuid 列表。默认值：[]。
- **attributes**(dict | None, 可选)：实体属性。默认值：{}。

### fetch_embed_task

```python
def fetch_embed_task() -> list[tuple[Self, str, str]]
```

返回 (self, "content_embedding", content) 与 (self, "name_embedding", name)。

**返回**：

- **list[tuple[Self, str, str]]**：嵌入任务列表。

---

## class Relation

```python
class openjiuwen.core.foundation.store.graph.graph_object.Relation(NamedGraphObject)
```

图中关系边，连接两个实体（lhs、rhs），可有有效期等属性。

**参数**（继承 NamedGraphObject，并增加）：

- **obj_type**(str, 可选)：固定为 "Relation"。默认值："Relation"。
- **valid_since**(int, 可选)：关系生效时间戳。默认值：-1（校验后会被设为 created_at）。
- **valid_until**(int, 可选)：关系失效时间戳。默认值：-1。
- **offset_since**(int, 可选)：valid_since 的时区偏移。默认值：0。
- **offset_until**(int, 可选)：valid_until 的时区偏移。默认值：0。
- **lhs**(BaseGraphObject | str)：左侧实体（对象或 uuid）。
- **rhs**(BaseGraphObject | str)：右侧实体（对象或 uuid）。

### update_connected_entities

```python
def update_connected_entities() -> Self
```

根据当前 relation 更新 lhs/rhs 对应实体上的 `relations` 列表，将本关系加入其 `relations`（若尚未存在）。在批量构建图数据时，应在所有 Relation 创建完成后对每个 Relation 调用一次，以便写入存储时实体能正确引用关系。

**返回**：

- **Self**：当前 Relation，便于链式调用。

**示例**（见 `examples/store/graph_scenario_data.py`）：

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
# 构建完所有关系后统一更新实体的 relations 引用
for r in all_relations:
    r.update_connected_entities()
```

---

## class Episode

```python
class openjiuwen.core.foundation.store.graph.graph_object.Episode(BaseGraphObject)
```

情节节点，无 name 字段，包含一段描述文本及关联的实体 uuid 列表。

**参数**（继承 BaseGraphObject，并增加）：

- **obj_type**(str, 可选)：固定为 "Episode"。默认值："Episode"。
- **valid_since**(int, 可选)：情节生效时间戳。默认值：-1。
- **entities**(list[str], 可选)：该情节中出现的实体 uuid 列表（或可序列化为 uuid 的对象）。默认值：[]。

**示例**（见 `examples/store/graph_scenario_data.py`）：

```python
episode_weekly = Episode(
    content="Weekly sync: Alice presented ML platform metrics. Bob discussed search API.",
    language="en",
    entities=[alice.uuid, bob.uuid, carol.uuid],
    user_id="user_1",
)
```

> **参考示例**：更多 `Entity`、`Relation`、`Episode` 的构建与 `update_connected_entities` 用法请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/store/graph_scenario_data.py`。
