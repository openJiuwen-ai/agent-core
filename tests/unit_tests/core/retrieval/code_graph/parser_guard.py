# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skip Code Graph tests that would download tree-sitter grammars from GitHub."""

from __future__ import annotations

import pytest

from openjiuwen.core.retrieval.code_graph.indexing.parser import python_grammar_is_cached


def skip_unless_code_graph_parser() -> None:
    """Import the language pack, then skip if Python still needs a network fetch.

    ``pytest.importorskip`` only checks that the wheel is installed.
    ``tree-sitter-language-pack`` 1.x then downloads ``parsers-*.tar.zst``
    from GitHub on the first ``get_parser()``. Restricted CI hosts time out
    or hit ``CacheLockError``, which is an ERROR rather than a skip.
    """
    pytest.importorskip("tree_sitter_language_pack")
    cached = python_grammar_is_cached()
    if cached is False:
        pytest.skip(
            "tree-sitter-language-pack python grammar is not cached; "
            "skipping to avoid a GitHub download that times out in CI",
            allow_module_level=True,
        )
