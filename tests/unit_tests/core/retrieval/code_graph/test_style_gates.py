# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Guards that keep the Huawei code-graph findings from coming back."""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.core.retrieval.code_graph.indexing import parser as parser_mod
from openjiuwen.core.retrieval.code_graph.indexing import symbol_extractor
from openjiuwen.core.retrieval.code_graph.indexing.builder import definition_documents, _line_slice
from openjiuwen.core.retrieval.code_graph.models import Symbol, SymbolKind
from openjiuwen.core.retrieval.code_graph.query.analyze_impact import _risk
from openjiuwen.core.retrieval.code_graph.query.failure_path import _repo_relative
from openjiuwen.core.retrieval.code_graph.query.patch_impact import _patch_risk, _symbol_matches_focus
from openjiuwen.core.retrieval.code_graph.service import CodeGraphService
from openjiuwen.harness.schema.code_graph import bind_code_graph_runtime

pytestmark = pytest.mark.level0


def test_walk_helpers_stay_under_the_argument_limit() -> None:
    assert len(inspect.signature(symbol_extractor._walk_python).parameters) <= 5
    assert len(inspect.signature(symbol_extractor._walk_generic).parameters) <= 5
    assert len(inspect.signature(symbol_extractor._make_python_symbol).parameters) <= 5


def test_node_name_does_not_shadow_dataclasses_field() -> None:
    source = inspect.getsource(symbol_extractor._node_name)
    assert "for field in" not in source
    assert "for field_name in" in source


def test_cpp_base_pattern_is_not_string_path_concat() -> None:
    source = inspect.getsource(symbol_extractor._generic_bases)
    assert " + re.escape(" not in source
    assert "Bar" in symbol_extractor._generic_bases("class Foo : public Bar {", "Foo")


def test_failure_payload_is_a_staticmethod() -> None:
    assert isinstance(inspect.getattr_static(CodeGraphService, "_failure_payload"), staticmethod)


def test_line_slice_avoids_whitespace_before_colon() -> None:
    source = inspect.getsource(_line_slice)
    assert " - 1 :" not in source
    assert _line_slice("a\nb\nc", 2, 3) == "b\nc"


def test_language_parser_lib_avoids_whitespace_before_colon() -> None:
    source = inspect.getsource(parser_mod._is_language_parser_lib)
    assert "len(prefix) :" not in source
    assert parser_mod._is_language_parser_lib(Path("libtree_sitter_python.so"), "python") is True
    assert parser_mod._is_language_parser_lib(Path("libtree_sitter_java.so"), "python") is False


def test_definition_documents_does_not_use_a_multiline_comprehension() -> None:
    source = inspect.getsource(definition_documents)
    assert "parts.append(part)" in source
    symbol = Symbol(
        symbol_id="mod.py::Foo",
        name="Foo",
        kind=SymbolKind.CLASS,
        file="mod.py",
        start_line=1,
        end_line=1,
        qualified_name="Foo",
        signature="class Foo",
    )
    docs = definition_documents(symbol, "class Foo:\n    pass\n")
    assert docs
    _doc, tokens = docs[0]
    assert "foo" in tokens


def test_risk_conditions_stay_factored_under_the_boolean_limit() -> None:
    assert "_high_change_surface" in inspect.getsource(_risk)
    assert "_medium_change_surface" in inspect.getsource(_risk)
    assert "or truncated" not in inspect.getsource(_risk)
    assert "_patch_risk_high" in inspect.getsource(_patch_risk)
    assert "_file_matches_token_head" in inspect.getsource(_symbol_matches_focus)


def test_fork_session_does_not_poke_protected_fields() -> None:
    source = inspect.getsource(CodeGraphService.fork_session)
    assert "forked._store" not in source
    assert "forked._session_scoped" not in source
    assert "session_scoped=True" in source
    assert "persist_index=False" in source


def test_converted_exceptions_keep_the_original_cause() -> None:
    load = inspect.getsource(CodeGraphService._load_or_build)
    resolve = inspect.getsource(CodeGraphService.resolve_path)
    assert "from exc" in load
    assert "from exc" in resolve
    assert "cause=exc" in load
    assert "cause=exc" in resolve


def test_repo_relative_avoids_whitespace_before_colon() -> None:
    source = inspect.getsource(_repo_relative)
    assert " + 1 :" not in source


def test_bind_code_graph_runtime_uses_a_public_attribute() -> None:
    agent = SimpleNamespace()
    runtime = bind_code_graph_runtime(
        agent,
        session_id="s-1",
        repo_root="/repo",
        config=None,
    )
    assert agent.code_graph_runtime is runtime
    assert not hasattr(agent, "_code_graph_session_id")
    assert not hasattr(agent, "_code_graph_repo_root")
    assert not hasattr(agent, "_code_graph_config")
    assert not hasattr(agent, "_code_graph_run_state")
