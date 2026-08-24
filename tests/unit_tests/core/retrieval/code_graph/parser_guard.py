# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skip Code Graph tests that would download tree-sitter grammars from GitHub."""

from __future__ import annotations

import os

import pytest

from openjiuwen.core.retrieval.code_graph.indexing.parser import python_grammar_is_cached

_DOWNLOAD_ALLOWED = frozenset({"1", "true", "yes"})


def skip_unless_code_graph_parser() -> None:
    """Import the language pack, then skip if Python still needs a network fetch.

    ``pytest.importorskip`` only checks that the wheel is installed.
    ``tree-sitter-language-pack`` 1.x then downloads ``parsers-*.tar.zst``
    from GitHub on the first ``get_parser()``. Restricted CI hosts time out
    or hit ``CacheLockError``, which is an ERROR rather than a skip.
    An unknown cache (``None``) is treated the same as uncached.
    """
    pytest.importorskip("tree_sitter_language_pack")
    flag = os.environ.get("OPENJIUWEN_CODE_GRAPH_ALLOW_PARSER_DOWNLOAD", "").strip().lower()
    if flag in _DOWNLOAD_ALLOWED:
        return
    if python_grammar_is_cached() is True:
        return
    pytest.skip(
        "tree-sitter-language-pack python grammar is not cached; "
        "skipping to avoid a GitHub download that times out in CI",
        allow_module_level=True,
    )
