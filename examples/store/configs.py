# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Load configuration for object storage client
"""

import os
from pathlib import Path

import dotenv

from openjiuwen.core.common.logging import logger

env_file = Path(__file__).parent / ".env"
if not env_file.exists():
    logger.warning("No .env file found. Using environment variables or default values.")
else:
    dotenv.load_dotenv(str(env_file), override=True)

# OBS configuration (can be set via environment variables or .env file)
BUCKET_NAME = os.getenv("OBS_BUCKET_NAME", "openjiuwen-online-test")
OBS_SERVER = os.getenv("OBS_SERVER")
OBS_ACCESS_KEY_ID = os.getenv("OBS_ACCESS_KEY_ID")
OBS_SECRET_ACCESS_KEY = os.getenv("OBS_SECRET_ACCESS_KEY")
OBS_REGION = os.getenv("OBS_REGION")

# Milvus graph store configuration
MILVUS_URI = os.getenv("MILVUS_URI", "http://localhost:19530")
MILVUS_DB_NAME = os.getenv("MILVUS_DB_NAME", "demo_graph_db")

# Embedding configuration (for graph store and retrieval examples)
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_API_BASE") or os.getenv("EMBEDDING_BASE_URL")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")

__all__ = [
    "BUCKET_NAME",
    "OBS_SERVER",
    "OBS_ACCESS_KEY_ID",
    "OBS_SECRET_ACCESS_KEY",
    "OBS_REGION",
    "MILVUS_URI",
    "MILVUS_DB_NAME",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL",
    "EMBEDDING_API_KEY",
]
