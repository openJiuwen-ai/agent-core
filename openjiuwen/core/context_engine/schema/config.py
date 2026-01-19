# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional
from pydantic import BaseModel, Field


class ContextEngineConfig(BaseModel):
    """
    Configuration for the context engine.

    Attributes
    ----------
    max_context_message_num : int, optional
        Hard upper limit on the total number of messages allowed in any context.
        If None (default), no hard limit is enforced.

    default_window_message_num : int, optional
        Number of most-recent messages to retain when a sliding window is created
        without an explicit token or message count.  Must be > 0 if set; None
        (default) means "unlimited".

    default_window_token_num : int, optional
        Maximum token budget for a sliding window when token-based rather than
        message-based truncation is requested.  If None (default), truncation
        falls back to `default_window_message_num`.
    """

    max_context_message_num: Optional[int] = Field(default=None, gt=0)
    default_window_message_num: Optional[int] = Field(default=None, gt=0)
    default_window_token_num: Optional[int] = Field(default=None, gt=0)
    enable_kv_cache_release: bool = Field(default=False)