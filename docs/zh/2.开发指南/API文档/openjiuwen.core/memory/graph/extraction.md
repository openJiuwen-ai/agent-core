# openjiuwen.core.memory.graph.extraction

`openjiuwen.core.memory.graph.extraction` 是图记忆（Graph Memory）的**实体与关系抽取模块**，负责从对话、文档和 JSON 中抽取实体与关系，为记忆图提供结构化输入。本模块包含多语言响应模型基类、实体/关系类型定义、抽取用 Pydantic 模型、提示组装函数以及 LLM 响应解析工具。

---

## openjiuwen.core.memory.graph.extraction.base

### class MultilingualBaseModel

```python
class openjiuwen.core.memory.graph.extraction.base.MultilingualBaseModel(BaseModel)
```

基于 Pydantic 的 LLM 响应模型基类，支持多语言描述与 JSON Schema 生成，用于将输出模型转为字符串或结构化格式供 LLM 使用。

**参数**（继承自 `BaseModel`，由子类定义具体字段。）

#### classmethod multilingual_model_json_schema

```python
def multilingual_model_json_schema(cls, language: str = "cn", strict: bool = False, **kwargs) -> dict[str, Any]
```

按指定语言生成 JSON Schema；`strict=True` 时遵循 OpenAI 结构化输出格式（禁用 `additionalProperties`）。

**参数**：

* **language**(str, 可选)：语言标识，如 "cn"、"en"。默认值："cn"。
* **strict**(bool, 可选)：是否严格模式。默认值：False。
* **kwargs**：透传给 `model_json_schema` 的额外参数。

**返回**：

* **dict[str, Any]**：多语言描述替换后的 JSON Schema。

#### classmethod readable_schema

```python
def readable_schema(cls, language: str = "cn", **kwargs) -> tuple[str, dict]
```

生成供 LLM 阅读的 schema 定义字符串及嵌套属性字典。

**参数**：

* **language**(str, 可选)：语言标识。默认值："cn"。
* **kwargs**：透传参数。

**返回**：

* **tuple[str, dict]**：格式化为字符串的 schema 与 `$defs` 中 ref 的 properties 字典。

**示例**：

```python
>>> from openjiuwen.core.memory.graph.extraction.prompts import entity_extraction  # 注册 cn/en
>>> from openjiuwen.core.memory.graph.extraction.extraction_models import Fact, RelationExtraction
>>> out_str, ref_dict = Fact.readable_schema(language="cn")
>>> print(out_str)
name: str  # 该实体联系的名称
fact: str  # 关于实体联系的事实
valid_since: str  # 事实/关系的生效日期，请使用ISO格式YYYY-MM-DDTHH:MM:SS[+HH:MM]
valid_until: str  # 事实/关系的中止日期，请使用ISO格式YYYY-MM-DDTHH:MM:SS[+HH:MM]
source_id: int  # 主体的实体ID
target_id: int  # 客体的实体ID
>>> ref_dict
{}
>>> out_str, ref_dict = RelationExtraction.readable_schema(language="en")
>>> print(out_str)
extracted_relations: list[Fact]  # List of extracted relations
>>> list(ref_dict.keys())
['Fact']
>>> ref_dict["Fact"]  # 嵌套 ref：字段名 -> type 与 description
{'name': {'type': 'string', 'description': 'Name of factual relation'}, 'fact': {...}, 'valid_since': {...}, ...}
```

#### classmethod response_format

```python
def response_format(cls, language: str = "cn") -> dict[str, Any]
```

转换为 LLM 可用的响应格式，作为OpenAI标准的 `response_format` 字段。

**参数**：

* **language**(str, 可选)：语言标识。默认值："cn"。

**返回**：

* **dict[str, Any]**：包含 `type`、`json_schema` 等键的格式字典。

---

## openjiuwen.core.memory.graph.extraction.entity_type_definition

实体与关系类型定义及多语言描述注册表，用于抽取时的类型约束与展示。

### class EntityDefAttr

```python
class openjiuwen.core.memory.graph.extraction.entity_type_definition.EntityDefAttr(MultilingualBaseModel)
```

实体类型的属性定义。

**参数**（构造函数）：

* **content**(str, 可选)：属性内容占位描述。默认值：""。

### class EntityDef

```python
class openjiuwen.core.memory.graph.extraction.entity_type_definition.EntityDef(BaseModel)
```

基础实体类型定义，包含类型名、多语言描述与属性模型。

**参数**（构造函数）：

* **name**(str, 可选)：类型名称。默认值："Entity"。
* **description**(Dict[str, str], 可选)：多语言描述字典。默认值：由模块常量提供。
* **attributes**(MultilingualBaseModel, 可选)：属性模型，默认使用 `EntityDefAttr`。

### class RelationDef

```python
class openjiuwen.core.memory.graph.extraction.entity_type_definition.RelationDef(BaseModel)
```

关系类型定义，包含名称、描述及左右实体类型。

**参数**（构造函数）：

* **name**(str, 可选)：关系类型名称。默认值："Relation"。
* **description**(Dict[str, str], 可选)：多语言描述。默认值：由模块常量提供。
* **lhs**(type[EntityDef])：关系左端实体类型。
* **rhs**(type[EntityDef])：关系右端实体类型。

### class HumanEntity

```python
class openjiuwen.core.memory.graph.extraction.entity_type_definition.HumanEntity(EntityDef)
```

表示“用户”的实体类型。

**参数**（构造函数）：继承 `EntityDef`，**name** 默认 "Human"。

### class AIEntity

```python
class openjiuwen.core.memory.graph.extraction.entity_type_definition.AIEntity(EntityDef)
```

表示“AI 助手”的实体类型。

**参数**（构造函数）：继承 `EntityDef`，**name** 默认 "AI"。

---

## openjiuwen.core.memory.graph.extraction.extraction_models

抽取结果对应的 Pydantic 模型，用于实体声明、摘要、去重、关系抽取、时区预测与关系合并等。

### class Datetime

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.Datetime(MultilingualBaseModel)
```

表示日期时间（当前未使用）。

**参数**（构造函数）：**year**、**month**、**day**、**hour**、**minute**、**second**（均为 int，带多语言 description）。

### class EntityDeclaration

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.EntityDeclaration(MultilingualBaseModel)
```

单条实体声明：名称与类型 id。

**参数**（构造函数）：

* **name**(str)：实体名称。
* **entity_type_id**(int)：实体类型 id（对应 `EntityDef` 列表下标）。

### class Duplication

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.Duplication(MultilingualBaseModel)
```

实体去重结果：代表名、主 id 与重复 id 列表。

**参数**（构造函数）：

* **name**(str)：代表实体名称。
* **id**(int)：保留的实体 id。
* **duplicate_ids**(list[int])：合并掉的重复 id 列表。

### class Fact

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.Fact(MultilingualBaseModel)
```

一条事实关系：关系名、事实描述、有效期及源/目标实体 id。

**参数**（构造函数）：

* **name**(str)：关系类型名。
* **fact**(str)：事实内容。
* **valid_since**(str)：生效起始。
* **valid_until**(str)：生效结束。
* **source_id**(int)：源实体 id。
* **target_id**(int)：目标实体 id。

### class PossibleTimezone

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.PossibleTimezone(MultilingualBaseModel)
```

可能的时区猜测：名称、相对 UTC 偏移与推理说明。

**参数**（构造函数）：**name**(str)、**offset_from_utc**(str)、**reasoning**(str)。

### class EntityExtraction

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.EntityExtraction(MultilingualBaseModel)
```

实体声明抽取的输出模型。

**参数**（构造函数）：

* **extracted_entities**(list[EntityDeclaration])：抽取到的实体列表。

### class EntitySummary

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.EntitySummary(MultilingualBaseModel)
```

实体摘要与属性抽取的输出模型。

**参数**（构造函数）：

* **summary**(str)：实体摘要文本。
* **attributes**(dict)：属性键值对。

### class EntityDuplication

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.EntityDuplication(MultilingualBaseModel)
```

实体去重的输出模型。

**参数**（构造函数）：

* **duplicated_entities**(list[Duplication])：去重结果列表。

### class RelationExtraction

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.RelationExtraction(MultilingualBaseModel)
```

关系抽取的输出模型。

**参数**（构造函数）：

* **extracted_relations**(list[Fact])：抽取到的关系列表。

### class RelevantFacts

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.RelevantFacts(MultilingualBaseModel)
```

关系过滤结果：简要推理与相关关系 id 列表。

**参数**（构造函数）：

* **brief_reasoning**(str)：简要推理。
* **relevant_relations**(list[int])：相关关系的 id 列表。

### class TimezonePredictions

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.TimezonePredictions(MultilingualBaseModel)
```

时区预测的输出模型。

**参数**（构造函数）：

* **extracted_relations**(list[PossibleTimezone])：预测的时区列表（字段名沿用“关系”以兼容提示）。

### class MergeRelations

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.MergeRelations(MultilingualBaseModel)
```

关系合并的输出模型。

**参数**（构造函数）：

* **need_merging**(bool)：是否需要合并。
* **short_reasoning**(str)：简短推理。
* **combined_content**(str)：合并后的内容。
* **duplicate_ids**(list[int])：被合并的关系 id 列表。
* **valid_since**(str)：合并后生效起始。
* **valid_until**(str)：合并后生效结束。

---

## openjiuwen.core.memory.graph.extraction.prompts.manager

### class ThreadSafePromptManager

```python
class openjiuwen.core.memory.graph.extraction.prompts.manager.ThreadSafePromptManager
```

线程安全的提示词模板管理器，用于加载与解析抽取用提示词（.pr.md），单例。通过 `openjiuwen.core.memory.graph.extraction.prompts` 的 `TemplateManager` 别名对外使用。

```
ThreadSafePromptManager()
```

无参构造，返回单例；首次初始化时扫描 `**/*.pr.md` 并批量注册到内部 `PromptMgr`。

#### staticmethod load_pr_content

```python
def load_pr_content(content: str) -> list[dict[str, str]]
```

从 .pr.md 文件内容解析出角色消息列表。使用 `#user#`、`#system#`、`#assistant#`、`#tool#` 标记角色，每段内容对应一个 `{"role": ..., "content": ...}`。

**参数**：

* **content**(str)：.pr.md 原始内容。

**返回**：

* **list[dict[str, str]]**：消息列表。

#### get

```python
def get(self, name: str) -> Optional[PromptTemplate]
```

按名称获取已注册的提示词模板。

**参数**：

* **name**(str)：模板名（如 `entity_extraction_conversation_cn`）。

**返回**：

* **PromptTemplate | None**：模板实例，未注册则为 None。

#### register_in_bulk

```python
def register_in_bulk(self, prompt_dir: str, name: str = "")
```

将指定目录下所有 `.pr.md` 文件注册为提示词模板。

**参数**：

* **prompt_dir**(str)：包含 .pr.md 的目录路径。
* **name**(str, 可选)：该批模板的逻辑名称，用于日志。默认值：""。

---

## openjiuwen.core.memory.graph.extraction.extraction_prompts

按情节类型组装实体/关系抽取、去重、合并、时区预测等提示词的入口函数，返回模板变量、提示词模板与 LLM 响应格式。

### func extract_entity_declaration

```python
def extract_entity_declaration(
    src_type: EpisodeType,
    content: str,
    history: str = "",
    description: Optional[str] = None,
    entity_types: Optional[List[EntityDef]] = None,
    *,
    language: str = "cn",
    extras: Optional[Dict] = None,
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]
```

组装实体声明（名称）抽取的提示词。`src_type` 决定模板名（如 conversation/document/json）。更多关于情节类型的配置请参考 [config](../config.md) 中的 `EpisodeType`。

**参数**：

* **src_type**(EpisodeType)：情节来源类型（对话/文档/JSON）。
* **content**(str)：当前轮次或当前文档/JSON 内容。
* **history**(str, 可选)：历史上下文。默认值：""。
* **description**(str, 可选)：来源描述。默认值：None。
* **entity_types**(List[EntityDef] | None, 可选)：实体类型定义列表，为 None 时使用默认 `[EntityDef()]`。默认值：None。
* **language**(str, 可选)：语言。默认值："cn"。
* **extras**(Dict | None, 可选)：额外模板变量。默认值：None。
* **indent**(int, 可选)：输出 JSON 缩进。默认值：2。

**返回**：

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**：模板变量、提示词模板、LLM 响应格式。

### func extract_entity_attributes

```python
def extract_entity_attributes(
    entity: Entity,
    content: str,
    history: str = "",
    language: str = "cn",
    extras: Optional[Dict] = None,
    *,
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]
```

组装实体摘要与属性抽取的提示词。`entity` 为待补充摘要/属性的实体。更多关于 Entity 的定义请参考 [graph_objects](../../foundation/store/graph/graph_objects.md)。

**参数**：

* **entity**(Entity)：待抽取摘要与属性的实体。更多关于 Entity 的定义请参考 [graph_objects](../../foundation/store/graph/graph_objects.md)。
* **content**(str)：当前内容。
* **history**(str, 可选)：历史上下文。默认值：""。
* **language**(str, 可选)：语言。默认值："cn"。
* **extras**(Dict | None, 可选)：额外模板变量。默认值：None。
* **indent**(int, 可选)：输出缩进。默认值：2。

**返回**：

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**：模板变量、提示词模板、响应格式。

### func extract_relation_declaration

```python
def extract_relation_declaration(
    relation_types: Optional[List[Type[RelationDef]]],
    entities: List[EntityDeclaration],
    reference_time: int,
    tz_info: Any,
    content: str,
    *,
    history: str = "",
    entity_types: Optional[List[EntityDef]] = None,
    description: Optional[str] = None,
    language: str = "cn",
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]
```

组装关系抽取的提示词，需传入关系类型、已抽取实体、参考时间与时区信息。

**参数**：

* **relation_types**(List[Type[RelationDef]] | None)：关系类型定义列表。
* **entities**(List[EntityDeclaration])：已抽取的实体声明列表（带 id）。
* **reference_time**(int)：参考时间戳。
* **tz_info**(Any)：时区信息（可为 dict/list，会序列化为 JSON 字符串）。
* **content**(str)：当前内容。
* **history**(str, 可选)：历史上下文。默认值：""。
* **entity_types**(List[EntityDef] | None, 可选)：实体类型定义。默认值：None。
* **description**(str, 可选)：来源描述。默认值：None。
* **language**(str, 可选)：语言。默认值："cn"。
* **indent**(int, 可选)：输出缩进。默认值：2。

**返回**：

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**：模板变量、提示词模板、响应格式。

### func extract_timezone

```python
def extract_timezone(
    content: str,
    history: str = "",
    description: Optional[str] = None,
    language: str = "cn",
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]
```

组装时区猜测的提示词。

**参数**：

* **content**(str)：当前内容。
* **history**(str, 可选)：历史上下文。默认值：""。
* **description**(str, 可选)：来源描述。默认值：None。
* **language**(str, 可选)：语言。默认值："cn"。
* **indent**(int, 可选)：输出缩进。默认值：2。

**返回**：

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**：模板变量、提示词模板、响应格式。

### func merge_existing_entities

```python
def merge_existing_entities(
    target: Entity,
    sources: List[Entity],
    language: str = "cn",
    extras: Optional[Dict] = None,
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]
```

组装将多个已有实体合并到目标实体的提示词。

**参数**：

* **target**(Entity)：合并目标实体。
* **sources**(List[Entity])：待合并的源实体列表。
* **language**(str, 可选)：语言。默认值："cn"。
* **extras**(Dict | None, 可选)：额外模板变量。默认值：None。
* **indent**(int, 可选)：输出缩进。默认值：2。

**返回**：

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**：模板变量、提示词模板、响应格式。

### func filter_relations_for_merge

```python
def filter_relations_for_merge(
    target: Entity,
    relations: List[Relation],
    language: str = "cn",
    extras: Optional[Dict] = None,
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]
```

组装关系过滤提示：针对目标实体，从给定关系列表中筛选与合并相关的关系。

**参数**：

* **target**(Entity)：目标实体。
* **relations**(List[Relation])：候选关系列表（可为 `Relation` 或 `dict`）。
* **language**(str, 可选)：语言。默认值："cn"。
* **extras**(Dict | None, 可选)：额外模板变量。默认值：None。
* **indent**(int, 可选)：输出缩进。默认值：2。

**返回**：

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**：模板变量、提示词模板、响应格式。

### func dedupe_entity_list

```python
def dedupe_entity_list(
    content: str,
    candidate_entities: List[EntityDeclaration],
    existing_entities: List[Dict],
    entity_types: Optional[List[EntityDef]] = None,
    history: str = "",
    *,
    description: Optional[str] = None,
    language: str = "cn",
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]
```

组装实体去重提示：在已有实体列表基础上，判断候选实体是否重复并给出合并 id。

**参数**：

* **content**(str)：当前内容。
* **candidate_entities**(List[EntityDeclaration])：候选实体声明。
* **existing_entities**(List[Dict])：已有实体（字典列表）。
* **entity_types**(List[EntityDef] | None, 可选)：实体类型定义。默认值：None。
* **history**(str, 可选)：历史上下文。默认值：""。
* **description**(str, 可选)：来源描述。默认值：None。
* **language**(str, 可选)：语言。默认值："cn"。
* **indent**(int, 可选)：输出缩进。默认值：2。

**返回**：

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**：模板变量、提示词模板、响应格式。

### func dedupe_relation_list

```python
def dedupe_relation_list(
    content: str,
    relation: Relation,
    existing_relations: List[Dict],
    existing_entities: List[Entity],
    history: str = "",
    *,
    description: Optional[str] = None,
    language: str = "cn",
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]
```

组装关系去重/合并提示：判断新关系是否与已有关系重复并给出合并结果。

**参数**：

* **content**(str)：当前内容。
* **relation**(Relation)：新关系。
* **existing_relations**(List[Dict])：已有关系列表。
* **existing_entities**(List[Entity])：已有实体列表（用于上下文化）。
* **history**(str, 可选)：历史上下文。默认值：""。
* **description**(str, 可选)：来源描述。默认值：None。
* **language**(str, 可选)：语言。默认值："cn"。
* **indent**(int, 可选)：输出缩进。默认值：2。

**返回**：

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**：模板变量、提示词模板、响应格式。

### func format_new_entities

```python
def format_new_entities(
    entities: List[EntityDeclaration],
    entity_types: Optional[List[EntityDef]] = None,
    start_idx: int = 1,
    language: str = "cn",
) -> str
```

将候选实体声明格式化为带编号的字符串列表，供提示中使用；若提供 `entity_types` 会先输出类型说明。

**参数**：

* **entities**(List[EntityDeclaration])：实体声明列表。
* **entity_types**(List[EntityDef] | None, 可选)：实体类型定义。默认值：None。
* **start_idx**(int, 可选)：起始编号。默认值：1。
* **language**(str, 可选)：语言。默认值："cn"。

**返回**：

* **str**：格式化后的多行字符串。

---

## openjiuwen.core.memory.graph.extraction.custom_types

### JSONLike

类型别名：`Union[dict[str, Any], list[Any]]`，表示可从 JSON 解析得到的字典或列表结构。

---

## openjiuwen.core.memory.graph.extraction.parse_response

从 LLM 回复中解析 JSON 与结构化内容的工具函数。

### func parse_json

```python
def parse_json(resp: str, output_schema: Optional[dict[str, Any]] = None) -> Optional[JSONLike]
```

从 LLM 回复中尝试解析 JSON。优先在 markdown 代码块（无语言或 `json`）中查找并解码；若无则尝试整段 `raw_decode`。若提供 `output_schema` 且含 `required`，则只保留并模糊匹配这些键。

**参数**：

* **resp**(str)：LLM 原始回复文本。
* **output_schema**(dict[str, Any] | None, 可选)：可选输出 schema，可含 `json_schema.required` 用于键过滤。默认值：None。

**返回**：

* **JSONLike | None**：解析得到的字典或列表；失败为 None。

---

> **参考示例**：更多图记忆与抽取的使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中与 Graph Memory、实体关系抽取相关的示例代码。
