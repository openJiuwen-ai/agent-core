# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve the safest available token counter for a model."""

from __future__ import annotations

from openjiuwen.core.context_engine.token.base import TokenCounter
from openjiuwen.core.context_engine.token.native_tokenizer_counter import NativeTokenizerCounter
from openjiuwen.core.context_engine.token.string_length_counter import StringLengthCounter
from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter
from openjiuwen.core.context_engine.token.tiktoken_model_counter import TiktokenModelCounter
from openjiuwen.core.context_engine.token.tokenizer_manager import TokenizerArtifactManager
from openjiuwen.core.context_engine.token.tokenizer_registry import TokenizerRegistry
from openjiuwen.core.context_engine.token.tokenizer_spec import CompatibleTokenizerSpec, TokenizerSpec


class TokenizerSelector:
    """Select a native artifact, then tiktoken or string-length fallback.

    ``allow_tiktoken_fallback=False`` is used by ContextEngine and warm-up
    callers that must never turn a missing native artifact into a tiktoken
    initialization or a remote resolution attempt.
    """

    def __init__(
        self,
        *,
        provider: str = "",
        model: str = "",
        spec: TokenizerSpec | dict | None = None,
        registry: TokenizerRegistry | None = None,
        manager: TokenizerArtifactManager | None = None,
        allow_tiktoken_fallback: bool = True,
    ) -> None:
        self.provider = provider
        self.model = model
        self.registry = registry or TokenizerRegistry()
        self.manager = manager or TokenizerArtifactManager()
        self.allow_tiktoken_fallback = allow_tiktoken_fallback
        self._registry_match_kind = "exact"
        if spec is not None:
            self.spec = self._normalize_spec(spec)
        else:
            registry_match = self.registry.resolve_match(provider, model)
            self.spec = registry_match.spec if registry_match is not None else None
            if registry_match is not None:
                self._registry_match_kind = registry_match.kind

    def select(self) -> TokenCounter:
        exact_spec = self.spec
        if self.allow_tiktoken_fallback and exact_spec is None:
            if not self.provider and not self.model:
                # Preserve the historical default for contexts that have no model
                # configuration at all.
                return TiktokenCounter()

        if exact_spec is not None:
            is_family_match = self._registry_match_kind == "family"
            exact = self._native_counter(
                exact_spec,
                source="family_tokenizer_fallback" if is_family_match else "native_tokenizer",
                fallback_reason="model_variant_tokenizer_family" if is_family_match else None,
                fallback_tokenizer_model=exact_spec.model if is_family_match else None,
            )
            if exact is not None:
                return exact

            # The application-level model maps intentionally provide one
            # canonical family fallback. Keep selection one-hop even if an
            # older configuration accidentally contains a longer list.
            for candidate in exact_spec.compatible_fallbacks[:1]:
                family = self._native_counter(
                    candidate,
                    source="family_tokenizer_fallback",
                    fallback_reason="target_tokenizer_unavailable",
                    fallback_tokenizer_model=candidate.model,
                )
                if family is not None:
                    return family

        # A registry entry may be absent from the call-site spec, while the
        # selector still needs to provide a useful default for OpenAI aliases.
        if self.allow_tiktoken_fallback and self._prefers_tiktoken(exact_spec):
            return TiktokenCounter(model=self.model)

        if self.allow_tiktoken_fallback and self._tiktoken_available():
            return TiktokenCounter(
                model=self.model,
                source_override="tiktoken_fallback",
                fallback_reason="native_tokenizer_unavailable",
            )

        if exact_spec is None and not (self.provider or self.model) and not self.allow_tiktoken_fallback:
            fallback_reason = "tiktoken_disabled"
        elif exact_spec is None and (self.provider or self.model):
            fallback_reason = "model_tokenizer_spec_missing"
        else:
            fallback_reason = "native_tokenizer_unavailable"

        return StringLengthCounter(
            model=self.model,
            fallback_reason=fallback_reason,
        )

    def _native_counter(
        self,
        spec: TokenizerSpec | CompatibleTokenizerSpec,
        *,
        source: str,
        fallback_reason: str | None = None,
        fallback_tokenizer_model: str | None = None,
    ) -> TokenCounter | None:
        # Artifact resolution is an optional telemetry path.  A broken cache,
        # permission error, or optional backend failure must not prevent the
        # selector from reaching its fallback chain.
        try:
            artifact_path = self.manager.resolve(spec)
        except Exception:
            return None
        if artifact_path is None:
            return None
        try:
            engine = str(getattr(spec, "engine", "auto") or "auto").strip().casefold()
            if engine == "tiktoken":
                return TiktokenModelCounter(
                    artifact_path,
                    model=self.model,
                    tokenizer_model=getattr(spec, "tokenizer_id", None) or getattr(spec, "model", None),
                    measurement_source=source,
                    fallback_reason=fallback_reason,
                    fallback_tokenizer_model=fallback_tokenizer_model,
                )
            return NativeTokenizerCounter(
                artifact_path,
                model=self.model,
                tokenizer_model=getattr(spec, "tokenizer_id", None) or getattr(spec, "model", None),
                measurement_source=source,
                fallback_reason=fallback_reason,
                fallback_tokenizer_model=fallback_tokenizer_model,
            )
        except Exception:
            return None

    @staticmethod
    def _normalize_spec(spec: TokenizerSpec | dict) -> TokenizerSpec:
        return spec if isinstance(spec, TokenizerSpec) else TokenizerSpec.model_validate(spec)

    def _prefers_tiktoken(self, spec: TokenizerSpec | None) -> bool:
        if spec is not None and spec.engine == "tokenizers":
            return False
        return str(self.provider or "").strip().casefold() in {
            "openai",
            "openaicompatible",
            "openai-compatible",
            "openrouter",
        }

    @staticmethod
    def _tiktoken_available() -> bool:
        try:
            import tiktoken  # noqa: F401
        except ImportError:
            return False
        return True


__all__ = ["TokenizerSelector"]
