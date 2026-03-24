# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Build LLM, embedder, reranker, and graph config from .env variables.
"""

import json
import os

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.foundation.store.base_reranker import RerankerConfig
from openjiuwen.core.foundation.store.graph.config import GraphConfig
from openjiuwen.core.foundation.store.graph.database_config import GraphStoreIndexConfig
from openjiuwen.core.foundation.store.vector_fields.milvus_fields import MilvusAUTO
from openjiuwen.core.retrieval import OpenAIEmbedding
from openjiuwen.extensions.vendor_specific.aliyun_reranker import AliyunReranker


def get_env_json(key: str, default: dict | None = None) -> dict:
    """Parse an env var that holds a JSON object into a dict."""
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default or {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default or {}


def build_llm():
    """Build LLM client from JIUWEN_GRAPH_MEM_LLM_* and DASHSCOPE_API_KEY."""
    url = (os.environ.get("JIUWEN_GRAPH_MEM_LLM_URL") or "").strip()
    model = (os.environ.get("JIUWEN_GRAPH_MEM_LLM_MODEL") or "").strip()
    key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if not url or not model:
        return None
    cfg = get_env_json("JIUWEN_GRAPH_MEM_LLM_CONFIG")

    client_config = ModelClientConfig(
        client_provider=ProviderType.OpenAI,
        api_key=key,
        api_base=url.rstrip("/"),
        timeout=float(cfg.get("timeout", 60)),
        verify_ssl=False,
    )
    request_config = ModelRequestConfig(
        model=model,
        temperature=float(cfg.get("temperature", 0.6)),
        top_p=float(cfg.get("top_p", 0.1)),
        max_tokens=cfg.get("max_tokens"),
    )
    return Model(model_config=request_config, model_client_config=client_config)


def build_embedder():
    """Build embedder from JIUWEN_GRAPH_MEM_EMBED_* and DASHSCOPE_API_KEY."""
    url = (os.environ.get("JIUWEN_GRAPH_MEM_EMBED_URL") or "").strip()
    model = (os.environ.get("JIUWEN_GRAPH_MEM_EMBED_MODEL") or "").strip()
    key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if not url or not model:
        return None
    cfg = get_env_json("JIUWEN_GRAPH_MEM_EMBED_CONFIG")

    embed_config = EmbeddingConfig(
        model_name=model,
        base_url=url.rstrip("/"),
        api_key=key or "",
    )
    dim = cfg.get("dim", 1024)
    timeout = int(cfg.get("timeout", 30))
    return OpenAIEmbedding(embed_config, dimension=dim, timeout=timeout, max_concurrent=10)


def build_reranker():
    """Build optional reranker from JIUWEN_GRAPH_MEM_RERANK_* and DASHSCOPE_API_KEY."""
    if (os.environ.get("JIUWEN_GRAPH_MEM_RERANK_ENABLE") or "true").strip().lower() not in ("true", "1", "yes"):
        return None
    key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if not key:
        return None
    model = (os.environ.get("JIUWEN_GRAPH_MEM_RERANK_MODEL") or "qwen3-rerank").strip()
    api_base = (os.environ.get("JIUWEN_GRAPH_MEM_RERANK_URL") or "https://dashscope.aliyuncs.com/api/v1").strip()
    cfg = get_env_json("JIUWEN_GRAPH_MEM_RERANK_CONFIG")

    return AliyunReranker(
        RerankerConfig(
            api_key=key,
            api_base=api_base,
            model=model,
            timeout=float(cfg.get("timeout", 60)),
        )
    )


def build_graph_config(embed_dim: int) -> "GraphConfig":
    """Build GraphConfig from JIUWEN_GRAPH_MEM_MILVUS_URI and JIUWEN_GRAPH_MEM_MILVUS_DB_NAME."""

    uri = (os.environ.get("JIUWEN_GRAPH_MEM_MILVUS_URI") or "http://localhost:19530").strip()
    db_name = (os.environ.get("JIUWEN_GRAPH_MEM_MILVUS_DB_NAME") or "graph_memory_test").strip()
    db_embed_config = GraphStoreIndexConfig(
        index_type=MilvusAUTO(),
        distance_metric="cosine",
    )
    return GraphConfig(
        uri=uri,
        name=db_name,
        timeout=30.0,
        max_concurrent=20,
        embed_dim=embed_dim,
        db_embed_config=db_embed_config,
    )
