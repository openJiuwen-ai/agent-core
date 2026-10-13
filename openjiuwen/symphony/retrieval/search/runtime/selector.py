from __future__ import annotations

import logging
import re
from abc import abstractmethod
from dataclasses import dataclass
from time import perf_counter
from typing import Callable, Protocol, Sequence

from openjiuwen.symphony.retrieval.common.models import RetrieverNode, RetrieverTrace

from ...llm import ProgressiveLLMClient
from .contracts import TopKSelector
from .render.disclosure import ExposedFragment, SelectableResolution, build_disclosure_messages
from .types import (
    CurrentSubtree,
    ProgressiveRetrieverConfig,
    PromptBundle,
    SearchCursor,
    SelectableTarget,
    SelectionProtocol,
    SelectionResult,
)

LOGGER = logging.getLogger("retrieval.logit_selection.selection")

_ABSTAIN_HINT_RE = re.compile(
    r"(none|no suitable|no relevant|not relevant|unrelated|cannot determine|can't determine|"
    r"无合适|没有合适|无相关|没有相关|不相关|无法判断|无法确定)",
    re.IGNORECASE,
)


class FragmentSelector(Protocol):
    @abstractmethod
    def select(
        self,
        *,
        model: str,
        query_messages: list[dict[str, str]],
        node: RetrieverNode,
        depth: int,
        top_k: int,
        trace: RetrieverTrace,
        fragment: ExposedFragment,
    ) -> tuple[str, list[SelectableResolution]]:
        raise NotImplementedError


@dataclass
class GenerateFragmentSelector:
    generate_fn: Callable[..., tuple[str, list[SelectableResolution]]]

    def select(
        self,
        *,
        model: str,
        query_messages: list[dict[str, str]],
        node: RetrieverNode,
        depth: int,
        top_k: int,
        trace: RetrieverTrace,
        fragment: ExposedFragment,
    ) -> tuple[str, list[SelectableResolution]]:
        return self.generate_fn(
            model=model,
            query_messages=query_messages,
            node=node,
            depth=depth,
            top_k=top_k,
            trace=trace,
            fragment=fragment,
        )


@dataclass
class LogitSelectionFragmentSelector:
    client: ProgressiveLLMClient
    require_single_token_codes: bool
    fallback_mode: str
    generate_selector: GenerateFragmentSelector
    max_candidates: int = 512
    min_probability: float | None = None
    trace_top_n: int = 10

    def select(
        self,
        *,
        model: str,
        query_messages: list[dict[str, str]],
        node: RetrieverNode,
        depth: int,
        top_k: int,
        trace: RetrieverTrace,
        fragment: ExposedFragment,
    ) -> tuple[str, list[SelectableResolution]]:
        backend_name = getattr(self.client, "name", type(self.client).__name__)
        LOGGER.info(
            "fragment selection start mode=logit_selection backend=%s node=%s depth=%d "
            "top_k=%d candidate_count=%d compact=%s",
            backend_name,
            node.node_id,
            int(depth),
            int(top_k),
            len(fragment.candidate_codes),
            bool(fragment.compact_codes_enabled),
        )
        if not self.client.capabilities.candidate_scoring or not fragment.compact_codes_enabled:
            return self._fallback(
                reason=(
                    "backend_unavailable"
                    if not self.client.capabilities.candidate_scoring
                    else "compact_codes_disabled"
                ),
                model=model,
                query_messages=query_messages,
                node=node,
                depth=depth,
                top_k=top_k,
                trace=trace,
                fragment=fragment,
            )
        if len(fragment.candidate_codes) > max(1, int(self.max_candidates)):
            return self._fallback(
                reason=f"candidate_count_exceeds_limit:{len(fragment.candidate_codes)}>{int(self.max_candidates)}",
                model=model,
                query_messages=query_messages,
                node=node,
                depth=depth,
                top_k=top_k,
                trace=trace,
                fragment=fragment,
            )
        trace.record(
            "fragment_logit_selection_requested",
            node_id=node.node_id,
            depth=depth,
            detail={
                "backend": backend_name,
                "candidate_count": len(fragment.candidate_codes),
                "candidate_codes": list(fragment.candidate_codes),
                "candidate_canonical_ids": [
                    fragment.code_to_resolution[code].canonical_id
                    for code in fragment.candidate_codes
                    if code in fragment.code_to_resolution
                ],
                "fragment_fingerprint": fragment.fragment_fingerprint,
                "selection_mode": "logit_selection",
            },
        )
        try:
            scoring_started = perf_counter()
            messages = build_disclosure_messages(fragment=fragment, query_messages=query_messages)
            scoring_result = self.client.score_candidate_codes(
                model=model,
                messages=messages,
                candidate_codes=fragment.candidate_codes or tuple(fragment.code_to_resolution.keys()),
                code_to_canonical_id={
                    code: resolution.canonical_id for code, resolution in fragment.code_to_resolution.items()
                },
                top_k=max(1, len(fragment.candidate_codes or tuple(fragment.code_to_resolution.keys()))),
                require_single_token_codes=self.require_single_token_codes,
            )
            scores = list(scoring_result.scores)
            scoring_total_ms = round((perf_counter() - scoring_started) * 1000.0, 3)
        except Exception as exc:
            LOGGER.warning(
                "fragment selection scoring_failed backend=%s node=%s depth=%d reason=%s",
                backend_name,
                node.node_id,
                int(depth),
                exc,
            )
            return self._fallback(
                reason=str(exc),
                model=model,
                query_messages=query_messages,
                node=node,
                depth=depth,
                top_k=top_k,
                trace=trace,
                fragment=fragment,
            )
        top_scores = list(scores[: max(1, int(self.trace_top_n))])
        trace.record(
            "fragment_logit_selection_completed",
            node_id=node.node_id,
            depth=depth,
            detail={
                "backend": backend_name,
                "candidate_count": len(fragment.candidate_codes),
                "fragment_fingerprint": fragment.fragment_fingerprint,
                "top_codes": [item.code for item in top_scores],
                "top_canonical_ids": [item.canonical_id for item in top_scores],
                "top_logits": [item.logit for item in top_scores],
                "top_probabilities": [item.probability for item in top_scores],
                "latency_breakdown": dict(scoring_result.latency_breakdown, selector_total_ms=scoring_total_ms),
            },
        )
        threshold = None if self.min_probability is None else float(self.min_probability)
        filtered_scores = [item for item in scores if threshold is None or float(item.probability) >= threshold]
        selected = [
            fragment.code_to_resolution[item.code]
            for item in filtered_scores[: max(1, int(top_k))]
            if item.code in fragment.code_to_resolution
        ]
        raw_output = "\n".join(item.code for item in filtered_scores[: max(1, int(top_k))]) if selected else "0"
        LOGGER.info(
            "fragment selection complete backend=%s node=%s depth=%d selected_count=%d raw_output=%s",
            backend_name,
            node.node_id,
            int(depth),
            len(selected),
            raw_output,
        )
        return raw_output, selected

    def _fallback(
        self,
        *,
        reason: str,
        model: str,
        query_messages: list[dict[str, str]],
        node: RetrieverNode,
        depth: int,
        top_k: int,
        trace: RetrieverTrace,
        fragment: ExposedFragment,
    ) -> tuple[str, list[SelectableResolution]]:
        trace.record(
            "fragment_logit_selection_fallback",
            node_id=node.node_id,
            depth=depth,
            detail={
                "backend": getattr(self.client, "name", type(self.client).__name__),
                "fallback_mode": self.fallback_mode,
                "reason": reason,
                "fragment_fingerprint": fragment.fragment_fingerprint,
            },
        )
        mode = str(self.fallback_mode or "generate").strip().lower()
        LOGGER.warning(
            "fragment selection fallback backend=%s node=%s depth=%d mode=%s reason=%s",
            getattr(self.client, "name", type(self.client).__name__),
            node.node_id,
            int(depth),
            mode,
            reason,
        )
        if mode == "abstain":
            return "0", []
        if mode == "error":
            raise RuntimeError(reason)
        return self.generate_selector.select(
            model=model,
            query_messages=query_messages,
            node=node,
            depth=depth,
            top_k=top_k,
            trace=trace,
            fragment=fragment,
        )


ScoringFragmentSelector = LogitSelectionFragmentSelector
SoftmaxFragmentSelector = LogitSelectionFragmentSelector


@dataclass
class DefaultTopKSelector(TopKSelector):
    config: ProgressiveRetrieverConfig
    build_generate_selector: Callable[[], GenerateFragmentSelector | LogitSelectionFragmentSelector]

    def build_protocol(self, *, subtree: CurrentSubtree) -> SelectionProtocol:
        return SelectionProtocol(
            compact_codes_enabled=bool(subtree.fragment.compact_codes_enabled),
            candidate_codes=tuple(subtree.fragment.candidate_codes),
            code_width=int(subtree.fragment.code_width),
            abstain_token="0",
        )

    def select_topk(
        self,
        *,
        model: str,
        cursor: SearchCursor,
        query_messages: Sequence[dict[str, str]],
        subtree: CurrentSubtree,
        prompt: PromptBundle,
        trace: RetrieverTrace,
    ) -> SelectionResult:
        selector = self.build_generate_selector()
        output, selected = selector.select(
            model=model,
            query_messages=list(query_messages),
            node=cursor.node,
            depth=cursor.depth,
            top_k=cursor.top_k,
            trace=trace,
            fragment=subtree.fragment,
        )
        return SelectionResult(
            raw_output=output,
            selected_targets=tuple(SelectableTarget(resolution=item) for item in selected),
            is_abstain=not selected and self._is_abstain_output(output),
        )

    @staticmethod
    def _is_abstain_output(output: str) -> bool:
        text = str(output or "").strip()
        if not text:
            return False
        if text == "0":
            return True
        return _ABSTAIN_HINT_RE.search(text) is not None


__all__ = [
    "DefaultTopKSelector",
    "FragmentSelector",
    "GenerateFragmentSelector",
    "LogitSelectionFragmentSelector",
    "ScoringFragmentSelector",
    "SoftmaxFragmentSelector",
]
