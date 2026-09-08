# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Classic vs focused ACI: tool table, summaries, and focus_code."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from openjiuwen.harness.prompts.code_graph_profile import build_code_graph_profile_prompt
from openjiuwen.harness.schema.code_graph import (
    CodeGraphProfile,
    CodeGraphRequest,
    CodeGraphRetrievalInterface,
    CodeGraphRunState,
    PROMPT_MODE_LOCATE,
    PROMPT_MODE_PRODUCT,
    resolve_code_graph_retrieval_interface,
)
from openjiuwen.harness.tools.code_graph import (
    FOCUSED_CORE_TOOL_NAMES,
    LOCATE_EXAM_TOOL_NAMES,
    PRODUCT_GRAPH_TOOL_NAMES,
    CodeGraphToolContext,
    build_code_graph_profile_tools,
    code_graph_profile_tool_names,
)
from openjiuwen.core.retrieval.code_graph.errors import CodeGraphStatus
from openjiuwen.harness.tools.code_graph.focused import (
    apply_focused_observation,
    classify_role,
    focused_next_actions,
    infer_match_mode,
    stable_candidate_id,
)
from openjiuwen.harness.tools.code_graph.search_code import next_actions
from tests.unit_tests.core.retrieval.code_graph.parser_guard import skip_unless_code_graph_parser

pytestmark = pytest.mark.level0

SAMPLE = '''\
class UserService:
    def create_user(self, name: str) -> str:
        return name

ERROR_TOKEN = "header_rows missing"
''' + "\n".join(f"HELPER_{i} = {i}" for i in range(120))


def _state(*, interface: str = "classic", query: str = "create_user") -> CodeGraphRunState:
    return CodeGraphRunState(
        request=CodeGraphRequest(query=query),
        profile=CodeGraphProfile.GRAPH.value,
        prompt_mode=PROMPT_MODE_PRODUCT,
        retrieval_interface=interface,
    )


def _context(repo: Path, tmp_path: Path, state: CodeGraphRunState | None = None) -> CodeGraphToolContext:
    return CodeGraphToolContext(
        repo_root=str(repo),
        config=CodeGraphConfig(cache_dir=str(tmp_path / "cache"), max_files=100),
        language="en",
        agent_id="test",
        run_state=state,
    )


def _repo(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "user.py").write_text(SAMPLE, encoding="utf-8")
    return tmp_path


def test_unknown_interface_falls_back_to_classic() -> None:
    assert resolve_code_graph_retrieval_interface("nope") == CodeGraphRetrievalInterface.CLASSIC
    assert resolve_code_graph_retrieval_interface("focused") == CodeGraphRetrievalInterface.FOCUSED


def test_classic_tool_table_unchanged() -> None:
    assert code_graph_profile_tool_names(CodeGraphProfile.GRAPH) == PRODUCT_GRAPH_TOOL_NAMES
    assert "focus_code" not in PRODUCT_GRAPH_TOOL_NAMES
    assert "select_code_context" in PRODUCT_GRAPH_TOOL_NAMES


def test_focused_product_exposes_core_and_hides_select() -> None:
    names = code_graph_profile_tool_names(
        CodeGraphProfile.GRAPH,
        retrieval_interface="focused",
    )
    assert names == FOCUSED_CORE_TOOL_NAMES
    assert "focus_code" in names
    assert "select_code_context" not in names
    assert "find_callers" not in names
    assert "read_symbol" not in names
    assert "read_code" not in names


def test_classic_keeps_read_tools() -> None:
    names = code_graph_profile_tool_names(CodeGraphProfile.GRAPH)
    assert "read_symbol" in names
    assert "read_code" in names
    assert "focus_code" not in names


def test_locate_exam_never_gets_focus_code() -> None:
    names = code_graph_profile_tool_names(
        CodeGraphProfile.GRAPH,
        prompt_mode=PROMPT_MODE_LOCATE,
        retrieval_interface="focused",
    )
    assert names == LOCATE_EXAM_TOOL_NAMES
    assert "focus_code" not in names


def test_off_ignores_focused() -> None:
    assert (
        code_graph_profile_tool_names(
            CodeGraphProfile.OFF,
            retrieval_interface="focused",
        )
        == ()
    )
    assert build_code_graph_profile_prompt(
        "off",
        language="en",
        retrieval_interface="focused",
    ) == ""


def test_classic_next_actions_still_offer_callers() -> None:
    hits = next_actions(
        "create_user",
        [
            {
                "name": "create_user",
                "kind": "method",
                "symbol_id": "user.py::create_user",
                "file": "user.py",
                "start_line": 2,
                "score": 10.0,
            }
        ],
    )
    assert [item["tool"] for item in hits] == ["read_symbol", "find_callers"]


def test_focused_next_action_is_single_focus() -> None:
    actions = focused_next_actions(
        "create_user",
        [
            {
                "candidate_id": "C1",
                "name": "create_user",
                "kind": "method",
                "role": "implementation",
                "symbol_id": "user.py::create_user",
                "file": "src/user.py",
                "score": 9.0,
            }
        ],
    )
    assert len(actions) == 1
    assert actions[0]["tool"] == "focus_code"
    assert actions[0]["candidate_id"] == "C1"


def test_focused_class_hit_inspects_structure() -> None:
    actions = focused_next_actions(
        "UserService",
        [
            {
                "candidate_id": "C1",
                "name": "UserService",
                "kind": "class",
                "role": "implementation",
                "file": "src/user.py",
                "score": 8.0,
            }
        ],
    )
    assert actions == [
        {
            "tool": "inspect_code_structure",
            "file": "src/user.py",
            "reason": "see the members of UserService before focusing",
        }
    ]


def test_role_grouping_keeps_tests() -> None:
    assert classify_role("django/db/models/sql/query.py") == "implementation"
    assert classify_role("tests/ordering/tests.py") == "test"
    assert classify_role("setup.py") == "build"
    state = _state(interface="focused")
    data: dict = {}
    apply_focused_observation(
        data,
        query="add_ordering",
        state=state,
        raw_items=[
            {
                "symbol_id": "a.py::impl",
                "name": "impl",
                "kind": "function",
                "file": "pkg/a.py",
                "start_line": 10,
                "end_line": 20,
                "score": 4.0,
            },
            {
                "symbol_id": "tests/test_a.py::test_impl",
                "name": "test_impl",
                "kind": "function",
                "file": "tests/test_a.py",
                "start_line": 3,
                "end_line": 8,
                "score": 2.0,
            },
        ],
        matched_by=["symbol"],
    )
    assert set(data["groups"]) == {"implementation", "test"}
    assert data["candidates"][0]["candidate_id"] == "0:a.py::impl"
    assert data["candidates"][0]["candidate_id"] == stable_candidate_id(
        "0",
        {"symbol_id": "a.py::impl"},
    )
    assert data["candidates"][0]["next_action"]["tool"] == "focus_code"
    assert "source" not in data["candidates"][0]
    assert "body" not in data["candidates"][0]
    assert len(data["next_actions"]) == 1


def test_empty_focused_search_is_explicit() -> None:
    state = _state(interface="focused")
    data: dict = {}
    apply_focused_observation(
        data,
        query="missing",
        state=state,
        raw_items=[],
        matched_by=["symbol"],
    )
    assert "no matches" in data["message"]
    assert data["candidates"] == []


def test_infer_match_mode() -> None:
    assert infer_match_mode('"header_rows missing"') == "exact"
    assert infer_match_mode("@register") == "exact"
    assert infer_match_mode("ValueError") == "exact"
    assert infer_match_mode("CONFIG_KEY") == "exact"
    assert infer_match_mode("how ordering expressions work") == "lexical"


def test_focused_prompt_mentions_focus_code() -> None:
    text = build_code_graph_profile_prompt(
        "graph",
        language="en",
        retrieval_interface="focused",
    )
    assert "focus_code" in text
    assert "There is no read_symbol" in text
    classic = build_code_graph_profile_prompt("graph", language="en")
    assert "select_code_context" in classic
    assert "read_symbol" in classic
    assert "focus_code" not in classic


def test_candidate_id_is_stable_across_repeat_hits() -> None:
    state = _state(interface="focused")
    raw = [
        {
            "symbol_id": "user.py::create_user",
            "name": "create_user",
            "kind": "method",
            "file": "src/user.py",
            "start_line": 2,
            "end_line": 4,
        }
    ]
    first: dict = {}
    second: dict = {}
    apply_focused_observation(
        first,
        query="create_user",
        state=state,
        raw_items=raw,
        matched_by=["symbol"],
        generation_id="3",
    )
    apply_focused_observation(
        second,
        query="create_user again",
        state=state,
        raw_items=raw,
        matched_by=["symbol"],
        generation_id="3",
    )
    assert first["candidates"][0]["candidate_id"] == "3:user.py::create_user"
    assert first["candidates"][0]["candidate_id"] == second["candidates"][0]["candidate_id"]


def test_file_line_candidate_without_symbol() -> None:
    state = _state(interface="focused")
    data: dict = {}
    apply_focused_observation(
        data,
        query='"header_rows missing"',
        state=state,
        raw_items=[
            {
                "file": "src/user.py",
                "start_line": 7,
                "end_line": 7,
                "kind": "text",
                "matched_line": 'ERROR_TOKEN = "header_rows missing"',
            }
        ],
        matched_by=["exact_text"],
        generation_id="2",
    )
    assert data["candidates"][0]["candidate_id"] == "2:span:src/user.py:7-7"
    assert data["candidates"][0]["next_action"]["tool"] == "focus_code"


def test_factory_builds_focus_tool(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    state = _state(interface="focused")
    tools = build_code_graph_profile_tools(
        _context(repo, tmp_path, state),
        state,
        profile=CodeGraphProfile.GRAPH,
        retrieval_interface="focused",
    )
    assert [tool.card.name for tool in tools] == list(FOCUSED_CORE_TOOL_NAMES)
    assert state.uses_focused is True
    assert state.terminal_tool_name == "focus_code"


@pytest.mark.asyncio
async def test_focus_code_opens_window(tmp_path: Path) -> None:
    skip_unless_code_graph_parser()
    repo = _repo(tmp_path / "repo")
    state = _state(interface="focused")
    tools = {
        tool.card.name: tool
        for tool in build_code_graph_profile_tools(
            _context(repo, tmp_path, state),
            state,
            profile=CodeGraphProfile.GRAPH,
            retrieval_interface="focused",
        )
    }
    search = await tools["find_code_symbols"].invoke({"query": "create_user"})
    assert search.success
    candidates = search.data.get("candidates") or []
    assert candidates
    assert "candidate_id" in candidates[0]
    assert ":" in candidates[0]["candidate_id"]
    focused = await tools["focus_code"].invoke(
        {"candidate_id": candidates[0]["candidate_id"]}
    )
    assert focused.success
    assert focused.data.get("status") == "FOCUSED"
    assert focused.data.get("focused") is True
    assert focused.data.get("supporting_evidence") == []
    assert state.selected
    assert state.current_focus is not None
    window = int(focused.data["end_line"]) - int(focused.data["start_line"]) + 1
    assert 50 <= window <= 100


@pytest.mark.asyncio
async def test_focus_code_handles_file_line_candidate(tmp_path: Path) -> None:
    skip_unless_code_graph_parser()
    repo = _repo(tmp_path / "repo")
    state = _state(interface="focused")
    tools = {
        tool.card.name: tool
        for tool in build_code_graph_profile_tools(
            _context(repo, tmp_path, state),
            state,
            profile=CodeGraphProfile.GRAPH,
            retrieval_interface="focused",
        )
    }
    apply_focused_observation(
        {},
        query="header_rows missing",
        state=state,
        raw_items=[
            {
                "file": "src/user.py",
                "start_line": 7,
                "end_line": 7,
                "kind": "text",
                "matched_line": 'ERROR_TOKEN = "header_rows missing"',
            }
        ],
        matched_by=["exact_text"],
        generation_id=state.graph_generation or "0",
    )
    candidate_id = next(iter(state.focused_candidates))
    focused = await tools["focus_code"].invoke({"candidate_id": candidate_id})
    assert focused.success
    assert focused.data.get("status") == "FOCUSED"
    assert focused.data["file"].endswith("user.py")
    assert "header_rows missing" in str(focused.data.get("source") or "")


@pytest.mark.asyncio
async def test_focus_code_rejects_stale_generation(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    state = _state(interface="focused")
    tools = {
        tool.card.name: tool
        for tool in build_code_graph_profile_tools(
            _context(repo, tmp_path, state),
            state,
            profile=CodeGraphProfile.GRAPH,
            retrieval_interface="focused",
        )
    }
    apply_focused_observation(
        {},
        query="create_user",
        state=state,
        raw_items=[
            {
                "symbol_id": "user.py::create_user",
                "name": "create_user",
                "kind": "method",
                "file": "src/user.py",
                "start_line": 2,
                "end_line": 4,
            }
        ],
        matched_by=["symbol"],
        generation_id="1",
    )
    state.graph_generation = "2"
    stale = await tools["focus_code"].invoke({"candidate_id": "1:user.py::create_user"})
    assert stale.success
    assert stale.data.get("status") == CodeGraphStatus.STALE_CANDIDATE.value
