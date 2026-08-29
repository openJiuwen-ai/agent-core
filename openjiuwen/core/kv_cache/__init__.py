# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.kv_cache.kv_cache_types import KVCacheIdentity

# Keep this package initializer dependency-free.  The compatibility identity
# module under ``foundation.kv_cache`` imports this package, while the runtime
# configuration intentionally reuses constants from that legacy package.
# Import concrete runtime modules directly at composition roots.
__all__ = ["KVCacheIdentity"]
