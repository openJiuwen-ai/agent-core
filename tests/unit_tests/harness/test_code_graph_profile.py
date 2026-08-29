# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Code Graph profiles: off vs graph (find_*), and Code Agent wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.retrieval.code_graph.errors import CodeGraphStatus
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs, UserMessageInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
import openjiuwen.harness.rails.code_graph_profile_rail as profile_rail_mod
from openjiuwen.harness.rails import CodeGraphProfileRail
from openjiuwen.harness.schema.code_graph import (
    CodeGraphProfile,
    CodeGraphRequest,
    CodeGraphRunState,
    LocalizationPhase,
    resolve_code_graph_profile,
)
from openjiuwen.harness.subagents.code_agent import (
    build_code_agent_config,
    create_code_agent,
)
from openjiuwen.harness.tools.code_graph.session import reset_localization_sessions
from openjiuwen.harness.tools.code_graph import (
    LOCATE_EXAM_TOOL_NAMES,
    PRODUCT_GRAPH_TOOL_NAMES,
    CodeGraphToolContext,
    build_code_graph_profile_tools,
)
from tests.unit_tests.core.retrieval.code_graph.parser_guard import skip_unless_code_graph_parser

pytestmark = pytest.mark.level0

SAMPLE = '''\
class UserService:
    def create_user(self, name: str) -> str:
        return name
'''


@pytest.fixture(autouse=True)
def _clean_sessions() -> None:
    reset_localization_sessions()
    yield
    reset_localization_sessions()


def _repo(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "user.py").write_text(SAMPLE, encoding="utf-8")
    return tmp_path


def _context(
    repo: Path,
    tmp_path: Path,
    run_state: CodeGraphRunState | None = None,
) -> CodeGraphToolContext:
    return CodeGraphToolContext(
        repo_root=str(repo),
        config=CodeGraphConfig(cache_dir=str(tmp_path / "cache"), max_files=100),
        language="en",
        agent_id="test",
        run_state=run_state,
    )


def _fake_model() -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="openai",
            api_key="fake-key",
            api_base="http://localhost:0",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="fake-code-graph"),
    )


def test_off_profile_exposes_no_tools(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    tools = build_code_graph_profile_tools(
        _context(repo, tmp_path),
        profile=CodeGraphProfile.OFF,
    )
    assert tools == []


def test_graph_profile_exposes_find_tools(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    state = CodeGraphRunState(request=CodeGraphRequest(query="user"))
    names = [
        tool.card.name
        for tool in build_code_graph_profile_tools(
            _context(repo, tmp_path, state), state, profile=CodeGraphProfile.GRAPH
        )
    ]
    assert names == list(PRODUCT_GRAPH_TOOL_NAMES)
    assert "search_code" not in names
    assert "search_text" not in names
    assert "select_context" not in names
    assert "trace_call_chain" not in names
    assert "commit_code_context" not in names
    assert "analyze_impact" not in names
    assert "submit_code_context" not in names
    from openjiuwen.harness.tools.code_graph.search_code import FindCodeSymbolsTool

    assert FindCodeSymbolsTool(_context(repo, tmp_path, state)).card.name == "find_code_symbols"


def test_search_next_actions_recommend_find_tools() -> None:
    from openjiuwen.harness.tools.code_graph.search_code import next_actions

    empty = next_actions("missing", [])
    assert empty[0]["tool"] == "search_source_text"
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
    names = [item["tool"] for item in hits]
    assert names[0] == "read_symbol"
    assert "find_callers" in names
    assert "analyze_impact" not in names
    assert "list_symbols" not in names


def test_search_next_actions_def_alias_query_names_alias_not_ref_alias() -> None:
    from openjiuwen.harness.tools.code_graph.search_code import next_actions

    hits = next_actions(
        "def alias(",
        [
            {
                "name": "ref_alias",
                "kind": "method",
                "symbol_id": "sql/query.py::Query.ref_alias",
                "file": "sql/query.py",
                "score": 9.2,
            },
            {
                "name": "alias",
                "kind": "method",
                "symbol_id": "query.py::QuerySet.alias",
                "file": "query.py",
                "score": 8.0,
            },
        ],
    )
    assert hits[0]["symbol_id"] == "query.py::QuerySet.alias"


def test_text_next_actions_read_the_matching_definition() -> None:
    from openjiuwen.harness.tools.code_graph.search_text import text_next_actions

    actions = text_next_actions(
        [
            {
                "file": "sql/query.py",
                "symbol_id": "sql/query.py::Query.clear_select_clause",
                "name": "clear_select_clause",
                "kind": "definition",
            }
        ]
    )
    assert actions[0]["tool"] == "read_symbol"
    assert actions[0]["symbol_id"] == "sql/query.py::Query.clear_select_clause"


def test_search_next_actions_still_fire_when_the_top_hit_is_not_named() -> None:
    from openjiuwen.harness.tools.code_graph.search_code import next_actions

    hits = next_actions(
        "itrs_to_altaz",
        [
            {
                "name": "test_regression_5133",
                "kind": "function",
                "symbol_id": "tests/test_regression.py::test_regression_5133",
                "file": "tests/test_regression.py",
                "start_line": 303,
                "score": 22.0,
            },
            {
                "name": "cirs_to_itrs_mat",
                "kind": "function",
                "symbol_id": "src/transforms.py::cirs_to_itrs_mat",
                "file": "src/transforms.py",
                "start_line": 49,
                "score": 21.0,
            },
        ],
    )
    assert hits
    assert hits[0]["tool"] == "read_symbol"
    assert hits[0]["symbol_id"] == "src/transforms.py::cirs_to_itrs_mat"


def test_locate_search_named_hit_only_read_symbol() -> None:
    from openjiuwen.harness.tools.code_graph.search_code import locate_search_next_actions

    hits = locate_search_next_actions(
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
    assert [item["tool"] for item in hits] == ["read_symbol"]
    assert hits[0]["symbol_id"] == "user.py::create_user"
    assert "find_callers" not in {item["tool"] for item in hits}
    assert "read_file" not in {item["tool"] for item in hits}


def test_locate_search_unconfident_hit_is_not_pinned() -> None:
    from openjiuwen.harness.tools.code_graph.search_code import locate_search_next_actions

    hits = locate_search_next_actions(
        "itrs_to_altaz",
        [
            {
                "name": "test_regression_5133",
                "kind": "function",
                "symbol_id": "tests/test_regression.py::test_regression_5133",
                "file": "tests/test_regression.py",
                "score": 22.0,
            },
            {
                "name": "cirs_to_itrs_mat",
                "kind": "function",
                "symbol_id": "src/transforms.py::cirs_to_itrs_mat",
                "file": "src/transforms.py",
                "score": 21.0,
            },
        ],
    )
    assert hits == []


def test_importer_next_actions_inspect_files_instead_of_reading_tests() -> None:
    from openjiuwen.harness.tools.code_graph.find import importer_next_actions

    actions = importer_next_actions(
        [
            {
                "symbol_id": "tests/test_frames.py",
                "name": "test_frames.py",
                "kind": "file",
                "file": "tests/test_frames.py",
                "start_line": 1,
                "end_line": 1500,
            },
            {
                "symbol_id": "src/transforms.py",
                "name": "transforms.py",
                "kind": "file",
                "file": "src/transforms.py",
                "start_line": 1,
                "end_line": 280,
            },
        ]
    )
    assert actions
    assert actions[0]["tool"] == "inspect_code_structure"
    assert actions[0]["file"] == "src/transforms.py"
    assert all("test_frames.py" not in str(item) for item in actions)


def test_caller_next_actions_read_unresolved_production_callers() -> None:
    from openjiuwen.harness.tools.code_graph.find import caller_next_actions

    actions = caller_next_actions(
        [
            {
                "symbol_id": "schema.py::SchemaEditor._alter_many_to_many",
                "name": "_alter_many_to_many",
                "file": "schema.py",
            }
        ],
        [
            {
                "caller_id": "ops.py::RenameModel.database_forwards",
                "callee_name": "alter_db_table",
                "file": "ops.py",
            },
            {
                "caller_id": "tests/test_ops.py::test_rename",
                "callee_name": "alter_db_table",
                "file": "tests/test_ops.py",
            },
        ],
    )
    ids = [item["symbol_id"] for item in actions]
    assert ids[0] == "ops.py::RenameModel.database_forwards"
    assert "schema.py::SchemaEditor._alter_many_to_many" in ids
    assert all("test_ops.py" not in str(item) for item in actions)


def test_caller_next_actions_do_not_let_same_file_callers_crowd_out_unresolved() -> None:
    from openjiuwen.harness.tools.code_graph.find import caller_next_actions

    related = [
        {
            "symbol_id": f"query.py::QuerySet.{name}",
            "name": name,
            "file": "query.py",
        }
        for name in ("__or__", "get", "bulk_update", "contains", "dates")
    ]
    actions = caller_next_actions(
        related,
        [
            {
                "caller_id": "admin/options.py::ModelAdmin.get_search_results",
                "callee_name": "filter",
                "file": "admin/options.py",
            }
        ],
    )
    ids = [item["symbol_id"] for item in actions]
    assert ids[0] == "admin/options.py::ModelAdmin.get_search_results"
    assert "query.py::QuerySet.__or__" in ids


def test_locate_prompt_mode_adds_submit(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    state = CodeGraphRunState(request=CodeGraphRequest(query="user"))
    names = [
        tool.card.name
        for tool in build_code_graph_profile_tools(
            _context(repo, tmp_path, state),
            state,
            profile=CodeGraphProfile.GRAPH,
            prompt_mode="locate",
        )
    ]
    assert names == list(LOCATE_EXAM_TOOL_NAMES)
    assert "submit_code_context" in names


def test_unknown_profile_spellings_are_off() -> None:
    assert resolve_code_graph_profile("off") == CodeGraphProfile.OFF
    assert resolve_code_graph_profile("graph") == CodeGraphProfile.GRAPH
    assert resolve_code_graph_profile(True) == CodeGraphProfile.OFF
    assert resolve_code_graph_profile(False) == CodeGraphProfile.OFF
    assert resolve_code_graph_profile("default") == CodeGraphProfile.OFF
    assert resolve_code_graph_profile("on") == CodeGraphProfile.OFF
    assert resolve_code_graph_profile("retropus") == CodeGraphProfile.OFF
    assert resolve_code_graph_profile("nonsense") == CodeGraphProfile.OFF
    assert resolve_code_graph_profile(None) == CodeGraphProfile.OFF


@pytest.mark.asyncio
async def test_submit_code_context_publishes_a_packet_without_finishing(
    tmp_path: Path,
) -> None:
    skip_unless_code_graph_parser()
    repo = _repo(tmp_path / "repo")
    state = CodeGraphRunState(
        request=CodeGraphRequest(query="fix create_user"),
        profile=CodeGraphProfile.GRAPH.value,
    )
    tools = {
        tool.card.name: tool
        for tool in build_code_graph_profile_tools(
            _context(repo, tmp_path, state),
            state,
            profile=CodeGraphProfile.GRAPH,
            prompt_mode="locate",
        )
    }
    found = await tools["find_code_symbols"].invoke({"query": "create_user"})
    assert found.data["candidates_only"] is True
    symbol_id = found.data["matches"][0]["symbol_id"]
    await tools["read_symbol"].invoke({"symbol_id": symbol_id})
    committed = await tools["submit_code_context"].invoke(
        {"status": "COMPLETE", "summary": "create_user returns the name unchanged"}
    )
    assert committed.success
    assert committed.data["phase"] == LocalizationPhase.COMMITTED.value
    assert state.context_committed is True
    assert state.finished is False
    packet = committed.data["context_packet"]
    assert packet["span_count"] == 1
    assert packet["files"][0]["file"].endswith("user.py")


@pytest.mark.asyncio
async def test_submit_is_blocked_before_any_selection(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    state = CodeGraphRunState(
        request=CodeGraphRequest(query="fix create_user"),
        profile=CodeGraphProfile.GRAPH.value,
    )
    tools = {
        tool.card.name: tool
        for tool in build_code_graph_profile_tools(
            _context(repo, tmp_path, state),
            state,
            profile=CodeGraphProfile.GRAPH,
            prompt_mode="locate",
        )
    }
    blocked = await tools["submit_code_context"].invoke(
        {"status": "COMPLETE", "summary": "guessing"}
    )
    assert blocked.success is False
    assert "submit_code_context blocked" in (blocked.error or "")
    assert state.phase == LocalizationPhase.UNBOUND.value


@pytest.mark.asyncio
async def test_select_code_context_errors_use_the_public_name(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    tools = {
        tool.card.name: tool
        for tool in build_code_graph_profile_tools(
            _context(repo, tmp_path),
            profile=CodeGraphProfile.GRAPH,
        )
    }
    missing_state = await tools["select_code_context"].invoke({"reason": "gate"})
    assert missing_state.success is False
    error = missing_state.error or ""
    assert error.startswith("select_code_context")
    assert "select_context requires" not in error
    assert "select_context rejected" not in error


def test_code_agent_config_carries_the_profile_in_factory_kwargs() -> None:
    spec = build_code_agent_config(
        _fake_model(),
        language="en",
        code_graph_profile="graph",
        code_graph_prompt_mode="locate",
        inject_builtin_plan_agents=False,
    )
    assert spec.factory_kwargs["code_graph_profile"] == "graph"
    assert spec.factory_kwargs["code_graph_prompt_mode"] == "locate"
    assert spec.factory_kwargs["inject_builtin_plan_agents"] is False


def test_code_agent_config_without_a_profile_stays_unchanged() -> None:
    spec = build_code_agent_config(_fake_model(), language="en")
    assert "code_graph_profile" not in spec.factory_kwargs
    assert "code_graph_config" not in spec.factory_kwargs
    assert "code_graph_prompt_mode" not in spec.factory_kwargs


async def _code_agent(tmp_path: Path, **kwargs: object):
    agent = create_code_agent(
        _fake_model(),
        card=AgentCard(name="code_agent", description="code"),
        workspace=str(tmp_path),
        language="en",
        auto_create_workspace=False,
        **kwargs,
    )
    await agent.ensure_initialized()
    return agent


def _registered_tool_names(agent) -> set[str]:
    rail = agent.find_rails_by_type((CodeGraphProfileRail,))[0]
    return {tool.card.name for tool in rail._tools}  # noqa: SLF001


def _graph_tool_ctx(agent, *, tool_name: str, status: str) -> AgentCallbackContext:
    return AgentCallbackContext(
        agent=agent,
        inputs=ToolCallInputs(
            tool_name=tool_name,
            tool_result={"status": status, "message": status},
        ),
    )


@pytest.mark.asyncio
async def test_graph_profile_registers_find_tools_on_the_code_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: True)
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        inject_builtin_plan_agents=False,
    )
    names = _registered_tool_names(agent)
    assert names == set(PRODUCT_GRAPH_TOOL_NAMES)
    runtime = getattr(agent, "code_graph_runtime", None)
    assert runtime is not None
    assert runtime.repo_root
    # Conversation id is optional metadata; it is not the workspace graph key.
    assert runtime.run_state is not None
    assert not hasattr(agent, "_code_graph_session_id")
    assert "search_code" not in names
    assert "analyze_impact" not in names
    assert agent.ability_manager.get("resolve_symbol") is not None
    assert agent.ability_manager.get("submit_code_context") is None
    assert agent.ability_manager.get("grep") is None
    assert agent.ability_manager.get("glob") is None
    assert agent.ability_manager.get("edit_file") is not None
    assert agent.ability_manager.get("read_file") is not None
    assert [spec.agent_card.name for spec in (agent.deep_config.subagents or [])] == []


@pytest.mark.asyncio
async def test_graph_profile_keeps_grep_when_parser_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: False)
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        inject_builtin_plan_agents=False,
    )
    assert agent.ability_manager.get("resolve_symbol") is None
    assert agent.ability_manager.get("find_code_symbols") is None
    assert agent.ability_manager.get("grep") is not None
    assert agent.ability_manager.get("glob") is not None
    assert agent.ability_manager.get("read_file") is not None
    assert agent.ability_manager.get("edit_file") is not None


@pytest.mark.asyncio
async def test_graph_profile_restores_grep_when_graph_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: True)
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        inject_builtin_plan_agents=False,
    )
    assert agent.ability_manager.get("grep") is None
    rail = agent.find_rails_by_type((CodeGraphProfileRail,))[0]
    await rail.after_tool_call(
        _graph_tool_ctx(
            agent,
            tool_name="find_code_symbols",
            status=CodeGraphStatus.UNAVAILABLE.value,
        )
    )
    assert agent.ability_manager.get("grep") is not None
    assert agent.ability_manager.get("glob") is not None
    assert agent.ability_manager.get("find_code_symbols") is None
    assert agent.ability_manager.get("search_source_text") is None
    assert agent.ability_manager.get("resolve_symbol") is None
    assert agent.ability_manager.get("read_file") is not None
    assert agent.ability_manager.get("edit_file") is not None


@pytest.mark.asyncio
async def test_graph_profile_warms_the_index_on_user_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: True)
    seen: list[str] = []

    async def fake_fresh(self, repo_root, config=None, **kwargs):
        del self, config, kwargs
        seen.append(str(repo_root))
        raise RuntimeError("warmup should not fail the turn")

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.manager.CodeGraphManager.ensure_fresh",
        fake_fresh,
    )
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        inject_builtin_plan_agents=False,
    )
    rail = agent.find_rails_by_type((CodeGraphProfileRail,))[0]
    await rail.on_user_message(
        AgentCallbackContext(
            agent=agent,
            inputs=UserMessageInputs(parts=["where is UserService?"]),
        )
    )
    await asyncio.sleep(0.05)
    assert seen
    assert agent.ability_manager.get("grep") is None
    assert agent.ability_manager.get("find_code_symbols") is not None


@pytest.mark.asyncio
async def test_graph_profile_warmup_limit_drops_graph_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openjiuwen.core.retrieval.code_graph.errors import CodeGraphLimitExceeded

    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: True)

    async def fake_fresh(self, repo_root, config=None, **kwargs):
        del self, repo_root, config, kwargs
        raise CodeGraphLimitExceeded(
            "too many files",
            limit="max_files",
            observed=5001,
            cap=5000,
        )

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.manager.CodeGraphManager.ensure_fresh",
        fake_fresh,
    )
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        inject_builtin_plan_agents=False,
    )
    rail = agent.find_rails_by_type((CodeGraphProfileRail,))[0]
    await rail.on_user_message(
        AgentCallbackContext(
            agent=agent,
            inputs=UserMessageInputs(parts=["where is UserService?"]),
        )
    )
    await asyncio.sleep(0.05)
    assert agent.ability_manager.get("grep") is not None
    assert agent.ability_manager.get("glob") is not None
    assert agent.ability_manager.get("find_code_symbols") is None
    assert agent.ability_manager.get("search_source_text") is None


@pytest.mark.asyncio
async def test_graph_profile_keeps_grep_hidden_on_partial_query_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: True)
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        inject_builtin_plan_agents=False,
    )
    assert agent.ability_manager.get("grep") is None
    rail = agent.find_rails_by_type((CodeGraphProfileRail,))[0]
    await rail.after_tool_call(
        _graph_tool_ctx(
            agent,
            tool_name="find_code_symbols",
            status=CodeGraphStatus.PARTIAL.value,
        )
    )
    assert agent.ability_manager.get("grep") is None
    assert agent.ability_manager.get("glob") is None


@pytest.mark.asyncio
async def test_graph_profile_keeps_grep_hidden_while_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: True)
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        inject_builtin_plan_agents=False,
    )
    rail = agent.find_rails_by_type((CodeGraphProfileRail,))[0]
    await rail.after_tool_call(
        _graph_tool_ctx(
            agent,
            tool_name="resolve_symbol",
            status=CodeGraphStatus.BUILDING.value,
        )
    )
    assert agent.ability_manager.get("grep") is None


@pytest.mark.asyncio
async def test_graph_profile_keeps_grep_hidden_on_stale_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: True)
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        inject_builtin_plan_agents=False,
    )
    rail = agent.find_rails_by_type((CodeGraphProfileRail,))[0]
    await rail.after_tool_call(
        _graph_tool_ctx(
            agent,
            tool_name="find_code_symbols",
            status=CodeGraphStatus.STALE.value,
        )
    )
    assert agent.ability_manager.get("grep") is None


@pytest.mark.asyncio
async def test_graph_profile_does_not_restore_grep_on_query_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: True)
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        inject_builtin_plan_agents=False,
    )
    rail = agent.find_rails_by_type((CodeGraphProfileRail,))[0]
    await rail.after_tool_call(
        _graph_tool_ctx(
            agent,
            tool_name="find_code_symbols",
            status=CodeGraphStatus.ERROR.value,
        )
    )
    assert agent.ability_manager.get("grep") is None
    assert agent.ability_manager.get("glob") is None


@pytest.mark.asyncio
async def test_locate_exam_does_not_restore_grep_when_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: True)
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        code_graph_prompt_mode="locate",
        inject_builtin_plan_agents=False,
    )
    assert agent.ability_manager.get("grep") is None
    rail = agent.find_rails_by_type((CodeGraphProfileRail,))[0]
    await rail.after_tool_call(
        _graph_tool_ctx(
            agent,
            tool_name="find_code_symbols",
            status=CodeGraphStatus.UNAVAILABLE.value,
        )
    )
    assert agent.ability_manager.get("grep") is None


@pytest.mark.asyncio
async def test_write_file_marks_the_shared_workspace_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openjiuwen.core.retrieval.code_graph.manager import get_code_graph_manager, reset_code_graph_manager

    reset_code_graph_manager()
    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: True)
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        inject_builtin_plan_agents=False,
    )
    rail = agent.find_rails_by_type((CodeGraphProfileRail,))[0]
    manager = get_code_graph_manager()
    published: list[str] = []

    async def fake_fresh(self, repo_root, config=None, **kwargs):
        del self, config, kwargs
        published.append(str(repo_root))

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.manager.CodeGraphManager.ensure_fresh",
        fake_fresh,
    )
    await manager.get_service(tmp_path, rail.config, ensure=False)
    await rail.after_tool_call(
        AgentCallbackContext(
            agent=agent,
            inputs=ToolCallInputs(
                tool_name="write_file",
                tool_args={"path": "src/user.py"},
                tool_result={"status": "ok"},
            ),
        )
    )
    stats = manager.stats(tmp_path)
    assert "src/user.py" in stats["dirty_paths"]
    assert published
    reset_code_graph_manager()


@pytest.mark.asyncio
async def test_write_over_limit_restores_grep_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openjiuwen.core.retrieval.code_graph.errors import CodeGraphLimitExceeded

    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: True)
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        inject_builtin_plan_agents=False,
    )
    rail = agent.find_rails_by_type((CodeGraphProfileRail,))[0]

    async def fake_fresh(self, repo_root, config=None, **kwargs):
        del self, repo_root, config, kwargs
        raise CodeGraphLimitExceeded(
            "too many files",
            limit="max_files",
            observed=3,
            cap=1,
        )

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.manager.CodeGraphManager.ensure_fresh",
        fake_fresh,
    )
    await rail.after_tool_call(
        AgentCallbackContext(
            agent=agent,
            inputs=ToolCallInputs(
                tool_name="write_file",
                tool_args={"path": "src/extra.py"},
                tool_result={"status": "ok"},
            ),
        )
    )
    assert agent.ability_manager.get("grep") is not None
    assert agent.ability_manager.get("find_code_symbols") is None


@pytest.mark.asyncio
async def test_locate_prompt_mode_registers_submit_on_the_code_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: True)
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        code_graph_prompt_mode="locate",
        inject_builtin_plan_agents=False,
    )
    names = _registered_tool_names(agent)
    assert names == set(LOCATE_EXAM_TOOL_NAMES)
    assert agent.ability_manager.get("submit_code_context") is not None


@pytest.mark.asyncio
async def test_off_profile_registers_no_graph_tools(tmp_path: Path) -> None:
    agent = await _code_agent(tmp_path, code_graph_profile="off")
    rail = agent.find_rails_by_type((CodeGraphProfileRail,))[0]
    assert rail._tools == []  # noqa: SLF001
    assert agent.ability_manager.get("resolve_symbol") is None
    assert agent.ability_manager.get("search_code") is None
    assert agent.ability_manager.get("find_code_symbols") is None
    assert agent.ability_manager.get("grep") is not None
    assert agent.ability_manager.get("glob") is not None
    assert agent.ability_manager.get("read_file") is not None
    assert agent.ability_manager.get("edit_file") is not None


@pytest.mark.asyncio
async def test_uninit_restores_original_search_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: True)
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        inject_builtin_plan_agents=False,
    )
    assert agent.ability_manager.get("grep") is None
    assert agent.ability_manager.get("resolve_symbol") is not None
    rail = agent.find_rails_by_type((CodeGraphProfileRail,))[0]
    rail.uninit(agent)
    assert agent.ability_manager.get("grep") is not None
    assert agent.ability_manager.get("glob") is not None
    assert agent.ability_manager.get("resolve_symbol") is None
    assert agent.ability_manager.get("find_code_symbols") is None
    assert agent.ability_manager.get("read_file") is not None
    assert agent.ability_manager.get("edit_file") is not None


@pytest.mark.asyncio
async def test_graph_registration_failure_leaves_original_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_rail_mod, "_parser_ready", lambda: True)

    def boom(self, agent) -> None:
        raise RuntimeError("prompt failed")

    monkeypatch.setattr(CodeGraphProfileRail, "_inject_prompt", boom)
    agent = await _code_agent(
        tmp_path,
        code_graph_profile="graph",
        inject_builtin_plan_agents=False,
    )
    assert agent.ability_manager.get("resolve_symbol") is None
    assert agent.ability_manager.get("find_code_symbols") is None
    assert agent.ability_manager.get("grep") is not None
    assert agent.ability_manager.get("glob") is not None
    assert agent.ability_manager.get("read_file") is not None
    assert agent.ability_manager.get("edit_file") is not None
    rail = agent.find_rails_by_type((CodeGraphProfileRail,))[0]
    assert rail._tools == []  # noqa: SLF001


@pytest.mark.asyncio
async def test_omitted_profile_matches_develop_host_and_stays_baseline(
    tmp_path: Path,
) -> None:
    """JiuwenSwarm ``develop`` never passes ``code_graph_profile``.

    New agent-core must still produce the original grep / read / edit agent.
    """
    from openjiuwen.harness.factory import create_deep_agent

    agent = await _code_agent(tmp_path)
    assert agent.find_rails_by_type((CodeGraphProfileRail,)) == []
    assert agent.ability_manager.get("grep") is not None
    assert agent.ability_manager.get("read_file") is not None
    assert agent.ability_manager.get("edit_file") is not None
    assert agent.ability_manager.get("resolve_symbol") is None
    assert agent.ability_manager.get("search_code") is None

    root = create_deep_agent(
        _fake_model(),
        workspace=str(tmp_path),
        language="en",
        auto_create_workspace=False,
    )
    await root.ensure_initialized()
    assert root.find_rails_by_type((CodeGraphProfileRail,)) == []


@pytest.mark.asyncio
async def test_profile_replaces_the_default_code_graph_rail(tmp_path: Path) -> None:
    agent = await _code_agent(tmp_path, code_graph_profile="graph")
    assert agent.find_rails_by_type((CodeGraphProfileRail,))


@pytest.mark.asyncio
async def test_builtin_explore_and_plan_do_not_inherit_the_profile(tmp_path: Path) -> None:
    agent = await _code_agent(tmp_path, code_graph_profile="graph")
    specs = [
        spec
        for spec in (agent.deep_config.subagents or [])
        if getattr(getattr(spec, "agent_card", None), "name", "") in {
            "explore_agent",
            "plan_agent",
        }
    ]
    assert len(specs) == 2
    for spec in specs:
        assert "code_graph_profile" not in (spec.factory_kwargs or {})
