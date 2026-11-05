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

        ``identity`` carries caller-side attribution keys (e.g. the controller
        layer's agent_id) that embedding applications want stamped on every
        llm span as gen_ai.agent.* — the session they pass here is a bare
        conversation session without an agent card, so the rail's common-info
        path cannot supply them. ``None`` / empty (all pre-existing callers)
        skips decoration entirely and returns the bare model: core-internal
        callers never pass identity, and decorating them would mint anonymous
        llm spans that carry no attribution and double-count against existing
        bridge/shell payloads.
        """
        model = await self._get_resource(model_id)
        if not identity:
            return model
        return decorate_model_with_trace(model, session, identity=identity)
