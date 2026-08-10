# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import uuid
from typing import Mapping, Optional, Any

from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.schema.config import ProviderType


class HuaweiMaasModelClient(OpenAIModelClient):
    """Huawei MaaS Model Client: Injects x-span-id for each request for tracing."""

    __client_name__ = ProviderType.HuaweiMaas.value

    @classmethod
    def _build_request_headers(
        cls,
        base_headers: Optional[Mapping[str, Any]],
        request_headers: Optional[Mapping[str, Any]],
    ) -> dict[str, str]:
        headers = super()._build_request_headers(base_headers, request_headers)
        headers["x-span-id"] = uuid.uuid4().hex
        return headers

    def _get_client_name(self) -> str:
        return "Huawei MaaS client"
