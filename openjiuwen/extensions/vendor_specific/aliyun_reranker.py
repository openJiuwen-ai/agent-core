# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Deprecated, please use openjiuwen.core.retrieval.DashscopeReranker instead
"""

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.reranker.dashscope_reranker import DashscopeReranker

logger.warning("AliyunReranker is deprecated, please use openjiuwen.core.retrieval.DashscopeReranker instead.")
AliyunReranker = DashscopeReranker

__all__ = ["AliyunReranker"]
