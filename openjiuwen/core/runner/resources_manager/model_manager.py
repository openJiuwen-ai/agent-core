# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional
from openjiuwen.core.runner.resources_manager.abstract_manager import AbstractManager
from openjiuwen.core.runner.resources_manager.base import ModelProvider
from openjiuwen.core.session.tracer import decorate_model_with_trace
from openjiuwen.core.foundation.llm import Model


class ModelMgr(AbstractManager["Model"]):

    def __init__(self) -> None:
        super().__init__()

    def add_model(self, model_id: str, model: ModelProvider) -> None:
        self._register_resource_provider(model_id, model)

    def remove_model(self, model_id: str) -> Optional[ModelProvider]:
        return self._unregister_resource_provider(model_id)

    async def get_model(self, model_id: str, session=None, identity: dict | None = None) -> Optional[Model]:
        """Resolve a model and hand the caller a traced copy.

        Decoration is unconditional (baseline behavior): whether a span is
        produced never depends on ``identity``. ``identity`` is an optional
        enrichment dict — caller-side attribution keys (e.g. the controller
        layer's agent_id) stamped on every llm span as gen_ai.* attributes,
        because the session passed here is a bare conversation session
        without an agent card, so the rail's common-info path cannot supply
        them. ``None`` / empty keeps the exact pre-identity wire format
        (anonymous span).
        """
        model = await self._get_resource(model_id)
        return decorate_model_with_trace(model, session, identity=identity)
