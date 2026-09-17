# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Model-to-tokenizer registry with conservative family matching.

Exact provider/model entries always win. When an exact entry is not present,
the registry may resolve a model variant to the longest registered base model
at a separator boundary (or to an explicitly declared ``family``). This keeps
model aliases such as ``base-thinking`` and ``base_lora`` on the same tokenizer
without treating arbitrary substrings as compatible.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from openjiuwen.core.context_engine.token.tokenizer_spec import TokenizerSpec


def _key(provider: str | None, model: str | None) -> tuple[str, str]:
    return (
        str(provider or "").strip().casefold(),
        str(model or "").strip().casefold(),
    )


_VARIANT_SEPARATORS = frozenset({"_", "-", ":", "."})


def _variant_boundary_match(base: str, requested: str) -> bool:
    """Return whether ``requested`` is a delimited variant of ``base``."""
    if not base or requested == base or not requested.startswith(base):
        return False
    return len(requested) > len(base) and requested[len(base)] in _VARIANT_SEPARATORS


@dataclass(frozen=True)
class TokenizerRegistryMatch:
    """A registry result and whether it was exact or family-derived."""

    spec: TokenizerSpec
    kind: str


class TokenizerRegistry:
    """In-process registry for exact entries and safe family fallbacks."""

    def __init__(self, specs: Iterable[TokenizerSpec | dict] | None = None) -> None:
        self._specs: dict[tuple[str, str], TokenizerSpec] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: TokenizerSpec | dict) -> TokenizerSpec:
        normalized = spec if isinstance(spec, TokenizerSpec) else TokenizerSpec.model_validate(spec)
        self._specs[_key(normalized.provider, normalized.model)] = normalized
        return normalized

    def resolve(self, provider: str, model: str) -> TokenizerSpec | None:
        match = self.resolve_match(provider, model)
        return match.spec if match is not None else None

    def resolve_match(self, provider: str, model: str) -> TokenizerRegistryMatch | None:
        """Resolve exact, then an unambiguous model-family entry.

        Family matching is restricted to the same provider and to a delimiter
        boundary. The longest candidate wins. A tie between different specs is
        considered ambiguous and deliberately returns no match.
        """
        provider_key, model_key = _key(provider, model)
        exact = self._specs.get((provider_key, model_key))
        if exact is not None:
            return TokenizerRegistryMatch(spec=exact, kind="exact")

        candidates: dict[tuple[str, str], tuple[int, TokenizerSpec]] = {}
        for (candidate_provider, candidate_model), spec in self._specs.items():
            if candidate_provider != provider_key:
                continue

            candidate_lengths: list[int] = []
            if _variant_boundary_match(candidate_model, model_key):
                candidate_lengths.append(len(candidate_model))

            family = str(spec.family or "").strip().casefold()
            if family and (
                model_key == family
                or _variant_boundary_match(family, model_key)
            ):
                candidate_lengths.append(len(family))

            if not candidate_lengths:
                continue
            identity = (candidate_provider, candidate_model)
            best_length = max(candidate_lengths)
            previous = candidates.get(identity)
            if previous is None or best_length > previous[0]:
                candidates[identity] = (best_length, spec)

        if not candidates:
            return None
        longest = max(length for length, _ in candidates.values())
        matches = [spec for length, spec in candidates.values() if length == longest]
        if len(matches) != 1:
            return None
        return TokenizerRegistryMatch(spec=matches[0], kind="family")

    def clear(self) -> None:
        self._specs.clear()

    def __len__(self) -> int:
        return len(self._specs)


__all__ = ["TokenizerRegistry", "TokenizerRegistryMatch"]
