# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Load configuration for embedding, reranker, and query rewriter (LLM).
"""

import os
from pathlib import Path

import dotenv

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.mode_info import BaseModelInfo, ModelConfig
from openjiuwen.core.retrieval.common.config import EmbeddingConfig, RerankerConfig

env_file = Path(__file__).parent / ".env"
if not env_file.exists():
    raise FileNotFoundError("Please supply your .env file based on the .env.example provided")
dotenv.load_dotenv(str(env_file), override=True)


EMBEDDING_CONFIG = EmbeddingConfig(
    model_name=os.environ["EMBEDDING_MODEL"],
    base_url=os.environ["EMBEDDING_API_BASE"],
    api_key=os.environ["EMBEDDING_API_KEY"],
)

try:
    RERANKER_CONFIG = RerankerConfig(
        model=os.environ["RERANKER_MODEL"],
        api_base=os.environ["RERANKER_API_BASE"],
        api_key=os.environ["RERANKER_API_KEY"],
    )
except Exception as e:
    logger.error("Reranker not configured: %r", e)
    RERANKER_CONFIG = None

try:
    from utils.find_token import load_tokens_from_huggingface, load_tokens_from_tiktoken

    model_name = os.environ["CHAT_RERANKER_MODEL"]
    try:
        yes_no_ids = load_tokens_from_tiktoken(model_name).values()
    except KeyError:
        yes_no_ids = load_tokens_from_huggingface(model_name).values()

    CHAT_RERANKER_CONFIG = RerankerConfig(
        model=model_name,
        api_base=os.environ["CHAT_RERANKER_API_BASE"],
        api_key=os.environ["CHAT_RERANKER_API_KEY"],
        yes_no_ids=yes_no_ids,
    )
except Exception as e:
    logger.error("Chat reranker not configured: %r", e)
    CHAT_RERANKER_CONFIG = RERANKER_CONFIG

try:
    MULTIMODAL_EMBEDDING_CONFIG = EmbeddingConfig(
        model_name=os.environ["MULTIMODAL_EMBEDDING_MODEL"],
        base_url=os.environ["MULTIMODAL_EMBEDDING_API_BASE"],
        api_key=os.environ["MULTIMODAL_EMBEDDING_API_KEY"],
    )
except Exception as e:
    logger.error("Multimodal embedding not configured: %r", e)
    MULTIMODAL_EMBEDDING_CONFIG = EMBEDDING_CONFIG

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")

# Query Rewriter (LLM): used by showcase_query_rewriter.py; fallback to API_BASE/API_KEY/MODEL_NAME/MODEL_PROVIDER
try:
    _qr_api_base = os.environ.get("QR_LLM_API_BASE") or os.environ.get("API_BASE", "")
    _qr_api_key = os.environ.get("QR_LLM_API_KEY") or os.environ.get("API_KEY", "")
    _qr_model = os.environ.get("QR_LLM_MODEL") or os.environ.get("MODEL_NAME", "")
    _qr_provider = os.environ.get("QR_LLM_PROVIDER") or os.environ.get("MODEL_PROVIDER", "OpenAI")
    if _qr_api_base and _qr_model:
        QR_LLM_MODEL_CONFIG = ModelConfig(
            model_provider=_qr_provider,
            model_info=BaseModelInfo(
                api_key=_qr_api_key,
                api_base=_qr_api_base,
                model=_qr_model,
                temperature=0.0,
                top_p=0.1,
                timeout=60,
            ),
        )
    else:
        QR_LLM_MODEL_CONFIG = None
except Exception as e:
    logger.error("QR LLM config not configured: %r", e)
    QR_LLM_MODEL_CONFIG = None

__all__ = [
    "EMBEDDING_CONFIG",
    "MULTIMODAL_EMBEDDING_CONFIG",
    "RERANKER_CONFIG",
    "QR_LLM_MODEL_CONFIG",
]
