# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""In-memory adjacency helpers. The live graph lives on ``CodeGraphIndex``."""

from __future__ import annotations

from openjiuwen.core.retrieval.code_graph.models import CodeGraphIndex, RelationKind


def neighbors(index: CodeGraphIndex, symbol_id: str, relation: RelationKind) -> tuple[str, ...]:
    """Return neighbor symbol ids for ``relation`` (forward or inverse)."""
    return tuple(index.neighbors(symbol_id, relation))
