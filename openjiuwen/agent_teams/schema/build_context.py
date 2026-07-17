# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Re-export of the harness BuildContext.

``BuildContext`` and its factory helpers now live in
``openjiuwen.harness.schema.build_context`` as the harness-level source of
truth. This module is preserved as a thin re-export so existing team import
paths keep working and refer to the same objects (``is``-identical).
"""

from __future__ import annotations

from openjiuwen.harness.schema.build_context import (  # noqa: F401
    _BUILD_CONTEXT_FACTORY,
    BuildContext,
    build_context_from_seed,
    register_build_context_factory,
)

__all__ = [
    "BuildContext",
    "register_build_context_factory",
    "build_context_from_seed",
]
