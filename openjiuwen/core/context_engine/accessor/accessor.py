#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.context_engine.accessor.history import ConversationHistory
from openjiuwen.core.context_engine.base import ContextOwner
from openjiuwen.core.context_engine.config import ContextEngineConfig


class ContextAccessor:
    def __init__(self, config: ContextEngineConfig):
        self._chat_history_manager: dict = {}
        self._config = config

    def history(self, owner: ContextOwner) -> ConversationHistory:
        history = self._chat_history_manager.get(owner)
        if not history:
            history = ConversationHistory(self._config)
            self._chat_history_manager[owner] = history
        return history

    def clear_context(self, owner: ContextOwner):
        """clear history messages by owner"""
        for own in list(self._chat_history_manager.keys()):
            if own.agent_id == owner.agent_id and own.session_id == owner.session_id:
                del self._chat_history_manager[own]
