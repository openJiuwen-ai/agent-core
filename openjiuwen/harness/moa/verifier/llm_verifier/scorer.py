# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MoA 调用内进展评分适配，参考 LLM as a verifier。"""

from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Callable
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.harness.moa._validation import validate_positive_int
from openjiuwen.harness.moa.config import MoAConfig
from openjiuwen.harness.moa.state import MoAState
from openjiuwen.harness.moa.verifier.llm_verifier.progress import ProgressTracker, validate_chat_model
from openjiuwen.harness.moa.verifier.redaction import redact


async def _call(callback: Callable[..., Any], *args: Any) -> Any:
    """Keep async callbacks on the current loop and adapt sync callbacks."""
    try:
        if inspect.iscoroutinefunction(callback) or inspect.iscoroutinefunction(getattr(callback, "__call__", None)):
            return await callback(*args)
        value = await asyncio.to_thread(callback, *args)
        return await value if inspect.isawaitable(value) else value
    except BaseError:
        raise
    except Exception as exc:
        raise build_error(
            StatusCode.DEEPAGENT_RUNTIME_ERROR, error_msg="MoA progress scorer call failed", cause=exc
        ) from exc


class LlmVerifierProgressScorer:
    """Create a tracker per invocation and score through a jiuwen Model.

    A custom ``tracker_factory`` is called synchronously by ``for_invoke``
    and may supply either a synchronous or asynchronous ``update`` method.
    Its updates must append text before evaluating it, including on failure.
    """

    def __init__(
        self,
        *,
        model: Model | None = None,
        n_evaluations: int = 3,
        tracker_factory: Any = None,
        client: Any = None,
    ) -> None:
        if isinstance(model, str) or client is not None:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="Pass a constructed jiuwen Model as model=verifier_model; "
                "model-name strings and SDK client arguments are no longer supported",
            )
        validate_positive_int("n_evaluations", n_evaluations)
        if tracker_factory is None:
            if model is None:
                raise build_error(
                    StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                    error_msg="LlmVerifierProgressScorer requires model=verifier_model (a jiuwen Model)",
                )
            validate_chat_model(model)
            tracker_factory = ProgressTracker
        self._factory = tracker_factory
        self._model = model
        self._n_evaluations = n_evaluations

    def for_invoke(self, problem: str, n_evaluations: int | None = None) -> _TrackerScore:
        """Synchronously create a tracker isolated from other invocations."""
        repeats = self._n_evaluations if n_evaluations is None else n_evaluations
        validate_positive_int("n_evaluations", repeats)
        kwargs: dict[str, Any] = {"n_evaluations": repeats}
        if self._model is not None:
            kwargs["model"] = self._model
        try:
            return _TrackerScore(self._factory(problem[:8_000], **kwargs))
        except BaseError:
            raise
        except Exception as exc:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR, error_msg="MoA progress tracker initialization failed", cause=exc
            ) from exc


class _TrackerScore:
    def __init__(self, tracker: Any) -> None:
        self._tracker = tracker
        self._seen = 0

    async def __call__(self, problem: str, steps: tuple[str, ...], n_evaluations: int) -> float:
        del problem, n_evaluations
        seen = self._seen
        new_steps = steps[seen:]
        # Trackers append text before evaluation; even failed scores consume
        # this prefix so the next checkpoint must not replay these steps.
        self._seen = len(steps)
        try:
            value = await _call(self._tracker.update, "\n\n".join(new_steps))
            score = float(value)
            rows = getattr(self._tracker.result(), "per_rep_scores", None) or []
            if rows and all(not row or row[-1] is None for row in rows):
                raise build_error(
                    StatusCode.DEEPAGENT_RUNTIME_ERROR, error_msg="verifier returned no valid progress scores"
                )
            return score
        except BaseError:
            raise
        except Exception as exc:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR, error_msg="MoA progress tracker returned invalid scores", cause=exc
            ) from exc


async def score_progress(config: MoAConfig, state: MoAState) -> float | None:
    """Create an invocation-local scorer and validate a checkpoint score."""
    try:
        problem = redact(state.problem[:8_000])
        scorer = state.scorer
        if scorer is None:
            scorer = config.scorer
            if callable(getattr(type(scorer), "for_invoke", None)):
                factory = scorer.for_invoke
                if "n_evaluations" in inspect.signature(factory).parameters:
                    scorer = factory(problem, n_evaluations=config.verifier.n_evaluations)
                else:
                    scorer = factory(problem)
            state.scorer = scorer
        arguments = (problem, tuple(state.steps), config.verifier.n_evaluations)
        value = await _call(scorer, *arguments)
        score = float(getattr(value, "value", value))
        return score if math.isfinite(score) and 0 <= score <= 1 else None
    except Exception:
        return None
