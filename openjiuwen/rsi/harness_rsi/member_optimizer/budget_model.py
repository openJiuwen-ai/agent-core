# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""RSI request policy without locally imposed model capacity limits."""
from openjiuwen.core.foundation.llm import Model, ModelRequestConfig
from openjiuwen.rsi.harness_rsi.member_optimizer.model_config import with_rsi_output_budget


class BudgetedRsiModel(Model):
    """Leave capacity validation to the provider and preserve input evidence."""

    def __init__(self, model_client_config=None, model_config=None, **kwargs):
        request = model_config.model_dump() if model_config is not None else {}
        request = with_rsi_output_budget({"model_request_config": request})["model_request_config"]
        super().__init__(model_client_config=model_client_config,
                         model_config=ModelRequestConfig.model_validate(request), **kwargs)

    def _budget_options(self, messages, options):
        options = dict(options)
        options.pop("max_tokens", None)
        options.pop("max_completion_tokens", None)
        return options

    async def invoke(self, messages, **kwargs):
        return await super().invoke(messages=messages, **self._budget_options(messages, kwargs))

    async def stream(self, messages, **kwargs):
        async for chunk in super().stream(messages=messages, **self._budget_options(messages, kwargs)):
            yield chunk
