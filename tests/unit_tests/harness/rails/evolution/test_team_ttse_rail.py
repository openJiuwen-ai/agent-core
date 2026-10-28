# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags, TraceState

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map, iter_spans, span_attributes
from openjiuwen.agent_evolving.ttse.config import TTSEConfig
from openjiuwen.agent_evolving.ttse.subject_log import (
    bind_induce_subject,
    current_induce_subject,
    reset_induce_subject,
    tag_ttse_message,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent, InvokeInputs
from openjiuwen.extensions.observability import semconv
from openjiuwen.extensions.observability.semconv import (
    OJ_EXECUTION_SUBJECT_ID,
    OJ_EXECUTION_SUBJECT_KIND,
)
from openjiuwen.harness.rails.evolution import team_ttse_rail as team_ttse_module
from openjiuwen.harness.rails.evolution.team_ttse_rail import (
    TeamTTSERail,
    _subject_label,
    render_leader_catalog_markdown,
)
from openjiuwen.harness.rails.evolution.ttse_rail import TTSERail


def _rail(tmp_path) -> TeamTTSERail:
    return TeamTTSERail(
        llm=object(),
        model="dummy",
        member_store_path=str(tmp_path / "member" / "bank.json"),
        ttse_config=TTSEConfig(store_path=str(tmp_path / "leader" / "bank.json")),
        trajectory_span_processor=TrajectorySpanProcessor(),
    )


def _span(span_id: str, kind: str, subject_id: str) -> dict:
    return {
        "name": f"llm.{span_id}",
        "spanId": span_id,
        "traceId": "aa",
        "startTimeUnixNano": span_id,
        "endTimeUnixNano": span_id,
        "attributes": attributes_from_map(
            {
                OJ_EXECUTION_SUBJECT_KIND: kind,
                OJ_EXECUTION_SUBJECT_ID: subject_id,
            }
        ),
    }


def _trajectory(*spans: dict) -> Trajectory:
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map({TRAJECTORY_ID: "exec-1", SESSION_ID: "s1"})
                    },
                    "scopeSpans": [{"scope": {"name": "test"}, "spans": list(spans)}],
                }
            ]
        }
    )


def test_subject_windows_stay_separate_and_accumulate(tmp_path) -> None:
    rail = _rail(tmp_path)
    rail._merge_clean_increment(
        None,
        _trajectory(
            _span("01", "team_leader", "leader"),
            _span("02", "team_member", "coder"),
            _span("03", "team_member", "writer"),
            _span("04", "subagent", "coder"),
        ),
    )
    rail._merge_clean_increment(None, _trajectory(_span("05", "team_member", "coder")))

    assert set(rail._subject_windows) == {("leader",), ("member", "coder"), ("member", "writer")}
    assert len(list(iter_spans(rail._subject_windows[("leader",)]))) == 1
    assert len(list(iter_spans(rail._subject_windows[("member", "coder")]))) == 2
    assert len(list(iter_spans(rail._subject_windows[("member", "writer")]))) == 1


@pytest.mark.asyncio
async def test_round_query_is_the_first_user_message(tmp_path) -> None:
    rail = _rail(tmp_path)
    await rail._on_before_invoke(SimpleNamespace(inputs=SimpleNamespace(query="book a hotel")))
    await rail._on_before_invoke(SimpleNamespace(inputs=SimpleNamespace(query="poll the board")))
    assert rail._round_user_query == "book a hotel"


@pytest.mark.asyncio
async def test_notify_snapshots_and_returns_before_induction(tmp_path) -> None:
    rail = _rail(tmp_path)
    rail._ttse_config.evolve_enabled = True
    rail._subject_windows[("leader",)] = object()
    rail._round_user_query = "book a hotel"
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(leader, members, query) -> None:
        del leader, members, query
        started.set()
        await release.wait()

    rail._induce_round = slow  # type: ignore[method-assign]
    await asyncio.wait_for(rail.notify_team_accepted(), timeout=1)
    assert rail._subject_windows == {}
    assert rail._round_user_query == ""
    await asyncio.wait_for(started.wait(), timeout=1)
    release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_evolve_off_clears_windows_without_induction(tmp_path) -> None:
    rail = _rail(tmp_path)
    rail._ttse_config.evolve_enabled = False
    rail._subject_windows[("leader",)] = object()
    called = False

    async def induce(leader, members, query) -> None:
        del leader, members, query
        nonlocal called
        called = True

    rail._induce_round = induce  # type: ignore[method-assign]
    await rail.notify_team_accepted()
    assert rail._subject_windows == {}
    assert called is False


@pytest.mark.asyncio
async def test_induction_retries_the_same_window_once(tmp_path) -> None:
    rail = _rail(tmp_path)
    calls = {"n": 0}

    async def boom(trajectory, ctx, *, snapshot) -> None:
        del trajectory, ctx, snapshot
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("llm down")

    rail._run_ttse_induction = boom  # type: ignore[method-assign]
    rail._trajectory_to_messages = lambda trajectory: [{"role": "user", "content": "q"}]  # type: ignore[method-assign]
    await rail._induce_window(rail, object(), "book a hotel")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_induction_drops_the_window_after_a_second_failure(tmp_path) -> None:
    rail = _rail(tmp_path)
    calls = {"n": 0}

    async def boom(trajectory, ctx, *, snapshot) -> None:
        del trajectory, ctx, snapshot
        calls["n"] += 1
        raise RuntimeError("still down")

    rail._run_ttse_induction = boom  # type: ignore[method-assign]
    rail._trajectory_to_messages = lambda trajectory: []  # type: ignore[method-assign]
    await rail._induce_window(rail, object(), "book a hotel")
    assert calls["n"] == 2


def test_subject_tag_is_only_applied_while_bound() -> None:
    assert tag_ttse_message("[TTSERail] starting induction") == "[TTSERail] starting induction"
    token = bind_induce_subject("leader")
    try:
        assert (
            tag_ttse_message("[TTSERail] starting induction")
            == "[TTSERail subject=leader] starting induction"
        )
        assert tag_ttse_message("[TeamTTSERail] induce begin") == "[TeamTTSERail] induce begin"
    finally:
        reset_induce_subject(token)
    assert current_induce_subject() == ""


def test_member_log_label_keeps_the_member_name() -> None:
    assert _subject_label("leader") == "leader"
    assert (
        _subject_label("member", "team-member:web:jiuwen_team:chart-visualizer")
        == "member:chart-visualizer"
    )


@pytest.mark.asyncio
async def test_window_capabilities_use_mounted_or_called_tools(tmp_path) -> None:
    rail = _rail(tmp_path)
    snapshots: list[dict] = []

    async def fake(trajectory, ctx, *, snapshot) -> str:
        del trajectory, ctx
        snapshots.append(dict(snapshot))
        return "skip"

    class Ability:
        async def list_tool_info(self):
            return [{"name": "spawn_teammate", "description": "staff a teammate"}]

    class Skills:
        def get_all(self):
            return []

    rail._agent = SimpleNamespace(ability_manager=Ability(), skill_manager=Skills())
    rail._run_ttse_induction = fake  # type: ignore[method-assign]
    rail._trajectory_to_messages = lambda trajectory: [  # type: ignore[method-assign]
        {"role": "assistant", "tool_calls": [{"name": "spawn_teammate"}]}
    ]
    await rail._induce_window(rail, object(), "plan the work", subject="leader")
    leader_caps = snapshots[-1]["ttse_capabilities"]
    assert "`spawn_teammate`" in leader_caps
    assert "`python_exec`" not in leader_caps
    assert "`bash`" not in leader_caps

    rail._member_writer._run_ttse_induction = fake  # type: ignore[method-assign]
    rail._member_writer._trajectory_to_messages = lambda trajectory: [  # type: ignore[method-assign]
        {"role": "assistant", "tool_calls": [{"name": "claim_task"}]}
    ]
    await rail._induce_window(rail._member_writer, object(), "plan the work", subject="member:coder")
    member_caps = snapshots[-1]["ttse_capabilities"]
    assert "`claim_task`" in member_caps
    assert "`spawn_teammate`" not in member_caps
    assert "`python_exec`" not in member_caps


@pytest.mark.asyncio
async def test_induce_round_keeps_the_other_window_when_one_crashes(tmp_path) -> None:
    rail = _rail(tmp_path)
    seen: list[str] = []

    async def window(writer, trajectory, query, *, subject: str = "") -> None:
        del writer, trajectory, query
        if subject == "leader":
            raise RuntimeError("leader projection")
        seen.append(subject)

    rail._induce_window = window  # type: ignore[method-assign]
    await rail._induce_round(object(), {"coder": object()}, "book a hotel")
    assert seen == ["member:coder"]


@pytest.mark.asyncio
async def test_window_projection_error_stays_inside_the_window(tmp_path) -> None:
    rail = _rail(tmp_path)

    def boom(trajectory):
        del trajectory
        raise RuntimeError("bad trajectory")

    rail._trajectory_to_messages = boom  # type: ignore[method-assign]
    await rail._induce_window(rail, object(), "book a hotel", subject="member:coder")


@pytest.mark.asyncio
async def test_induce_round_tags_leader_and_each_member(tmp_path) -> None:
    rail = _rail(tmp_path)
    seen: list[str] = []
    notes: list[str] = []

    async def fake(trajectory, ctx, *, snapshot) -> str:
        del trajectory, ctx
        seen.append(current_induce_subject())
        notes.append(str(snapshot.get("ttse_role_note") or ""))
        return "skip"

    rail._run_ttse_induction = fake  # type: ignore[method-assign]
    rail._member_writer._run_ttse_induction = fake  # type: ignore[method-assign]
    rail._trajectory_to_messages = lambda trajectory: []  # type: ignore[method-assign]
    rail._member_writer._trajectory_to_messages = lambda trajectory: []  # type: ignore[method-assign]
    await rail._induce_round(object(), {"team-member:web:team:coder": object()}, "book a hotel")
    assert set(seen) == {"leader", "member:coder"}
    assert current_induce_subject() == ""
    assert sum(bool(note) for note in notes) == 1
    assert any("team leader" in note for note in notes)

    seen.clear()
    await rail._induce_round(None, {}, "book a hotel")
    assert seen == []


def _invoke_ctx() -> AgentCallbackContext:
    return AgentCallbackContext(
        agent=SimpleNamespace(card=SimpleNamespace(id="leader")),
        event=AgentCallbackEvent.BEFORE_INVOKE,
        inputs=InvokeInputs(query="collect the weather", conversation_id="s1"),
        session=None,
    )


def _member_tool_span() -> ReadableSpan:
    context = SpanContext(
        trace_id=1,
        span_id=7,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=TraceState(),
    )
    return ReadableSpan(
        name="tool.write_file",
        context=context,
        resource=Resource.create({SESSION_ID: "s1", TRAJECTORY_ID: "exec-1"}),
        kind=SpanKind.INTERNAL,
        attributes={
            semconv.GEN_AI_OPERATION_NAME: "execute_tool",
            semconv.GEN_AI_TOOL_NAME: "write_file",
            OJ_EXECUTION_SUBJECT_KIND: "team_member",
            OJ_EXECUTION_SUBJECT_ID: "team-member:s:t:coder",
        },
        status=Status(StatusCode.OK),
        start_time=1,
        end_time=2,
    )


@pytest.mark.asyncio
async def test_gap_span_stays_on_one_subscription_until_acceptance(tmp_path, monkeypatch) -> None:
    processor = TrajectorySpanProcessor()
    rail = TeamTTSERail(
        llm=object(),
        model="dummy",
        member_store_path=str(tmp_path / "member" / "bank.json"),
        ttse_config=TTSEConfig(store_path=str(tmp_path / "leader" / "bank.json")),
        trajectory_span_processor=processor,
    )
    monkeypatch.setattr(team_ttse_module, "_team_trace", lambda: ("00000000000000000000000000000001", "team-a"))
    captured: dict[str, object] = {}

    async def remember(leader, members, query) -> None:
        del leader, query
        captured["members"] = members

    rail._induce_round = remember  # type: ignore[method-assign]
    ctx = _invoke_ctx()
    await rail.before_invoke(ctx)
    held = next(iter(rail._active_captures))
    await rail.after_invoke(ctx)
    assert list(rail._active_captures) == [held]

    processor.on_end(_member_tool_span())
    await rail.before_invoke(ctx)
    assert list(rail._active_captures) == [held]
    await rail.notify_team_accepted()
    await asyncio.sleep(0)
    window = captured["members"]["team-member:s:t:coder"]
    names = [span.get("name") for span in iter_spans(window)]
    assert names == ["tool.write_file"]
    assert span_attributes(next(iter_spans(window))).get(OJ_EXECUTION_SUBJECT_ID) == "team-member:s:t:coder"
    assert rail._subject_windows == {}

    await rail.after_invoke(ctx)
    assert rail._active_captures == {}
    processor.on_end(_member_tool_span())
    assert processor.drain(held) == (None, ())


def test_leader_catalog_has_no_business_category() -> None:
    text = render_leader_catalog_markdown(6, 9)
    assert "`all`" in text
    assert "skill-agent-meta-workflows" not in text
    assert "FACT 6" in text
    assert "TIP 9" in text
    assert "(empty)" in render_leader_catalog_markdown(0, 0)


def test_leader_guidance_tells_consult_all(tmp_path) -> None:
    rail = _rail(tmp_path)
    section = rail._catalog_guidance_section()
    assert "category` 填 `all`" in section.content["cn"]
    assert rail._consult_category_description.startswith("Always `all`")
    assert rail._member_writer._consult_tool_description is None
    assert rail._member_writer._classify_added_rules.__func__ is TTSERail._classify_added_rules


@pytest.mark.asyncio
async def test_leader_skips_category_assignment(tmp_path, monkeypatch) -> None:
    rail = _rail(tmp_path)
    called = {"n": 0}

    async def classify_rules(*_args, **_kwargs):
        called["n"] += 1
        return []

    monkeypatch.setattr(
        "openjiuwen.harness.rails.evolution.ttse_rail.classify_rules",
        classify_rules,
    )
    await rail._classify_added_rules([("leader keeps this uncategorized", "fact")])
    assert called["n"] == 0
    await rail._member_writer._classify_added_rules([])
    assert called["n"] == 1


def test_leader_catalog_projection_drops_category_dirs(tmp_path) -> None:
    rail = _rail(tmp_path)
    root = tmp_path / "leader"
    by_cat = root / "by_cat" / "skill-agent-meta-workflows"
    by_cat.mkdir(parents=True)
    (by_cat / "SUMMARY.md").write_text("old\n", encoding="utf-8")
    rail._project_leader_catalog()
    catalog = (root / "CATALOG.md").read_text(encoding="utf-8")
    assert "`all`" in catalog
    assert "(empty)" in catalog
    assert not (root / "by_cat").exists()
    index = (root / "index.json").read_text(encoding="utf-8")
    assert '"uncategorized": true' in index
