from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .builder import TreeBuilder


class TreeBuilderMixin:
    """Typing bridge for behavior split across TreeBuilder mixins."""

    if TYPE_CHECKING:

        def __getattr__(self, name: str) -> Any:
            raise AttributeError(name)

    def _tree_builder(self) -> TreeBuilder:
        return cast("TreeBuilder", self)
