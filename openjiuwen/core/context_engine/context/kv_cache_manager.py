# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Optional, Union

from openjiuwen.core.foundation.llm.inference_affinity_model import InferenceAffinityModel
from openjiuwen.core.context_engine.base import ContextWindow


class KVCacheManager:
    def __init__(self, session_id: str):
        self._session_id = session_id
        self._last_context_window: ContextWindow = None

    def release(self, context_window: ContextWindow, **kwargs):
        model = kwargs.get("model")
        if model is None or not isinstance(model, InferenceAffinityModel):
            return

        if not self._last_context_window:
            self._last_context_window = context_window
            return

        messages_released_index = self._find_messages_released_index(context_window)
        if messages_released_index < 0:
            return

        result = model.release(
            model=model.model_config.model_name,
            cache_salt=self._session_id,
            messages=self._last_context_window.get_messages(),
            messages_released_index=messages_released_index,
            tools=self._last_context_window.tools,
        )
        if result:
            self._last_context_window = context_window

    def _find_messages_released_index(self, context_window: ContextWindow):
        old_messages = self._last_context_window.get_messages()
        new_messages = context_window.get_messages()

        for idx, (old_msg, new_msg) in enumerate(zip(old_messages, new_messages)):
            if old_msg is not new_msg:
                return idx

        if len(old_messages) > len(new_messages):
            return len(old_messages) - 1
        return -1