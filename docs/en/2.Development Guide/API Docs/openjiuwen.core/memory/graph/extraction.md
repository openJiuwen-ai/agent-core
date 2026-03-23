# openjiuwen.core.memory.graph.extraction

`openjiuwen.core.memory.graph.extraction` is the **entity and relation extraction** submodule for Graph Memory: it extracts entities and relations from conversations, documents, and JSON to feed the memory graph. It includes a multilingual response model base class, entity/relation type definitions, Pydantic models for extraction outputs, prompt-assembly functions, and LLM response parsing utilities.

---

## openjiuwen.core.memory.graph.extraction.base

### class MultilingualBaseModel

```python
class openjiuwen.core.memory.graph.extraction.base.MultilingualBaseModel(BaseModel)
```

Pydantic-based base class for LLM response models, with multilingual descriptions and JSON Schema generation, used to turn output models into string or structured format for the LLM.

**Parameters** (inherited from `BaseModel`; subclasses define concrete fields.)

#### classmethod multilingual_model_json_schema

```python
def multilingual_model_json_schema(cls, language: str = "cn", strict: bool = False, **kwargs) -> dict[str, Any]
```

Builds JSON Schema for the given language; when `strict=True`, follows OpenAI structured output format (disables `additionalProperties`).

**Parameters**:

* **language**(str, optional): Language code, e.g. "cn", "en". Default: "cn".
* **strict**(bool, optional): Whether to use strict mode. Default: False.
* **kwargs**: Extra arguments passed to `model_json_schema`.

**Returns**:

* **dict[str, Any]**: JSON Schema with multilingual descriptions applied.

#### classmethod readable_schema

```python
def readable_schema(cls, language: str = "cn", **kwargs) -> tuple[str, dict]
```

Produces an LLM-readable schema definition string and nested properties dict.

**Parameters**:

* **language**(str, optional): Language code. Default: "cn".
* **kwargs**: Pass-through arguments.

**Returns**:

* **tuple[str, dict]**: Formatted schema string and refs' properties from `$defs`.

**Example**:

```python
>>> from openjiuwen.core.memory.graph.extraction.prompts import entity_extraction  # register cn/en
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
>>> ref_dict["Fact"]  # nested ref: field name -> type & description
{'name': {'type': 'string', 'description': 'Name of factual relation'}, 'fact': {...}, 'valid_since': {...}, ...}
```

#### classmethod response_format

```python
def response_format(cls, language: str = "cn") -> dict[str, Any]
```

Converts to LLM response format, which can be used as `response_format` parameter in OpenAI standard.

**Parameters**:

* **language**(str, optional): Language code. Default: "cn".

**Returns**:

* **dict[str, Any]**: Format dict with `type`, `json_schema`, etc.

---

## openjiuwen.core.memory.graph.extraction.entity_type_definition

Entity and relation type definitions and multilingual description registries for extraction type constraints and display.

### class EntityDefAttr

```python
class openjiuwen.core.memory.graph.extraction.entity_type_definition.EntityDefAttr(MultilingualBaseModel)
```

Attribute definition for an entity type.

**Parameters** (constructor):

* **content**(str, optional): Placeholder for attribute content. Default: "".

### class EntityDef

```python
class openjiuwen.core.memory.graph.extraction.entity_type_definition.EntityDef(BaseModel)
```

Base entity type: name, multilingual description, and attribute model.

**Parameters** (constructor):

* **name**(str, optional): Type name. Default: "Entity".
* **description**(Dict[str, str], optional): Multilingual description dict. Default: from module constant.
* **attributes**(MultilingualBaseModel, optional): Attribute model, default `EntityDefAttr`.

### class RelationDef

```python
class openjiuwen.core.memory.graph.extraction.entity_type_definition.RelationDef(BaseModel)
```

Relation type: name, description, and left/right entity types.

**Parameters** (constructor):

* **name**(str, optional): Relation type name. Default: "Relation".
* **description**(Dict[str, str], optional): Multilingual description. Default: from module constant.
* **lhs**(type[EntityDef]): Left entity type.
* **rhs**(type[EntityDef]): Right entity type.

### class HumanEntity

```python
class openjiuwen.core.memory.graph.extraction.entity_type_definition.HumanEntity(EntityDef)
```

Entity type for the user.

**Parameters** (constructor): Inherits `EntityDef`; **name** defaults to "Human".

### class AIEntity

```python
class openjiuwen.core.memory.graph.extraction.entity_type_definition.AIEntity(EntityDef)
```

Entity type for the AI assistant.

**Parameters** (constructor): Inherits `EntityDef`; **name** defaults to "AI".

---

## openjiuwen.core.memory.graph.extraction.extraction_models

Pydantic models for extraction results: entity declarations, summaries, deduplication, relation extraction, timezone prediction, and relation merging.

### class Datetime

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.Datetime(MultilingualBaseModel)
```

Represents datetime (currently unused).

**Parameters** (constructor): **year**, **month**, **day**, **hour**, **minute**, **second** (all int with multilingual description).

### class EntityDeclaration

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.EntityDeclaration(MultilingualBaseModel)
```

Single entity declaration: name and type id.

**Parameters** (constructor):

* **name**(str): Entity name.
* **entity_type_id**(int): Entity type id (index into `EntityDef` list).

### class Duplication

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.Duplication(MultilingualBaseModel)
```

Entity deduplication result: representative name, kept id, and duplicate id list.

**Parameters** (constructor):

* **name**(str): Representative entity name.
* **id**(int): Kept entity id.
* **duplicate_ids**(list[int]): List of merged duplicate ids.

### class Fact

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.Fact(MultilingualBaseModel)
```

A factual relation: relation name, fact content, validity range, and source/target entity ids.

**Parameters** (constructor):

* **name**(str): Relation type name.
* **fact**(str): Fact content.
* **valid_since**(str): Validity start.
* **valid_until**(str): Validity end.
* **source_id**(int): Source entity id.
* **target_id**(int): Target entity id.

### class PossibleTimezone

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.PossibleTimezone(MultilingualBaseModel)
```

A possible timezone guess: name, UTC offset, and reasoning.

**Parameters** (constructor): **name**(str), **offset_from_utc**(str), **reasoning**(str).

### class EntityExtraction

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.EntityExtraction(MultilingualBaseModel)
```

Output model for entity declaration extraction.

**Parameters** (constructor):

* **extracted_entities**(list[EntityDeclaration]): Extracted entities.

### class EntitySummary

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.EntitySummary(MultilingualBaseModel)
```

Output model for entity summary and attribute extraction.

**Parameters** (constructor):

* **summary**(str): Entity summary text.
* **attributes**(dict): Attribute key-value pairs.

### class EntityDuplication

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.EntityDuplication(MultilingualBaseModel)
```

Output model for entity deduplication.

**Parameters** (constructor):

* **duplicated_entities**(list[Duplication]): Deduplication result list.

### class RelationExtraction

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.RelationExtraction(MultilingualBaseModel)
```

Output model for relation extraction.

**Parameters** (constructor):

* **extracted_relations**(list[Fact]): Extracted relations.

### class RelevantFacts

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.RelevantFacts(MultilingualBaseModel)
```

Relation filter result: brief reasoning and relevant relation id list.

**Parameters** (constructor):

* **brief_reasoning**(str): Brief reasoning.
* **relevant_relations**(list[int]): Relevant relation ids.

### class TimezonePredictions

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.TimezonePredictions(MultilingualBaseModel)
```

Output model for timezone prediction.

**Parameters** (constructor):

* **extracted_relations**(list[PossibleTimezone]): Predicted timezones (field name kept for prompt compatibility).

### class MergeRelations

```python
class openjiuwen.core.memory.graph.extraction.extraction_models.MergeRelations(MultilingualBaseModel)
```

Output model for relation merging.

**Parameters** (constructor):

* **need_merging**(bool): Whether to merge.
* **short_reasoning**(str): Short reasoning.
* **combined_content**(str): Merged content.
* **duplicate_ids**(list[int]): Relation ids to merge.
* **valid_since**(str): Merged validity start.
* **valid_until**(str): Merged validity end.

---

## openjiuwen.core.memory.graph.extraction.prompts.manager

### class ThreadSafePromptManager

```python
class openjiuwen.core.memory.graph.extraction.prompts.manager.ThreadSafePromptManager
```

Thread-safe prompt template manager for loading and resolving extraction prompts (.pr.md); singleton. Exposed as `TemplateManager` in `openjiuwen.core.memory.graph.extraction.prompts`.

```
ThreadSafePromptManager()
```

No-arg constructor; returns the singleton. On first init, scans `**/*.pr.md` and registers them in bulk with the internal `PromptMgr`.

#### staticmethod load_pr_content

```python
def load_pr_content(content: str) -> list[dict[str, str]]
```

Parses .pr.md content into a list of role messages. Uses `#user#`, `#system#`, `#assistant#`, `#tool#` markers; each segment becomes `{"role": ..., "content": ...}`.

**Parameters**:

* **content**(str): Raw .pr.md content.

**Returns**:

* **list[dict[str, str]]**: Message list.

#### get

```python
def get(self, name: str) -> Optional[PromptTemplate]
```

Returns the registered prompt template by name.

**Parameters**:

* **name**(str): Template name (e.g. `entity_extraction_conversation_cn`).

**Returns**:

* **PromptTemplate | None**: Template instance, or None if not registered.

#### register_in_bulk

```python
def register_in_bulk(self, prompt_dir: str, name: str = "")
```

Registers all `.pr.md` files in the given directory as prompt templates.

**Parameters**:

* **prompt_dir**(str): Directory containing .pr.md files.
* **name**(str, optional): Logical name for the batch (for logging). Default: "".

---

## openjiuwen.core.memory.graph.extraction.extraction_prompts

Entry functions that assemble prompts for entity/relation extraction, deduplication, merging, and timezone prediction by episode type; they return template variables, prompt template, and LLM response format.

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

Assembles the prompt for entity declaration (name) extraction. `src_type` determines the template name (e.g. conversation/document/json). For episode type configuration see `EpisodeType` in [config](../config.md).

**Parameters**:

* **src_type**(EpisodeType): Episode source type (conversation/document/json).
* **content**(str): Current turn or document/JSON content.
* **history**(str, optional): History context. Default: "".
* **description**(str, optional): Source description. Default: None.
* **entity_types**(List[EntityDef] | None, optional): Entity type definitions; if None, uses default `[EntityDef()]`. Default: None.
* **language**(str, optional): Language. Default: "cn".
* **extras**(Dict | None, optional): Extra template variables. Default: None.
* **indent**(int, optional): JSON indent. Default: 2.

**Returns**:

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**: Template vars, prompt template, LLM response format.

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

Assembles the prompt for entity summary and attribute extraction. For `Entity` see [graph_objects](../../foundation/store/graph/graph_objects.md).

**Parameters**:

* **entity**(Entity): Entity to extract summary and attributes for.
* **content**(str): Current content.
* **history**(str, optional): History context. Default: "".
* **language**(str, optional): Language. Default: "cn".
* **extras**(Dict | None, optional): Extra template variables. Default: None.
* **indent**(int, optional): Output indent. Default: 2.

**Returns**:

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**: Template vars, prompt template, response format.

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

Assembles the prompt for relation extraction; requires relation types, extracted entities, reference time, and timezone info.

**Parameters**:

* **relation_types**(List[Type[RelationDef]] | None): Relation type definitions.
* **entities**(List[EntityDeclaration]): Extracted entity declarations (with ids).
* **reference_time**(int): Reference timestamp.
* **tz_info**(Any): Timezone info (dict/list will be serialized to JSON string).
* **content**(str): Current content.
* **history**(str, optional): History context. Default: "".
* **entity_types**(List[EntityDef] | None, optional): Entity type definitions. Default: None.
* **description**(str, optional): Source description. Default: None.
* **language**(str, optional): Language. Default: "cn".
* **indent**(int, optional): Output indent. Default: 2.

**Returns**:

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**: Template vars, prompt template, response format.

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

Assembles the prompt for timezone guessing.

**Parameters**:

* **content**(str): Current content.
* **history**(str, optional): History context. Default: "".
* **description**(str, optional): Source description. Default: None.
* **language**(str, optional): Language. Default: "cn".
* **indent**(int, optional): Output indent. Default: 2.

**Returns**:

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**: Template vars, prompt template, response format.

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

Assembles the prompt to merge multiple existing entities into a target entity.

**Parameters**:

* **target**(Entity): Target entity.
* **sources**(List[Entity]): Source entities to merge.
* **language**(str, optional): Language. Default: "cn".
* **extras**(Dict | None, optional): Extra template variables. Default: None.
* **indent**(int, optional): Output indent. Default: 2.

**Returns**:

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**: Template vars, prompt template, response format.

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

Assembles the prompt to filter relations relevant to merging for a target entity.

**Parameters**:

* **target**(Entity): Target entity.
* **relations**(List[Relation]): Candidate relations (can be `Relation` or dict).
* **language**(str, optional): Language. Default: "cn".
* **extras**(Dict | None, optional): Extra template variables. Default: None.
* **indent**(int, optional): Output indent. Default: 2.

**Returns**:

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**: Template vars, prompt template, response format.

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

Assembles the prompt for entity deduplication: decide whether candidates duplicate existing entities and return merge ids.

**Parameters**:

* **content**(str): Current content.
* **candidate_entities**(List[EntityDeclaration]): Candidate entity declarations.
* **existing_entities**(List[Dict]): Existing entities (list of dicts).
* **entity_types**(List[EntityDef] | None, optional): Entity type definitions. Default: None.
* **history**(str, optional): History context. Default: "".
* **description**(str, optional): Source description. Default: None.
* **language**(str, optional): Language. Default: "cn".
* **indent**(int, optional): Output indent. Default: 2.

**Returns**:

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**: Template vars, prompt template, response format.

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

Assembles the prompt for relation deduplication/merge: decide if the new relation duplicates existing ones and return merge result.

**Parameters**:

* **content**(str): Current content.
* **relation**(Relation): New relation.
* **existing_relations**(List[Dict]): Existing relations.
* **existing_entities**(List[Entity]): Existing entities (for context).
* **history**(str, optional): History context. Default: "".
* **description**(str, optional): Source description. Default: None.
* **language**(str, optional): Language. Default: "cn".
* **indent**(int, optional): Output indent. Default: 2.

**Returns**:

* **Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]**: Template vars, prompt template, response format.

### func format_new_entities

```python
def format_new_entities(
    entities: List[EntityDeclaration],
    entity_types: Optional[List[EntityDef]] = None,
    start_idx: int = 1,
    language: str = "cn",
) -> str
```

Formats candidate entity declarations into a numbered string list for use in prompts; if `entity_types` is provided, type descriptions are included first.

**Parameters**:

* **entities**(List[EntityDeclaration]): Entity declarations.
* **entity_types**(List[EntityDef] | None, optional): Entity type definitions. Default: None.
* **start_idx**(int, optional): Start index. Default: 1.
* **language**(str, optional): Language. Default: "cn".

**Returns**:

* **str**: Formatted multi-line string.

---

## openjiuwen.core.memory.graph.extraction.custom_types

### JSONLike

Type alias: `Union[dict[str, Any], list[Any]]`, for JSON-parsable dict or list structures.

---

## openjiuwen.core.memory.graph.extraction.parse_response

Utilities to parse JSON and structured content from LLM responses.

### func parse_json

```python
def parse_json(resp: str, output_schema: Optional[dict[str, Any]] = None) -> Optional[JSONLike]
```

Tries to parse JSON from the LLM response. Preferentially decodes inside markdown code blocks (no lang or `json`); otherwise tries raw_decode on the whole string. If `output_schema` with `required` is provided, only those keys are kept (with fuzzy matching).

**Parameters**:

* **resp**(str): Raw LLM response text.
* **output_schema**(dict[str, Any] | None, optional): Optional output schema; may contain `json_schema.required` for key filtering. Default: None.

**Returns**:

* **JSONLike | None**: Parsed dict or list, or None on failure.

---

> **Reference**: For more examples of Graph Memory and extraction usage, see the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository for sample code related to Graph Memory and entity/relation extraction.
