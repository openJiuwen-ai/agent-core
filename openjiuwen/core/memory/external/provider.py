# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Memory provider interface."""

from abc import ABC
from abc import abstractmethod
from typing import Any


class MemoryProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: 
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """Check if configured and ready. No network calls."""
        pass
    
    @abstractmethod
    async def initialize(self, **kwargs) -> None:
        pass
    
    @abstractmethod
    def get_tool_schemas(self) -> list[dict[str, Any]]:
        pass
    
    @abstractmethod
    async def handle_tool_call(self, tool_name: str, args: dict) -> str:
        pass
    
    @abstractmethod
    async def prefetch(self, query: str, **kwargs) -> str:
        pass
    
    @abstractmethod
    async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs) -> None:
        pass
    
    def system_prompt_block(self) -> str:
        """Return each provider's guide of system prompts.."""
        return ""
    
    async def shutdown(self) -> None:
        pass
    
    async def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        pass
    
    @property
    def is_initialized(self) -> bool:
        return False