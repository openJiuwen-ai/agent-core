# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The Reward package as the Reward stage of the attribution pipeline."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_evolving.attribution import run as run_module
from openjiuwen.agent_evolving.attribution.model import DiagnosticSignal, SignalSource, TaskParseResult
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode
from openjiuwen.agent_evolving.attribution.reward_stage import reward_adapter as adapter
from openjiuwen.agent_evolving.attribution.run import run_diagnosis

pytestmark = pytest.mark.asyncio

LEADER = "team-member:s:t:team-leader"
MEMBER = "team-member:s:t:writer"


def _fast_result(total: Any = 72) -> dict[str, Any]:
    item = {"category_id": "plan", "category_name": "日程", "comment": ""}
    return {
        "meta": {"warnings": []},
        "total": {"score": total, "summary": "时间表可用，外婆到达时间与约束冲突"},
        "categories": [{"category_id": "plan", "category_name": "日程", "comment": "一处冲突", "score": 70}],
        "sub_category_items": [
            {**item, "rubric": "每段时间都排得开", "status": "met", "type": "floor"},
            {
                **item,
                "rubric": "外婆到达在 5 点以后",
                "status": "unmet",
                "type": "floor",
                "comment": "16:55 到，早于 5 点",
            },
            {**item, "rubric": "购物清单标出顺路时段", "status": "partial", "type": "floor", "comment": "牛奶缺时段"},
            {**item, "rubric": "预判师傅超时的应对", "status": "unmet", "type": "proactive_challenge"},
            {**item, "rubric": "泳镜货源可靠", "status": "unknown", "type": "floor"},
        ],
    }


def _node(key: str, seq: int, ts: int, kind: str) -> TrajectoryNode:
    return TrajectoryNode(
        node_id=f"{key}:{seq}",
        subagent_key=key,
        display_name=key,
        kind=kind,
        role="assistant",
        content="x",
        start_ts_ns=ts,
        end_ts_ns=ts + 1,
    )


def _nodes() -> dict[str, list[TrajectoryNode]]:
    return {
        LEADER: [_node(LEADER, 1, 10, "team_leader"), _node(LEADER, 2, 90, "team_leader")],
        MEMBER: [_node(MEMBER, 1, 95, "team_member")],
    }


def _task_parse(tmp_path: Any) -> TaskParseResult:
    deliverable = tmp_path / "schedule.md"
    deliverable.write_text("# 周六", encoding="utf-8")
    return TaskParseResult(
        task_description="安排周六",
        message_artifacts=[(f"{LEADER}:1", "团队已组建"), (f"{MEMBER}:1", "已写入"), (f"{LEADER}:2", "最终答复")],
        file_artifacts=[str(deliverable), str(tmp_path / "missing.md")],
        file_candidates=[],
        user_request="帮我安排周六：外婆 5 点以后有空",
    )


def test_claims_become_dimensions_with_tiers_and_unknowns_are_counted() -> None:
    payload = adapter.to_reward_payload(_fast_result(), "zh")
    assert payload["overall"] == {
        "comment": "要求 1/3 满足 · 挑战 0/1 做到。时间表可用，外婆到达时间与约束冲突 另有 1 项因证据不足未能判定。",
        "total_score": 72,
    }
    assert [(d["requirement"], d["score"], d["tier"]) for d in payload["dimensions"]] == [  # what to fix first
        ("[要求] 外婆到达在 5 点以后", 0, "weak"),
        ("[要求] 购物清单标出顺路时段", 75, "fair"),  # each score in its tier's band, as the frontend colours it
        ("[挑战] 预判师傅超时的应对", 75, "fair"),
        ("[要求] 每段时间都排得开", 100, "perfect"),
    ]
    assert payload["dimensions"][0]["comment"] == "16:55 到，早于 5 点"
    assert payload["dimensions"][3]["comment"] == "每段时间都排得开"  # no reason: the claim itself
    assert payload["categories"] == [{"name": "日程", "comment": "一处冲突", "score": 70}]
    with pytest.raises(ValueError):
        adapter.to_reward_payload(_fast_result(total=None))
    with pytest.raises(ValueError):
        adapter.to_reward_payload({"total": {"score": 50}, "sub_category_items": [{"status": "unknown"}]})


def test_final_reply_is_the_last_message_the_user_got(tmp_path: Any) -> None:
    assert adapter.final_reply(_task_parse(tmp_path), _nodes()) == "最终答复"
    bare = TaskParseResult(task_description="t", message_artifacts=[], file_artifacts=[], file_candidates=[])
    nodes = _nodes()
    nodes[LEADER].append(
        TrajectoryNode(
            node_id=f"{LEADER}:3",
            subagent_key=LEADER,
            display_name=LEADER,
            kind="team_leader",
            role="assistant",
            content="两个文件都已发送给你",
            start_ts_ns=99,
            end_ts_ns=100,
        )
    )
    nodes[MEMBER].append(
        TrajectoryNode(
            node_id=f"{MEMBER}:2",
            subagent_key=MEMBER,
            display_name=MEMBER,
            kind="team_member",
            role="assistant",
            content="任务已完成",
            start_ts_ns=120,
            end_ts_ns=121,
        )
    )
    assert adapter.final_reply(bare, nodes) == "两个文件都已发送给你"  # no artifact reported: the leader's last reply


def test_route_from_model_reads_the_core_model_and_refuses_a_bare_one() -> None:
    model = SimpleNamespace(
        model_client_config=SimpleNamespace(
            api_base="https://example.com/v1", api_key="k", client_provider="OpenAI", timeout=300
        ),
        model_config=SimpleNamespace(model_name="qwen3.7-plus", extra_body={"enable_thinking": False}),
    )
    assert adapter.route_from_model(model) == {
        "model": "qwen3.7-plus",
        "base_url": "https://example.com/v1",
        "api_key": "k",
        "provider": "OpenAI",
        "extra_body": {"enable_thinking": False},
    }  # the model's own timeout is not carried over: Reward waits for a slow model
    with pytest.raises(ValueError):
        adapter.route_from_model(object())
    model.model_client_config.supports_vision = True  # declared in JiuwenSwarm's model config: Reward does not probe
    assert adapter.route_from_model(model)["vision"] is True


def test_the_reward_config_sets_no_time_limit(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.delenv("REWARD_CONFIG", raising=False)
    monkeypatch.setenv("REWARD_HOME", str(tmp_path))  # no config file
    monkeypatch.setenv("REWARD_DEADLINE", "600")  # an old setting: no longer read
    model = SimpleNamespace(
        model_client_config=SimpleNamespace(api_base="https://example.com/v1", api_key="k", timeout=300),
        model_config=SimpleNamespace(model_name="glm"),
    )
    config = adapter.reward_config(model)
    assert "deadline_seconds" not in config and "timeout" not in config["producer"]


def _keyword(signals: list[DiagnosticSignal]) -> Any:
    async def fake(*args: Any, **kwargs: Any) -> tuple[list[DiagnosticSignal], dict[str, Any]]:
        del args, kwargs
        return signals, {"mode": "rule"}

    return fake


async def test_reward_diagnosis_keeps_failed_tool_calls(monkeypatch: Any, tmp_path: Any) -> None:
    seen: dict[str, Any] = {}

    async def fake_reward(task: str, artifacts: list[str], config: dict[str, Any], options: Any) -> dict[str, Any]:
        seen.update(task=task, artifacts=artifacts, config=config, options=options)
        return _fast_result()

    from openjiuwen.agent_evolving.reward import pipeline

    monkeypatch.delenv("ATTRIBUTION_REWARD", raising=False)
    monkeypatch.setattr(pipeline, "reward", fake_reward)
    monkeypatch.setattr(adapter, "reward_config", lambda model: {"producer": {}, "judge": {}})
    monkeypatch.setattr(run_module, "build_reward_agent", lambda **kwargs: pytest.fail("the Reward agent ran"))
    nodes = _nodes()
    failed = TrajectoryNode(
        node_id=f"{MEMBER}:2",
        subagent_key=MEMBER,
        display_name=MEMBER,
        kind="team_member",
        role="tool",
        name="write_file",
        content="Error: File has not been fully read yet.",
        start_ts_ns=96,
        end_ts_ns=97,
        error=True,
    )
    nodes[MEMBER].append(failed)
    tool_error = DiagnosticSignal.create(
        "Error: File has not been fully read yet.", source=SignalSource.KEYWORD_TOOL, node_ids=[failed.node_id]
    )
    complaint = DiagnosticSignal.create(
        "没有路径我怎么知道去哪里看文件", source=SignalSource.KEYWORD_USER, node_ids=[f"{LEADER}:1"]
    )
    monkeypatch.setattr(run_module, "run_keyword_diagnosis", _keyword([tool_error, complaint]))
    events: list[Any] = []
    result = await run_diagnosis(
        run_id="r",
        language="zh",
        nodes_by_subagent=nodes,
        user_attribution="外婆的时间要核对",
        task_parse=_task_parse(tmp_path),
        model=object(),
        status_sink=SimpleNamespace(report=events.append),
    )
    # the reviewer's note on top, then the user's words (not the summary)
    assert seen["task"].startswith("## 用户补充（最高优先级") and seen["task"].endswith(
        "帮我安排周六：外婆 5 点以后有空"
    )
    assert seen["artifacts"] == [str(tmp_path / "schedule.md")]  # a missing file is left out
    assert seen["options"].txt_response == "最终答复" and seen["options"].user_note == "外婆的时间要核对"
    assert result.signal_mode == "reward" and result.reward_overall.total_score == 72
    # tiers follow each claim's status, and each score sits in its tier's band (>= 90 / >= 75 / else)
    assert [s.tier for s in result.signals] == ["weak", "fair", "fair", "perfect", "uncertain", "uncertain"]
    assert [s.score for s in result.signals[:4]] == [0, 75, 75, 100]
    # the failed tool call and the user's follow-up still reach attribution
    assert [s.signal_id for s in result.signals[-2:]] == [tool_error.signal_id, complaint.signal_id]
    assert result.run_meta["process_signals_kept"] == 2
    assert result.run_meta["reward"]["engine"] == "reward"
    assert result.reward_payload["schema_version"] == "attribution.reward.v1"
    assert [e.stage for e in events if getattr(e, "stage", "") == "reward"]


async def test_progress_streams_what_reward_does_now(monkeypatch: Any, tmp_path: Any) -> None:
    import asyncio

    from openjiuwen.agent_evolving.reward import pipeline, progress

    async def fake_reward(task: str, artifacts: list[str], config: dict[str, Any], options: Any) -> dict[str, Any]:
        steps = progress.current()
        steps.stage("criteria")
        options.log("3 criteria: plan, list, message; tools: ['read', 'python']")
        steps.stage("evidence", total=3)
        options.log("evidence c1 (python) for plan")
        options.log("evidence c2 (read) for list, message")
        options.log("evidence batch done")
        steps.stage("judging", total=3)
        for name in ("plan", "list", "message"):
            await steps.counted(asyncio.sleep(0), name)
        return _fast_result()

    monkeypatch.delenv("ATTRIBUTION_REWARD", raising=False)
    monkeypatch.setattr(pipeline, "reward", fake_reward)
    monkeypatch.setattr(adapter, "reward_config", lambda model: {})
    monkeypatch.setattr(run_module, "run_keyword_diagnosis", _keyword([]))
    events: list[Any] = []
    await run_diagnosis(
        run_id="r",
        language="zh",
        nodes_by_subagent=_nodes(),
        user_attribution="",
        task_parse=_task_parse(tmp_path),
        model=object(),
        status_sink=SimpleNamespace(report=events.append),
    )
    lines = [e.response_preview for e in events if e.stage == "reward"]
    assert lines[0].startswith("░░░░░░░░░░ 3% · 出题：根据用户的请求写判定项 · 已用 0分")
    assert any("出题完成：3 个维度" in line for line in lines)
    assert any("取证第 1 轮：2 项检查完成（python、read），累计 2 项" in line for line in lines)  # one line a round
    assert any("判定：3/3 个维度已判定" in line for line in lines)
    assert lines[-1].startswith("▓▓▓▓▓▓▓▓▓▓ 100% · 完成：总分 72")
    percents = [int(line.split("% ")[0].split(" ")[-1]) for line in lines]
    assert percents == sorted(percents)  # the bar only moves forward
    assert not isinstance(progress.current(), adapter._ProgressLines)  # the run's progress slot is restored


def test_the_evidence_bar_keeps_moving_while_checks_come() -> None:
    lines: list[str] = []
    steps = adapter._ProgressLines(lines.append, "zh")
    steps.stage("evidence", total=5)
    for index in range(1, 19):  # more checks than two per criterion, one round each
        steps.log(f"evidence c{index} (python) for plan")
        steps.log("evidence batch done")
    percents = [int(line.split("% ")[0].split(" ")[-1]) for line in lines[1:]]
    assert percents == sorted(set(percents))  # every check moves the bar
    assert percents[-1] < 68  # judging starts at 68


async def test_a_failed_reward_falls_back_to_the_process_signals(monkeypatch: Any, tmp_path: Any) -> None:
    async def broken(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ConnectionError("model unreachable")

    from openjiuwen.agent_evolving.reward import pipeline

    monkeypatch.delenv("ATTRIBUTION_REWARD", raising=False)
    monkeypatch.setattr(pipeline, "reward", broken)
    monkeypatch.setattr(adapter, "reward_config", lambda model: {})
    signal = DiagnosticSignal.create("错误", source=SignalSource.KEYWORD_TOOL, node_ids=[f"{LEADER}:1"])
    monkeypatch.setattr(run_module, "run_keyword_diagnosis", _keyword([signal]))
    result = await run_diagnosis(
        run_id="r",
        language="zh",
        nodes_by_subagent=_nodes(),
        user_attribution="",
        task_parse=_task_parse(tmp_path),
        model=object(),
    )
    assert result.signal_mode == "rule_jev" and result.signals == [signal]
    assert result.run_meta["reward"] == {
        "engine": "reward",
        "failed": "ConnectionError",
        "error": "model unreachable",
    }


def test_reward_is_the_default_and_the_reward_agent_stays_one_switch_away(monkeypatch: Any) -> None:
    monkeypatch.setenv("ATTRIBUTION_REWARD", "agent")
    assert adapter.reward_enabled() is False
    monkeypatch.delenv("ATTRIBUTION_REWARD")
    assert adapter.reward_enabled() is True


def test_an_unscored_looks_category_is_named_with_its_reason_not_counted_as_unknown() -> None:
    result = {
        "total": {"score": 80, "summary": "ok"},
        "categories": [
            {"category_id": "story", "category_name": "故事", "score": 80},
            {"category_id": "visual", "category_name": "画面质量", "score": None, "unscored": "blind"},
        ],
        "sub_category_items": [
            {"category_id": "story", "category_name": "故事", "type": "floor", "status": "met", "rubric": "a"},
            {"category_id": "story", "category_name": "故事", "type": "floor", "status": "unknown", "rubric": "b"},
            *[
                {"category_id": "visual", "category_name": "画面质量", "type": "floor", "status": "unknown"}
                for _ in range(6)
            ],
        ],
    }
    comment = adapter.to_reward_payload(result, "zh")["overall"]["comment"]
    assert "另有 1 项因证据不足未能判定" in comment  # the six looks items are not counted here
    assert "画面维度「画面质量」未评：所用模型 - 不支持读图" in comment and "这不代表作品有问题" in comment
    result["meta"] = {"models": {"judge": "deepseek-v4.1-flash"}, "vision": {"judge": False, "art_judge": False}}
    comment = adapter.to_reward_payload(result, "zh")["overall"]["comment"]
    assert "所用模型 deepseek-v4.1-flash 不支持读图，需要看图才能判定的子项也在其中" in comment  # the 1 unknown
    assert "画面维度「画面质量」未评：所用模型 deepseek-v4.1-flash 不支持读图" in comment
    result["categories"][1]["unscored"] = "no_screenshots"
    assert "没有拿到作品的截图" in adapter.to_reward_payload(result, "zh")["overall"]["comment"]


def test_warnings_and_failed_checks_show_in_the_progress_with_their_reason() -> None:
    from openjiuwen.agent_evolving.reward.pipeline import WARNING

    lines: list[str] = []
    steps = adapter._ProgressLines(lines.append, "zh")
    steps.stage("evidence", total=2)
    steps.log(WARNING + "默认浏览没能运行（RuntimeError: no browser could start）")
    steps.log("evidence c1 (browser) for play, visual: RuntimeError: no browser could start")
    steps.log("evidence c2 (python) for play")
    steps.log("evidence c3 (read) for play: skipped: the source of a page is not read: use the page in the browser")
    steps.log("evidence batch done")
    assert "⚠ 默认浏览没能运行（RuntimeError: no browser could start）" in lines[1] and "15%" in lines[1]
    assert "第 1 项检查没能运行（browser）：RuntimeError: no browser could start" in lines[2]  # said at once
    # one line for the round; a check not run by rule is said as such, not as a failure
    assert lines[3].split(" · ")[1] == (
        "取证第 1 轮：2 项检查完成（browser、python），累计 3 项；"
        "1 项按规则不执行（网页作品不读源码，改在浏览器里实际使用）"
    )


def test_files_sent_with_the_reply_are_named_to_the_judges() -> None:
    parse = TaskParseResult(
        task_description="t",
        message_artifacts=[],
        file_artifacts=["/w/page-1.png"],
        file_candidates=[
            ("/w/out/page-1.png", "main:3", "send_file_to_user"),
            ("/w/out/page-2.png", "main:3", "send_file_to_user"),
            ("/w/notes.md", "main:2", "write_file"),
        ],
    )
    reply = adapter.with_sent_files("绘本做好了：page-1.png、page-2.png", parse, "zh")
    assert reply.startswith("绘本做好了") and "已作为附件发送给用户" in reply
    assert "page-1.png、page-2.png）" in reply and "notes.md" not in reply
    bare = TaskParseResult(task_description="t", message_artifacts=[], file_artifacts=[], file_candidates=[])
    assert adapter.with_sent_files("done", bare, "en") == "done"


def test_reward_prompt_requires_the_requested_output_language() -> None:
    from openjiuwen.agent_evolving.attribution.reward_stage.reward_agent import load_reward_prompt

    assert "输出语言：中文" in load_reward_prompt("zh")
    assert "Output language: English" in load_reward_prompt("en")


def test_requested_language_overrides_reward_task_detection() -> None:
    from openjiuwen.agent_evolving.reward import models

    english = models.set_requested_language("en")
    try:
        models.set_language("中文任务")
        assert models.ANSWER_LANGUAGE.get() == "English"
        assert models.is_chinese_answer("中文任务") is False
    finally:
        models.reset_requested_language(english)
        models.ANSWER_LANGUAGE.set(None)

    chinese = models.set_requested_language("zh")
    try:
        models.set_language("An English task")
        assert models.ANSWER_LANGUAGE.get() == "Chinese (简体中文)"
        assert models.is_chinese_answer("An English task") is True
    finally:
        models.reset_requested_language(chinese)
        models.ANSWER_LANGUAGE.set(None)


def test_visual_criterion_follows_the_requested_language() -> None:
    from openjiuwen.agent_evolving.reward import models, pipeline

    english = models.set_requested_language("en")
    try:
        criteria = pipeline.ensure_visual([], "一个图表任务")
        assert criteria[-1]["name"] == "Visual quality"
    finally:
        models.reset_requested_language(english)
        models.ANSWER_LANGUAGE.set(None)

    chinese = models.set_requested_language("zh")
    try:
        criteria = pipeline.ensure_visual([], "a chart task")
        assert criteria[-1]["name"] == "画面质量"
    finally:
        models.reset_requested_language(chinese)
        models.ANSWER_LANGUAGE.set(None)
