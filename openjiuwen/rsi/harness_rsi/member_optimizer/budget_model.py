# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""RSI-local output budgeting at the actual request boundary."""
import json

from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.foundation.llm import Model


class BudgetedRsiModel(Model):
    """Do not reserve more output than fits beside the unchanged input."""

    def _budget_options(self, messages, options):
        options = dict(options)
        requested = options.get("max_tokens")
        if requested is None:
            requested = self.model_config.max_tokens
        if requested is not None and self.model_config.max_tokens is not None:
            requested = min(requested, self.model_config.max_tokens)
        rows = [{"role": "user", "content": messages}] if isinstance(messages, str) else [
            m.model_dump(mode="json", exclude_none=True) if hasattr(m, "model_dump") else m
            for m in messages
        ]
        schemas = [t.model_dump(mode="json") if hasattr(t, "model_dump") else t
                   for t in (options.get("tools") or [])]
        window = ContextUtils.resolve_context_max(
            model_name=self.model_config.model_name,
            fallback_context_window_tokens=self.model_config.context_window,
        )
        # Conservative text-token bound; never silently discard evidence.
        used = sum(len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
                   for value in (rows, schemas)) + 4096
        if (requested is not None and requested <= 0) or used >= window:
            raise ValueError("RSI request has no positive output budget within the configured context window")
        if requested is not None:
            options["max_tokens"] = min(requested, window - used)
        return options

    async def invoke(self, messages, **kwargs):
        return await super().invoke(messages=messages, **self._budget_options(messages, kwargs))

    async def stream(self, messages, **kwargs):
        async for chunk in super().stream(messages=messages, **self._budget_options(messages, kwargs)):
            yield chunk
