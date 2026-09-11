# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Locate-exam submit nudge: widen a one-file packet once, then allow."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from openjiuwen.harness.schema.code_graph import (
    PROMPT_MODE_LOCATE,
    CodeGraphLocation,
    CodeGraphProfile,
    CodeGraphRelation,
    CodeGraphRequest,
    CodeGraphRunState,
)
from openjiuwen.harness.tools.code_graph import (
    CodeGraphToolContext,
    build_code_graph_profile_tools,
)
from openjiuwen.harness.tools.code_graph.submit_nudge import locate_submit_nudge
from openjiuwen.harness.tools.code_graph.session import reset_localization_sessions
from tests.unit_tests.core.retrieval.code_graph.parser_guard import skip_unless_code_graph_parser

pytestmark = pytest.mark.level0


@pytest.fixture(autouse=True)
def _clean_sessions() -> None:
    reset_localization_sessions()
    yield
    reset_localization_sessions()


def _state(**kwargs: object) -> CodeGraphRunState:
    selected = kwargs.pop("selected", None)
    prompt_mode = str(kwargs.pop("prompt_mode", PROMPT_MODE_LOCATE))
    if selected is None:
        selected = [
            CodeGraphLocation(
                symbol_id="pkg/a.py::foo",
                file="pkg/a.py",
                start_line=1,
                end_line=4,
                reason="primary",
            )
        ]
    return CodeGraphRunState(
        request=CodeGraphRequest(query="fix foo"),
        profile=CodeGraphProfile.GRAPH.value,
        prompt_mode=prompt_mode,
        selected=list(selected),
        **kwargs,
    )


def test_product_mode_never_nudges() -> None:
    state = _state(prompt_mode="product")
    state.read_evidence["e"] = {"symbol_id": "pkg/b.py::bar", "file": "pkg/b.py"}
    assert locate_submit_nudge(state) is None


def test_two_file_packet_is_not_nudged() -> None:
    state = _state(
        selected=[
            CodeGraphLocation("pkg/a.py::foo", "pkg/a.py", 1, 4, "a"),
            CodeGraphLocation("pkg/b.py::bar", "pkg/b.py", 1, 2, "b"),
        ]
    )
    assert locate_submit_nudge(state) is None


def test_already_read_second_file_is_offered_once() -> None:
    state = _state()
    state.read_evidence["a"] = {"symbol_id": "pkg/a.py::foo", "file": "pkg/a.py"}
    state.read_evidence["b"] = {"symbol_id": "pkg/b.py::bar", "file": "pkg/b.py"}
    first = locate_submit_nudge(state)
    assert first is not None
    assert first["submitted"] is False
    assert first["next_actions"][0]["tool"] == "submit_code_context"
    extras = first["next_actions"][0]["locations"]
    assert extras == [{"symbol_id": "pkg/b.py::bar", "file": "pkg/b.py"}]
    assert locate_submit_nudge(state) is None


def test_one_file_without_a_relation_hop_is_deferred_once() -> None:
    state = _state()
    first = locate_submit_nudge(state)
    assert first is not None
    tools = {item["tool"] for item in first["next_actions"]}
    assert tools == {"find_importers", "find_callers"}
    assert locate_submit_nudge(state) is None


def test_after_a_hop_unread_search_hits_are_not_forced() -> None:
    state = _state(
        selected=[
            CodeGraphLocation(
                "django/contrib/admin/options.py::ModelAdmin.save_model",
                "django/contrib/admin/options.py",
                1,
                4,
                "primary",
            )
        ]
    )
    state.submit_nudges.add("relation_hop")
    state.relations.append(
        CodeGraphRelation(
            source="django/contrib/admin/options.py::ModelAdmin.save_model",
            relation="imported_by",
            target="django/contrib/gis/db/models/lookups.py::RelateLookup",
        )
    )
    state.seen_files.update(
        {
            "django/contrib/admin/options.py",
            "django/contrib/gis/db/models/lookups.py",
            "django/contrib/staticfiles/storage.py",
        }
    )
    state.candidates["django/contrib/gis/db/models/lookups.py::RelateLookup"] = {
        "symbol_id": "django/contrib/gis/db/models/lookups.py::RelateLookup",
        "file": "django/contrib/gis/db/models/lookups.py",
    }
    assert locate_submit_nudge(state) is None


def test_after_a_hop_already_read_file_is_still_offered() -> None:
    state = _state()
    state.submit_nudges.add("relation_hop")
    state.relations.append(
        CodeGraphRelation(source="pkg/a.py::foo", relation="imported_by", target="pkg/b.py::bar")
    )
    state.read_evidence["a"] = {"symbol_id": "pkg/a.py::foo", "file": "pkg/a.py"}
    state.read_evidence["b"] = {"symbol_id": "pkg/b.py::bar", "file": "pkg/b.py"}
    first = locate_submit_nudge(state)
    assert first is not None
    assert first["next_actions"][0]["tool"] == "submit_code_context"
    extras = first["next_actions"][0]["locations"]
    assert extras == [{"symbol_id": "pkg/b.py::bar", "file": "pkg/b.py"}]
    assert locate_submit_nudge(state) is None


def _repo(tmp_path: Path) -> Path:
    src = tmp_path / "pkg"
    src.mkdir(parents=True)
    (src / "util.py").write_text("def compute():\n    return 1\n", encoding="utf-8")
    (src / "entry.py").write_text(
        "from pkg.util import compute\n\n\ndef entry():\n    return compute()\n",
        encoding="utf-8",
    )
    return tmp_path


def _tools(tmp_path: Path, state: CodeGraphRunState):
    repo = _repo(tmp_path / "repo")
    context = CodeGraphToolContext(
        repo_root=str(repo),
        config=CodeGraphConfig(cache_dir=str(tmp_path / "cache"), max_files=100),
        language="en",
        agent_id="test",
        run_state=state,
    )
    return {
        tool.card.name: tool
        for tool in build_code_graph_profile_tools(
            context,
            state,
            profile=CodeGraphProfile.GRAPH,
            prompt_mode="locate",
        )
    }


@pytest.mark.asyncio
async def test_submit_includes_already_read_file_on_the_second_call(tmp_path: Path) -> None:
    skip_unless_code_graph_parser()
    state = CodeGraphRunState(
        request=CodeGraphRequest(query="fix entry and compute"),
        profile=CodeGraphProfile.GRAPH.value,
    )
    tools = _tools(tmp_path, state)
    found = await tools["find_code_symbols"].invoke({"query": "entry"})
    entry_id = found.data["matches"][0]["symbol_id"]
    await tools["read_symbol"].invoke({"symbol_id": entry_id})
    found_util = await tools["find_code_symbols"].invoke({"query": "compute"})
    util_id = found_util.data["matches"][0]["symbol_id"]
    await tools["read_symbol"].invoke({"symbol_id": util_id})
    committed = await tools["submit_code_context"].invoke(
        {
            "status": "COMPLETE",
            "summary": "entry and compute",
            "locations": [{"symbol_id": entry_id}, {"symbol_id": util_id}],
        }
    )
    assert committed.data["phase"] == "committed"
    names = {row["file"] for row in committed.data["context_packet"]["files"]}
    assert any(name.endswith("entry.py") for name in names)
    assert any(name.endswith("util.py") for name in names)
