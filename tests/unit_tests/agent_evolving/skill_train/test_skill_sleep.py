# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for skill_train sleep mode (JiuwenSwarm trace harvest + SemVer adopt)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore
from openjiuwen.agent_evolving.skill_train.sleep.adopt import adopt_staged_skill_async
from openjiuwen.agent_evolving.skill_train.sleep.backend import MockBackend
from openjiuwen.agent_evolving.skill_train.sleep.config import SleepConfig
from openjiuwen.agent_evolving.skill_train.sleep.consolidate import consolidate
from openjiuwen.agent_evolving.skill_train.sleep.cycle import run_sleep_cycle
from openjiuwen.agent_evolving.skill_train.sleep.harvest import harvest_otlp_trajectories
from openjiuwen.agent_evolving.skill_train.sleep.memory import (
    LEARNED_END,
    LEARNED_START,
    apply_edits_detailed,
    extract_learned,
    set_learned,
)
from openjiuwen.agent_evolving.skill_train.sleep.mine import assign_splits, mine
from openjiuwen.agent_evolving.skill_train.sleep.staging import write_staging
from openjiuwen.agent_evolving.skill_train.sleep.types import EditRecord, SessionDigest, SleepReport, TaskRecord
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map


def _envelope(content: str, *, source: str = "web", msg_type: str = "user input") -> str:
    payload = {
        "source": source,
        "content": content,
        "type": msg_type,
        "timezone": "Asia/Shanghai",
    }
    return "你收到一条消息：\n" + json.dumps(payload, ensure_ascii=False)


def _jiuwenswarm_llm_record(
    *,
    session_id: str,
    span_id: str,
    start: int,
    prompt: list[dict],
    completion: dict | None = None,
) -> dict:
    attributes: dict = {
        "session.id": session_id,
        "agentteam.session.id": session_id,
        "gen_ai.operation.name": "chat",
    }
    for index, message in enumerate(prompt):
        for field, value in message.items():
            attributes[f"langfuse.gen_ai.prompt.{index}.{field}"] = value
    if completion is not None:
        for field, value in completion.items():
            attributes[f"langfuse.gen_ai.completion.0.{field}"] = value
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "a" * 32,
                                "spanId": span_id,
                                "name": "llm.call",
                                "startTimeUnixNano": str(start),
                                "endTimeUnixNano": str(start + 1),
                                "attributes": attributes_from_map(attributes),
                            }
                        ]
                    }
                ],
            }
        ]
    }


def _jiuwenswarm_tool_record(
    *,
    session_id: str,
    span_id: str,
    start: int,
    tool_name: str,
    tool_input: dict,
) -> dict:
    attributes = {
        "session.id": session_id,
        "agentteam.session.id": session_id,
        "gen_ai.tool.name": tool_name,
        "gen_ai.tool.input": json.dumps(tool_input, ensure_ascii=False),
    }
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "a" * 32,
                                "spanId": span_id,
                                "name": f"tool.{tool_name}",
                                "startTimeUnixNano": str(start),
                                "endTimeUnixNano": str(start + 1),
                                "attributes": attributes_from_map(attributes),
                            }
                        ]
                    }
                ],
            }
        ]
    }


def test_harvest_jiuwenswarm_traces_by_session(tmp_path: Path) -> None:
    traces = tmp_path / "traces-2026-09-10.jsonl"
    session = "web_abc"
    first_user = _envelope("上海的天气")
    second_user = _envelope("那明天呢")
    records = [
        _jiuwenswarm_llm_record(
            session_id=session,
            span_id="1",
            start=10,
            prompt=[
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": first_user},
            ],
            completion={"role": "assistant", "content": "今天晴"},
        ),
        _jiuwenswarm_tool_record(
            session_id=session,
            span_id="2",
            start=20,
            tool_name="skill_tool",
            tool_input=[[{"skill_name": "weather-zh"}], {"session": "session:web_abc"}],
        ),
        # Later call already includes prior user history; should not duplicate.
        _jiuwenswarm_llm_record(
            session_id=session,
            span_id="3",
            start=30,
            prompt=[
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": first_user},
                {"role": "assistant", "content": "今天晴"},
                {"role": "user", "content": second_user},
            ],
            completion={"role": "assistant", "content": "明天多云"},
        ),
        # Evolution review call later in the same session — must not become main call.
        _jiuwenswarm_llm_record(
            session_id=session,
            span_id="4",
            start=40,
            prompt=[
                {
                    "role": "user",
                    "content": "判断「待判定的用户消息」是否包含对对话中已使用 skill 的被动纠正",
                }
            ],
            completion={"role": "assistant", "content": "{}"},
        ),
        # Prewarm session should be ignored.
        _jiuwenswarm_llm_record(
            session_id="__prewarm___session",
            span_id="5",
            start=5,
            prompt=[{"role": "user", "content": _envelope("hello", source="__prewarm__")}],
            completion={"role": "assistant", "content": "hi"},
        ),
        # No session.id → ignored.
        {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "b" * 32,
                                    "spanId": "6",
                                    "name": "llm.call",
                                    "startTimeUnixNano": "1",
                                    "attributes": attributes_from_map(
                                        {
                                            "langfuse.gen_ai.prompt.0.role": "user",
                                            "langfuse.gen_ai.prompt.0.content": "x",
                                        }
                                    ),
                                }
                            ]
                        }
                    ],
                }
            ]
        },
    ]
    with traces.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    cfg = SleepConfig(trajectory_store_dir=str(tmp_path), project="demo", max_trajectories=10)
    digests = harvest_otlp_trajectories(cfg)
    assert len(digests) == 1
    digest = digests[0]
    assert digest.session_id == session
    assert digest.user_prompts == ["上海的天气", "那明天呢"]
    assert digest.assistant_finals[-1] == "明天多云"
    assert "weather-zh" in digest.skills_used
    assert "skill_tool" in digest.tools_used

    tasks = mine(digests, max_tasks=10, val_fraction=0.0, seed=1)
    assert len(tasks) == 1
    assert tasks[0].intent == "上海的天气"
    assert tasks[0].skill_hint == "weather-zh"


def test_harvest_returns_empty_without_trace_files(tmp_path: Path) -> None:
    # Non-trace files (including legacy trajectories_*.jsonl) are ignored.
    (tmp_path / "trajectories_default.jsonl").write_text("{}\n", encoding="utf-8")
    cfg = SleepConfig(trajectory_store_dir=str(tmp_path), project="demo", max_trajectories=10)
    assert harvest_otlp_trajectories(cfg) == []


def test_mine_accepts_short_cjk_intent() -> None:
    digests = [
        SessionDigest(
            session_id="s1",
            project="p",
            user_prompts=["上海的天气"],
            assistant_finals=["晴"],
            skills_used=["weather-zh"],
            n_user_turns=1,
            n_assistant_turns=1,
        )
    ]
    tasks = mine(digests, max_tasks=10, val_fraction=0.0, seed=1)
    assert len(tasks) == 1
    assert tasks[0].intent == "上海的天气"


def test_mine_and_splits() -> None:
    digests = [
        SessionDigest(
            session_id="s1",
            project="p",
            user_prompts=["Please wrap the final answer in tags for reproducibility"],
            assistant_finals=["ok"],
            feedback_signals=["neg:user_feedback"],
            n_user_turns=1,
            n_assistant_turns=1,
        )
    ]
    tasks = mine(digests, max_tasks=10, val_fraction=0.5, seed=1)
    assert tasks
    assert tasks[0].outcome == "fail"
    assign_splits(tasks, val_fraction=0.5, seed=1)


def test_learned_markers_roundtrip() -> None:
    doc = set_learned("# Skill\n", ["Prefer brief weather fallbacks."])
    assert LEARNED_START in doc
    assert LEARNED_END in doc
    assert "Prefer brief weather fallbacks." in extract_learned(doc)
    updated, applied, unmatched = apply_edits_detailed(
        doc,
        [EditRecord(target="skill", op="add", content="Ask for location when missing.")],
    )
    assert applied and not unmatched
    assert "Ask for location when missing." in extract_learned(updated)
    assert LEARNED_START in updated and LEARNED_END in updated


def test_consolidate_accepts_improving_edit() -> None:
    backend = MockBackend()
    skill = "# Skill\n\nBase instructions.\n"
    tasks = [
        TaskRecord(
            id="task_a",
            project="p",
            intent="wrap answer",
            reference_kind="exact",
            reference="42",
            tags=["rule:wrap-answer"],
            split="train",
        ),
        TaskRecord(
            id="task_b",
            project="p",
            intent="wrap answer again",
            reference_kind="exact",
            reference="42",
            tags=["rule:wrap-answer"],
            split="val",
        ),
    ]
    result = consolidate(
        backend,
        tasks,
        skill,
        "",
        edit_budget=2,
        gate_metric="hard",
        gate_mode="on",
        evolve_skill=True,
        evolve_memory=False,
        night=1,
    )
    assert result.accepted
    assert LEARNED_START in result.new_skill
    assert "Always wrap the final answer" in result.new_skill


def test_consolidate_rejects_when_no_improvement() -> None:
    backend = MockBackend()
    # Skill already contains the rule — reflect finds nothing useful / no gain.
    skill = "# Skill\n\nAlways wrap the final answer in <answer>...</answer> tags.\n"
    tasks = [
        TaskRecord(
            id="task_a",
            project="p",
            intent="wrap",
            reference_kind="exact",
            reference="42",
            tags=["rule:wrap-answer"],
            split="train",
        ),
        TaskRecord(
            id="task_b",
            project="p",
            intent="wrap2",
            reference_kind="exact",
            reference="42",
            tags=["rule:wrap-answer"],
            split="val",
        ),
    ]
    result = consolidate(
        backend,
        tasks,
        skill,
        "",
        gate_metric="hard",
        evolve_skill=True,
        night=1,
    )
    assert result.new_skill == skill
    assert not result.applied_edits or not result.accepted


def test_apply_edits_unmatched() -> None:
    doc = "# Skill\n"
    new_doc, applied, unmatched = apply_edits_detailed(
        doc,
        [EditRecord(target="skill", op="delete", anchor="missing-line")],
    )
    assert applied == []
    assert unmatched
    assert LEARNED_START in new_doc or new_doc.startswith("#")


def test_staging_does_not_touch_skill_root(tmp_path: Path) -> None:
    skill_root = tmp_path / "skills"
    skill_root.mkdir()
    staging_root = tmp_path / "staging"
    report = SleepReport(night=1, project="p", accepted=True, gate_action="accept")
    path = write_staging(
        staging_root / "20260101-000000",
        report=report,
        proposed_skill="# New\n",
        baseline_skill="# Old\n",
        skill_name="demo-skill",
    )
    assert (path / "proposed_SKILL.md").exists()
    assert (path / "manifest.json").exists()
    assert list(skill_root.iterdir()) == []


@pytest.mark.asyncio
async def test_adopt_archives_and_bumps_semver(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    store = EvolutionStore(str(skills_root))
    await store.create_skill("demo-skill", "desc", "original body")
    before = await store.read_skill_content("demo-skill")
    assert "original body" in before
    version0 = await store.resolve_current_version("demo-skill")
    assert version0 == "1.0.0"

    staging = tmp_path / "staging" / "n1"
    write_staging(
        staging,
        report=SleepReport(night=1, project="p", accepted=True, gate_action="accept"),
        proposed_skill="---\nname: demo-skill\ndescription: desc\nversion: 1.0.0\n---\n\n# demo-skill\n\nlearned rule\n",
        baseline_skill=before,
        skill_name="demo-skill",
    )
    result = await adopt_staged_skill_async(staging, store=store, skill_name="demo-skill")
    assert result.previous_version == "1.0.0"
    assert result.new_version == "1.1.0"
    assert result.archived_body is not None

    after = await store.read_skill_content("demo-skill")
    assert "learned rule" in after
    assert await store.resolve_current_version("demo-skill") == "1.1.0"
    archives = store.list_archives("demo-skill")
    assert any("1.0.0" in name for name in archives)


def test_run_sleep_cycle_dry_run_with_seed_tasks(tmp_path: Path) -> None:
    cfg = SleepConfig(
        trajectory_store_dir=str(tmp_path / "traj"),
        state_dir=str(tmp_path / "state"),
        staging_root=str(tmp_path / "staging"),
        project="unit",
        backend="mock",
        gate_metric="hard",
    )
    (tmp_path / "traj").mkdir()
    tasks = [
        TaskRecord(
            id="task_a",
            project="unit",
            intent="wrap answer",
            reference_kind="exact",
            reference="42",
            tags=["rule:wrap-answer"],
            split="train",
            skill_hint="sleep-demo",
        ),
        TaskRecord(
            id="task_b",
            project="unit",
            intent="wrap answer val",
            reference_kind="exact",
            reference="42",
            tags=["rule:wrap-answer"],
            split="val",
            skill_hint="sleep-demo",
        ),
    ]
    outcome = run_sleep_cycle(cfg, dry_run=True, seed_tasks=tasks, backend=MockBackend())
    assert outcome.dry_run
    assert outcome.report.accepted
    assert {row.skill_name for row in outcome.report.skill_groups} == {"sleep-demo"}
    assert outcome.staging_dir is None


def test_group_tasks_by_skill_hint() -> None:
    from openjiuwen.agent_evolving.skill_train.sleep.mine import group_tasks_by_skill_hint

    tasks = [
        TaskRecord(id="a", project="p", intent="one", skill_hint="alpha"),
        TaskRecord(id="b", project="p", intent="two", skill_hint="beta"),
        TaskRecord(id="c", project="p", intent="three", skill_hint=""),
    ]
    groups = group_tasks_by_skill_hint(tasks)
    assert set(groups) == {"alpha", "beta"}
    assert [t.id for t in groups["alpha"]] == ["a"]
    assert "c" not in {t.id for ts in groups.values() for t in ts}


def test_run_sleep_cycle_skips_tasks_without_skill_hint(tmp_path: Path) -> None:
    cfg = SleepConfig(
        trajectory_store_dir=str(tmp_path / "traj"),
        state_dir=str(tmp_path / "state"),
        staging_root=str(tmp_path / "staging"),
        project="unit",
        backend="mock",
        gate_metric="hard",
    )
    (tmp_path / "traj").mkdir()
    tasks = [
        TaskRecord(
            id="orphan",
            project="unit",
            intent="no skill attached",
            reference_kind="exact",
            reference="42",
            tags=["rule:wrap-answer"],
            split="train",
            skill_hint="",
        ),
    ]
    outcome = run_sleep_cycle(cfg, dry_run=True, seed_tasks=tasks, backend=MockBackend())
    assert not outcome.report.accepted
    assert outcome.report.skill_groups == []
    assert any("no_detected_skills_to_update" in note for note in outcome.report.notes)


def test_multi_skill_cycle_dry_run(tmp_path: Path) -> None:
    cfg = SleepConfig(
        trajectory_store_dir=str(tmp_path / "traj"),
        state_dir=str(tmp_path / "state"),
        staging_root=str(tmp_path / "staging"),
        project="unit",
        backend="mock",
        gate_metric="hard",
    )
    (tmp_path / "traj").mkdir()
    tasks = [
        TaskRecord(
            id="a_train",
            project="unit",
            intent="wrap a",
            reference_kind="exact",
            reference="42",
            tags=["rule:wrap-answer"],
            split="train",
            skill_hint="skill-a",
        ),
        TaskRecord(
            id="a_val",
            project="unit",
            intent="wrap a val",
            reference_kind="exact",
            reference="42",
            tags=["rule:wrap-answer"],
            split="val",
            skill_hint="skill-a",
        ),
        TaskRecord(
            id="b_train",
            project="unit",
            intent="wrap b",
            reference_kind="exact",
            reference="42",
            tags=["rule:wrap-answer"],
            split="train",
            skill_hint="skill-b",
        ),
        TaskRecord(
            id="b_val",
            project="unit",
            intent="wrap b val",
            reference_kind="exact",
            reference="42",
            tags=["rule:wrap-answer"],
            split="val",
            skill_hint="skill-b",
        ),
    ]
    outcome = run_sleep_cycle(cfg, dry_run=True, seed_tasks=tasks, backend=MockBackend())
    names = {row.skill_name for row in outcome.report.skill_groups}
    assert names == {"skill-a", "skill-b"}
    assert outcome.report.accepted
    assert all(row.accepted for row in outcome.report.skill_groups)


@pytest.mark.asyncio
async def test_multi_skill_adopt_from_staging(tmp_path: Path) -> None:
    from openjiuwen.agent_evolving.skill_train.sleep.adopt import adopt_all_staged_skills_async

    skills_root = tmp_path / "skills"
    store = EvolutionStore(str(skills_root))
    staging = tmp_path / "staging" / "n1"
    write_staging(
        staging,
        report=SleepReport(night=1, project="p", accepted=True, gate_action="multi_skill_accept"),
        skill_name="",
        skill_proposals={
            "skill-a": "---\nname: skill-a\nversion: 1.0.0\n---\n\n# skill-a\n\nrule-a\n",
            "skill-b": "---\nname: skill-b\nversion: 1.0.0\n---\n\n# skill-b\n\nrule-b\n",
        },
    )
    results = await adopt_all_staged_skills_async(staging, store=store)
    assert {r.skill_name for r in results} == {"skill-a", "skill-b"}
    assert "rule-a" in await store.read_skill_content("skill-a")
    assert "rule-b" in await store.read_skill_content("skill-b")


# --- multi-turn segmentation + structured rubric -------------------------------


def _turn(role: str, content: str, skills: list[str] | None = None) -> dict:
    return {"role": role, "content": content, "skills": list(skills or [])}


def test_classify_user_turn() -> None:
    from openjiuwen.agent_evolving.skill_train.sleep.mine import classify_user_turn

    assert classify_user_turn("你好") == "greeting"
    assert classify_user_turn("谢谢！") == "greeting"
    assert classify_user_turn("hello") == "greeting"
    assert classify_user_turn("这个不对，缺少紫外线指数") == "correction"
    assert classify_user_turn("需要") == "correction"
    assert classify_user_turn("查天气用的什么skill") == "meta"
    assert classify_user_turn("那明天呢") == "followup"
    assert classify_user_turn("郑州的天气") == "task"
    assert classify_user_turn("帮我写一个读取 CSV 并统计列均值的脚本") == "task"


def test_segment_mine_skips_greeting_and_uses_real_request() -> None:
    digest = SessionDigest(
        session_id="s1",
        project="p",
        user_prompts=["你好", "郑州的天气"],
        assistant_finals=["你好！有什么可以帮你？", "郑州：多云 28°C"],
        skills_used=["weather-zh"],
        n_user_turns=2,
        n_assistant_turns=2,
        turns=[
            _turn("user", "你好"),
            _turn("assistant", "你好！有什么可以帮你？"),
            _turn("user", "郑州的天气"),
            _turn("tool", "skill_tool", ["weather-zh"]),
            _turn("assistant", "郑州：多云 28°C"),
        ],
    )
    tasks = mine([digest], max_tasks=10, val_fraction=0.0, seed=1)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.intent == "郑州的天气"
    assert task.reference_kind == "none"
    assert task.reference == ""
    assert task.context_excerpt == ""
    assert task.attempted_solution == "郑州：多云 28°C"
    assert task.skill_hint == "weather-zh"


def test_segment_mine_builds_rubric_from_follow_ups() -> None:
    digest = SessionDigest(
        session_id="s2",
        project="p",
        user_prompts=["上海的天气", "查天气用的什么skill", "这个不对，缺少紫外线指数", "需要"],
        assistant_finals=["上海：晴 26°C", "用的是 weather-zh", "已补充", "好的"],
        skills_used=["weather-zh"],
        n_user_turns=4,
        n_assistant_turns=4,
        turns=[
            _turn("user", "上海的天气"),
            _turn("tool", "skill_tool", ["weather-zh"]),
            _turn("assistant", "上海：晴 26°C"),
            _turn("user", "查天气用的什么skill"),
            _turn("assistant", "用的是 weather-zh"),
            _turn("user", "这个不对，缺少紫外线指数"),
            _turn("assistant", "已补充"),
            _turn("user", "需要"),
            _turn("assistant", "好的"),
        ],
    )
    tasks = mine([digest], max_tasks=10, val_fraction=0.0, seed=1)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.intent == "上海的天气"
    assert task.outcome == "fail"
    assert task.reference_kind == "rubric"
    assert task.reference.startswith("用户任务：上海的天气")
    assert "必须包含：紫外线指数" in task.reference
    assert "需回应用户追问：查天气用的什么skill" in task.reference
    # Bare acknowledgement "需要" is kept as context but not turned into a rubric line.
    assert "- 需要" not in task.reference
    assert "Follow-up constraints" in task.context_excerpt
    assert "缺少紫外线指数" in task.context_excerpt
    assert task.attempted_solution == "好的"
    assert task.skill_hint == "weather-zh"


def test_segment_mine_splits_multiple_requests_and_prefers_segment_skill() -> None:
    digest = SessionDigest(
        session_id="s3",
        project="p",
        user_prompts=["郑州的天气", "帮我把这份表格转成 markdown"],
        assistant_finals=["郑州：多云", "| a | b |"],
        skills_used=["weather-zh", "table-md"],
        n_user_turns=2,
        n_assistant_turns=2,
        turns=[
            _turn("user", "郑州的天气"),
            _turn("tool", "skill_tool", ["weather-zh"]),
            _turn("assistant", "郑州：多云"),
            _turn("user", "帮我把这份表格转成 markdown"),
            _turn("tool", "skill_tool", ["table-md"]),
            _turn("assistant", "| a | b |"),
        ],
    )
    tasks = mine([digest], max_tasks=10, val_fraction=0.0, seed=1)
    by_intent = {task.intent: task for task in tasks}
    assert set(by_intent) == {"郑州的天气", "帮我把这份表格转成 markdown"}
    # Session-level hint is ambiguous (two skills) but segment-level is unique.
    assert by_intent["郑州的天气"].skill_hint == "weather-zh"
    assert by_intent["帮我把这份表格转成 markdown"].skill_hint == "table-md"
    assert by_intent["郑州的天气"].attempted_solution == "郑州：多云"


def test_segment_mine_falls_back_to_user_prompts_without_turns() -> None:
    digest = SessionDigest(
        session_id="s4",
        project="p",
        user_prompts=["你好", "上海的天气", "这个不对，缺少风速"],
        assistant_finals=["你好", "上海：晴", "已补充风速"],
        skills_used=["weather-zh"],
        n_user_turns=3,
        n_assistant_turns=3,
    )
    tasks = mine([digest], max_tasks=10, val_fraction=0.0, seed=1)
    assert len(tasks) == 1
    assert tasks[0].intent == "上海的天气"
    assert "必须包含：风速" in tasks[0].reference
    assert tasks[0].skill_hint == "weather-zh"


def test_harvest_jiuwenswarm_traces_fills_ordered_turns(tmp_path: Path) -> None:
    traces = tmp_path / "traces-2026-09-10.jsonl"
    session = "web_turns"
    records = [
        _jiuwenswarm_llm_record(
            session_id=session,
            span_id="1",
            start=10,
            prompt=[{"role": "user", "content": _envelope("你好")}],
            completion={"role": "assistant", "content": "你好！"},
        ),
        _jiuwenswarm_llm_record(
            session_id=session,
            span_id="2",
            start=20,
            prompt=[
                {"role": "user", "content": _envelope("你好")},
                {"role": "assistant", "content": "你好！"},
                {"role": "user", "content": _envelope("郑州的天气")},
            ],
            completion={"role": "assistant", "content": "郑州：多云"},
        ),
        # Tool call happens after the second user turn was seen.
        _jiuwenswarm_tool_record(
            session_id=session,
            span_id="3",
            start=25,
            tool_name="skill_tool",
            tool_input={"skill_name": "weather-zh"},
        ),
    ]
    with traces.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    cfg = SleepConfig(trajectory_store_dir=str(tmp_path), project="demo", max_trajectories=10)
    digests = harvest_otlp_trajectories(cfg)
    assert len(digests) == 1
    turns = digests[0].turns
    roles = [turn["role"] for turn in turns]
    assert roles == ["user", "assistant", "user", "tool", "assistant"]
    assert turns[2]["content"] == "郑州的天气"
    assert turns[3]["content"] == "skill_tool"
    assert turns[3]["skills"] == ["weather-zh"]

    tasks = mine(digests, max_tasks=10, val_fraction=0.0, seed=1)
    assert [task.intent for task in tasks] == ["郑州的天气"]
    assert tasks[0].skill_hint == "weather-zh"


class _FakeChatClient:
    """Minimal stand-in for ChatLLMClient.chat."""

    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, str]] = []

    def chat(self, *, system: str, user: str, stage: str = "", **_kwargs) -> tuple[str, dict]:
        self.calls.append((stage, user))
        return self.replies.get(stage, ""), {}


def _rubric_task() -> TaskRecord:
    return TaskRecord(
        id="t1",
        project="p",
        intent="上海的天气",
        context_excerpt="Follow-up constraints from the same session:\n- 这个不对，缺少紫外线指数",
        attempted_solution="上海：晴 26°C",
        reference_kind="rubric",
        reference="用户任务：上海的天气\n用户在同一会话中随后提出的要求（回答必须满足）：\n- 必须包含：紫外线指数",
    )


def test_model_backend_synthesize_rubric_and_fallback() -> None:
    from openjiuwen.agent_evolving.skill_train.sleep.backend import ModelBackend

    good = _FakeChatClient(
        {"sleep_rubric": json.dumps({"rubric": ["给出上海当前天气", "包含紫外线指数"]}, ensure_ascii=False)}
    )
    backend = ModelBackend(target_client=good, optimizer_client=good)  # type: ignore[arg-type]
    task = _rubric_task()
    rubric = backend.synthesize_rubric(task)
    assert rubric.splitlines()[0] == "用户任务：上海的天气"
    assert "- 包含紫外线指数" in rubric
    stage, prompt = good.calls[0]
    assert stage == "sleep_rubric"
    assert "缺少紫外线指数" in prompt and "上海：晴 26°C" in prompt

    bad = _FakeChatClient({"sleep_rubric": "not json at all"})
    backend_bad = ModelBackend(target_client=bad, optimizer_client=bad)  # type: ignore[arg-type]
    assert backend_bad.synthesize_rubric(task) == task.reference


def test_model_backend_judge_prompt_includes_task_and_rubric() -> None:
    from openjiuwen.agent_evolving.skill_train.sleep.backend import ModelBackend

    client = _FakeChatClient({"sleep_judge": json.dumps({"score": 0.9, "reason": "ok"})})
    backend = ModelBackend(target_client=client, optimizer_client=client)  # type: ignore[arg-type]
    hard, soft, reason = backend.judge(_rubric_task(), "上海：晴 26°C，紫外线指数 4")
    assert (hard, soft, reason) == (1.0, 0.9, "ok")
    _stage, prompt = client.calls[0]
    assert "# Task\n上海的天气" in prompt
    assert "必须包含：紫外线指数" in prompt
    assert "does NOT need to repeat the rubric" in prompt


def test_run_sleep_cycle_applies_llm_rubric_synthesis(tmp_path: Path) -> None:
    class _RubricBackend(MockBackend):
        def __init__(self) -> None:
            super().__init__()
            self.seen: list[str] = []

        def synthesize_rubric(self, task: TaskRecord) -> str:
            self.seen.append(task.id)
            return task.reference + "\n- synthesized"

    cfg = SleepConfig(
        trajectory_store_dir=str(tmp_path / "traj"),
        state_dir=str(tmp_path / "state"),
        staging_root=str(tmp_path / "staging"),
        project="unit",
        backend="mock",
        rubric_synthesis="llm",
    )
    (tmp_path / "traj").mkdir()
    tasks = [
        TaskRecord(
            id="r1",
            project="unit",
            intent="wrap",
            reference_kind="rubric",
            reference="- base",
            split="train",
            skill_hint="skill-a",
        ),
        TaskRecord(
            id="e1",
            project="unit",
            intent="exact",
            reference_kind="exact",
            reference="42",
            split="val",
            skill_hint="skill-a",
        ),
    ]
    backend = _RubricBackend()
    outcome = run_sleep_cycle(cfg, dry_run=True, seed_tasks=tasks, backend=backend)
    assert backend.seen == ["r1"]
    assert tasks[0].reference.endswith("- synthesized")
    assert tasks[1].reference == "42"
    assert "rubric_synthesized=1" in outcome.report.notes
