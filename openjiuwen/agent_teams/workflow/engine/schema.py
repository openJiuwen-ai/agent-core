# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Schema resolution & validation for ``agent(..., schema=...)``.

Three accepted forms — the return type follows the schema kind:

* ``None``                  -> agent returns ``str``
* a JSON-Schema ``dict``    -> agent returns a validated ``dict``   (interop DEFAULT)
* a ``pydantic.BaseModel``  -> agent returns a validated instance   (type-safe path)

**Why dict is the default:** JSON Schema literals are the neutral interchange
format for JS<->Python interop (both sides consume them directly; the mock
synthesises conforming objects from them). A raw-dict schema is deep-validated
with ``jsonschema`` when installed, else passed through.

**When to use a Pydantic model:** pure-Python flows that want attribute access
(``r.name``), nested typing, and static type-checker support. The model is
lowered to JSON Schema for the backend, then ``coerce`` rehydrates a validated
instance. ``agent`` carries ``@overload``s so ``schema=MyModel`` statically
narrows the return to ``MyModel | None`` (vs ``dict | None`` / ``str | None``).
"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from .errors import SchemaError

# Soft imports: pydantic/jsonschema are optional at runtime, but the type checker
# always sees the real types (TYPE_CHECKING branch) so narrowing/attrs resolve.
if TYPE_CHECKING:
    from pydantic import BaseModel

    _HAS_PYDANTIC = True
else:
    try:
        from pydantic import BaseModel

        _HAS_PYDANTIC = True
    except Exception:  # pragma: no cover - exercised only without pydantic
        BaseModel = None
        _HAS_PYDANTIC = False

try:
    import jsonschema
except Exception:  # pragma: no cover - exercised only without jsonschema
    jsonschema = None


def is_model(schema: Any) -> bool:
    """True iff *schema* is a pydantic ``BaseModel`` subclass."""
    return _HAS_PYDANTIC and isinstance(schema, type) and issubclass(schema, BaseModel)


# Swarmflow scripts are LLM-authored and routinely write ``items`` as a list.
# Two shapes reach us: the draft-07 tuple form (a list of subschemas, which
# 2020-12 spells ``prefixItems``) and the shorthand ``"items": ["string"]``
# (a list of type names meaning "array of strings"). Neither is valid under the
# 2020-12 metaschema that ``jsonschema.validate`` applies by default, so the
# structured-output tool raised ``SchemaError`` on every submission before the
# payload was even looked at. Normalizing here — the one boundary every dict
# schema passes through — fixes the tool schema and ``coerce`` at once.
_LEGACY_SCHEMA_DIALECTS = ("draft-03", "draft-04", "draft-06", "draft-07")

# Keywords whose value is a single subschema / an array of subschemas / a map of
# name -> subschema. Only these are walked; other values (``enum``, ``const``,
# ``default``, ...) may hold arbitrary JSON that must not be rewritten.
_SINGLE_SUBSCHEMA_KEYWORDS = (
    "additionalProperties",
    "contains",
    "propertyNames",
    "not",
    "if",
    "then",
    "else",
    "unevaluatedItems",
    "unevaluatedProperties",
    "contentSchema",
)
_MAPPED_SUBSCHEMA_KEYWORDS = ("properties", "patternProperties", "dependentSchemas", "$defs", "definitions")
_LIST_SUBSCHEMA_KEYWORDS = ("prefixItems", "allOf", "anyOf", "oneOf")


def _declares_legacy_dialect(schema: dict) -> bool:
    """True iff this schema opts into a pre-2020-12 dialect via ``$schema``."""
    dialect = schema.get("$schema")
    return isinstance(dialect, str) and any(marker in dialect for marker in _LEGACY_SCHEMA_DIALECTS)


def _items_from_type_names(types: list[str]) -> dict:
    """Lower ``items: ["string"]`` shorthand to a 2020-12 ``items`` schema."""
    unique = list(dict.fromkeys(types))
    return {"type": unique[0] if len(unique) == 1 else unique}


def _normalize_tuple_items(node: Any) -> Any:
    """Normalize list-valued ``items`` into a 2020-12-valid shape, recursively.

    * a list of subschemas (draft-07 tuple form) -> ``prefixItems`` (+
      ``additionalItems`` -> ``items`` for the tail);
    * a list of type names (``["string"]``) -> ``items: {"type": ...}``;
    * a mixed list -> each element is lowered to a schema and kept positional.

    Returns a deep-copied tree so the caller's schema is never mutated. A node
    that explicitly declares a legacy dialect is left untouched — its own
    validator accepts the tuple form, and rewriting it would weaken that
    dialect's validation because ``prefixItems`` is an unknown keyword there.
    Non-dict nodes (a boolean schema, a plain value) pass through unchanged.
    """
    if not isinstance(node, dict):
        return node
    if _declares_legacy_dialect(node):
        return copy.deepcopy(node)

    schema = node
    out = dict(schema)
    items = schema.get("items")
    if isinstance(items, list):
        out.pop("items", None)
        out.pop("additionalItems", None)
        if items and all(isinstance(item, str) for item in items):
            out["items"] = _items_from_type_names(items)
        else:
            # Positional form. Type-name shorthand mixed with real subschemas is
            # lowered element-wise so the result is still a valid schema list.
            positional = [
                _items_from_type_names([item]) if isinstance(item, str) else _normalize_tuple_items(item)
                for item in items
            ]
            if "prefixItems" not in out:
                out["prefixItems"] = positional
            if "additionalItems" in schema:
                out["items"] = _normalize_tuple_items(schema["additionalItems"])
    else:
        # ``additionalItems`` is only meaningful next to the tuple form.
        out.pop("additionalItems", None)
        if items is not None:
            out["items"] = _normalize_tuple_items(items)

    for key in _SINGLE_SUBSCHEMA_KEYWORDS:
        value = schema.get(key)
        if isinstance(value, dict):
            out[key] = _normalize_tuple_items(value)
    for key in _MAPPED_SUBSCHEMA_KEYWORDS:
        value = schema.get(key)
        if isinstance(value, dict):
            out[key] = {name: _normalize_tuple_items(sub) for name, sub in value.items()}
    for key in _LIST_SUBSCHEMA_KEYWORDS:
        value = schema.get(key)
        if isinstance(value, list):
            out[key] = [_normalize_tuple_items(sub) for sub in value]
    return out


def resolve_schema(schema: Any) -> tuple[dict | None, type | None]:
    """Return ``(json_schema_dict | None, model_class | None)``.

    The backend only ever sees the JSON-Schema dict; the model class (if any)
    is kept so :func:`coerce` can rehydrate a typed instance from the backend's
    structured output.
    """
    if schema is None:
        return None, None
    if is_model(schema):
        return schema.model_json_schema(), schema
    if isinstance(schema, dict):
        return _normalize_tuple_items(schema), None
    raise SchemaError(
        f"schema must be None, a pydantic BaseModel subclass, or a JSON-Schema "
        f"dict; got {type(schema).__name__}"
    )


def coerce(raw: Any, json_schema: dict | None, model: type | None) -> Any:
    """Validate the backend's structured output and coerce to the return type.

    * model present  -> ``model.model_validate(raw)`` (raises on mismatch)
    * dict schema     -> ``jsonschema.validate`` if available, return ``raw``
    * neither         -> return ``raw`` unchanged
    """
    if model is not None:
        return model.model_validate(raw)
    if isinstance(json_schema, dict) and jsonschema is not None:
        jsonschema.validate(raw, json_schema)  # raises jsonschema.ValidationError
    return raw


def to_jsonable(value: Any) -> Any:
    """Best-effort conversion of an agent result to a JSON-serialisable value.

    Used by the journal. Pydantic instances are dumped in JSON mode; everything
    else is returned as-is (the caller only ever stores str / dict / None).
    """
    if _HAS_PYDANTIC and isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value
