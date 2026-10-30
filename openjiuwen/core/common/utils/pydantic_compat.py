# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compatibility helpers for pydantic models restored from older pickles."""

from __future__ import annotations

from typing import Any, Dict


class RestoreFieldDefaultsOnUnpickle:
    """Mixin for persisted models that may gain fields over time.

    ``BaseModel.__setstate__`` restores ``__dict__`` verbatim, so an instance
    pickled before a field was added has no value for it and reading the field
    raises ``AttributeError``. After unpickling, every declared field missing
    from ``__dict__`` gets its default; required fields are left untouched.

    Put it before ``BaseModel`` in the bases so the restore runs after the
    pydantic state is applied.
    """

    def __setstate__(self, state: Dict[Any, Any]) -> None:
        super().__setstate__(state)  # type: ignore[misc]  # pylint: disable=no-member
        values = self.__dict__
        for name, info in type(self).model_fields.items():  # type: ignore[attr-defined]  # pylint: disable=no-member
            if name not in values and not info.is_required():
                values[name] = info.get_default(call_default_factory=True)


__all__ = ["RestoreFieldDefaultsOnUnpickle"]
