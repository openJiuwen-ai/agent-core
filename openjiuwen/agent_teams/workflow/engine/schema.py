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
        return schema, None
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
