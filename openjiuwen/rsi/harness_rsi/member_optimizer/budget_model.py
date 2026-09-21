# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""RSI-local output budgeting at the actual request boundary."""
import json

from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.foundation.llm import Model, ModelRequestConfig
from openjiuwen.rsi.harness_rsi.member_optimizer.model_config import with_rsi_output_budget


class BudgetedRsiModel(Model):
    """Check input size without imposing an RSI output limit."""

    def __init__(self, model_client_config=None, model_config=None, **kwargs):
        request = model_config.model_dump() if model_config is not None else {}
        request = with_rsi_output_budget({"model_request_config": request})["model_request_config"]
        super().__init__(model_client_config=model_client_config,
                         model_config=ModelRequestConfig.model_validate(request), **kwargs)

    def _budget_options(self, messages, options):
        options = dict(options)
        options.pop("max_tokens", None)
        options.pop("max_completion_tokens", None)
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
        if used >= window:
            raise ValueError("RSI request has no positive output budget within the configured context window")
        return options

    async def invoke(self, messages, **kwargs):
        return await super().invoke(messages=messages, **self._budget_options(messages, kwargs))

    async def stream(self, messages, **kwargs):
        async for chunk in super().stream(messages=messages, **self._budget_options(messages, kwargs)):
            yield chunk
