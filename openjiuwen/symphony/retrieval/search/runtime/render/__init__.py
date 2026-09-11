from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .default import DefaultSubtreeRenderer as DefaultSubtreeRenderer
    from .disclosure import DisclosureConfig as DisclosureConfig
    from .disclosure import ExposedFragment as ExposedFragment
    from .disclosure import ExposedNode as ExposedNode
    from .disclosure import SelectableResolution as SelectableResolution
    from .disclosure import build_disclosure_messages as build_disclosure_messages
    from .disclosure import build_exposed_fragment as build_exposed_fragment
    from .disclosure import parse_selected_codes as parse_selected_codes

__all__ = [
    "DefaultSubtreeRenderer",
    "DisclosureConfig",
    "ExposedFragment",
    "ExposedNode",
    "SelectableResolution",
    "build_disclosure_messages",
    "build_exposed_fragment",
    "parse_selected_codes",
]


def __getattr__(name: str):
    if name == "DefaultSubtreeRenderer":
        from .default import DefaultSubtreeRenderer

        return DefaultSubtreeRenderer
    if name in {
        "DisclosureConfig",
        "ExposedFragment",
        "ExposedNode",
        "SelectableResolution",
        "build_disclosure_messages",
        "build_exposed_fragment",
        "parse_selected_codes",
    }:
        from . import disclosure

        return getattr(disclosure, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
