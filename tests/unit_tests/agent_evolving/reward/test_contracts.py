"""Contracts between prompts, parsers, routing and public output; no model or network calls."""

import ast
import inspect
import json
import re
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.reward import checks, evidence_agent, pipeline, prompts

PACKAGE = Path(pipeline.__file__).parent


def json_example(prompt):
    return json.JSONDecoder().raw_decode(prompt.split("Return JSON: ", 1)[1])[0]


def json_fields(value):
    if isinstance(value, dict):
        return set(value) | set().union(*(json_fields(v) for v in value.values()), set())
    if isinstance(value, list):
        return set().union(*(json_fields(v) for v in value), set())
    return set()


def code_strings(*objects):
    return {n.value for obj in objects for n in ast.walk(ast.parse(inspect.getsource(obj)))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def test_major_visual_weakness_only_caps_its_related_challenge():
    criterion = {"claims": [{"level": "floor"}, {"level": "challenge"}, {"level": "challenge"}]}
    verdict = {"critique": [{"issue": "Text overlaps", "severity": "major", "claims": [1, -1, True, 99]}],
               "claims": [{"index": i, "status": "met", "reason": "Observed"} for i in range(3)]}
    result = pipeline.cap_by_critique(criterion, verdict)
    assert [row["status"] for row in result["claims"]] == ["met", "partial", "met"]
    assert "Text overlaps" in result["claims"][1]["reason"]


def test_fallback_never_uses_visual_quality_or_drops_a_concern():
    criteria = [{"id": "flow", "claims": []}, {"id": "quality", "kind": "visual", "claims": []}]
    result = pipeline.merge_feedback(criteria, [{"criterion": "missing", "text": "Runs without a crash",
                                                "from": "process_signal"}])
    assert result[0]["claims"] == [{"text": "Runs without a crash", "level": "floor", "source": "process_signal"}]
    assert result[1]["claims"] == []
    only_looks = pipeline.merge_feedback(criteria[1:], [{"criterion": "missing", "text": "Runs without a crash",
                                                         "from": "process_signal"}])
    assert only_looks[0]["claims"][0]["text"] == "Runs without a crash"     # no other place: kept, not dropped


class Recorder:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    async def json(self, system, user, images=()):
        self.calls.append((system, json.loads(user) if user.startswith("{") else user, list(images)))
        if self.error:
            raise self.error
        return self.response


def context(tmp_path, art, art_seeing=True):
    return {"root": tmp_path, "tools": [], "art_judge": art, "art_seeing": art_seeing,
            "evidence": {"task": "Use the requested monochrome style", "deliverable": "source",
                         "final_reply": "reply", "references": "(not provided)"}}


@pytest.mark.asyncio
async def test_art_judge_receives_task_and_uses_its_own_vision(tmp_path):
    criterion = {"id": "visual", "kind": "visual", "claims": [{"text": "Style fits task", "level": "floor"}]}
    art = Recorder({"critique": [], "claims": [{"index": 0, "status": "met"}], "summary": "Style fits"})
    judge = Recorder(error=AssertionError("ordinary judge must not judge appearance"))
    result = await pipeline.evaluate_terminal(criterion, context(tmp_path, art), None, judge, lambda _: None,
                                              ([], [("page", "image")]), seeing=False)
    assert result["score"] == 100
    assert art.calls[0][1] == {"task": "Use the requested monochrome style", "criterion": criterion}
    assert art.calls[0][2] == [("page", "image")] and judge.calls == []


@pytest.mark.asyncio
async def test_blind_or_missing_visual_evidence_cannot_be_judged_from_source(tmp_path):
    criterion = {"id": "visual", "kind": "visual", "claims": [{"text": "Looks good", "level": "challenge"}]}
    judge = Recorder(error=AssertionError("must not infer appearance from source"))
    for seeing, images in ((False, [("page", "image")]), (True, [])):
        result = await pipeline.evaluate_terminal(criterion, context(tmp_path, judge, seeing), None, judge,
                                                  lambda _: None, ([], images), seeing=True)
        assert result["score"] is None and result["claims"][0]["status"] == "unknown"
    assert judge.calls == []


@pytest.mark.asyncio
async def test_a_stage_that_decides_scores_fails_loudly():
    with pytest.raises(evidence_agent.EvidenceError, match="flow: judge failed: RuntimeError"):
        await pipeline.ask(Recorder(error=RuntimeError("upstream unavailable")), "prompt", "{}", "flow: judge")
    with pytest.raises(evidence_agent.EvidenceError, match="criteria failed"):
        await pipeline.write_criteria("Task", {"producer": {}}, producer=Recorder(error=TimeoutError("late")))
    with pytest.raises(evidence_agent.EvidenceError, match="No criteria"):
        await pipeline.write_criteria("Task", {"producer": {}}, producer=Recorder({"criteria": [{"id": "x"}]}))


@pytest.mark.asyncio
async def test_new_criteria_see_the_earlier_result_so_the_bar_rises():
    earlier = {"categories": [{"category_id": "play", "category_name": "玩法", "rubric": "好玩"}],
               "sub_category_items": [
                   {"category_id": "play", "rubric": "能跳", "type": "floor", "status": "met"},
                   {"category_id": "play", "rubric": "有连击", "type": "proactive_challenge", "status": "met"}]}
    writer = Recorder({"criteria": [{"id": "play", "claims": [{"text": "能跳"}]}]})
    await pipeline.write_criteria("做个游戏", {"producer": {}}, producer=writer, previous_result=earlier)
    system, content, _ = writer.calls[0]
    assert "Earlier evaluation" in system and "Earlier evaluation" in content
    assert '"text": "有连击", "level": "challenge", "status": "met"' in content     # what the work already met
    await pipeline.write_criteria("做个游戏", {"producer": {}}, producer=writer)
    assert "Earlier evaluation" not in writer.calls[1][1]                       # nothing earlier: as before


@pytest.mark.asyncio
async def test_optional_stages_leave_a_warning_instead_of_failing_the_run():
    notes, broken = [], Recorder(error=RuntimeError("stream cut"))
    assert await pipeline.feedback_claims("Task", "Too small", "", [{"id": "quality", "claims": []}], {},
                                          producer=broken, notes=notes) == []
    summary = await pipeline.overall_summary("Task", [{"id": "a", "name": "玩法", "score": 75}], 75, broken,
                                             lambda _: None, notes)
    assert "玩法" in summary and len(notes) == 2 and all("没有完成" in n for n in notes)


@pytest.mark.asyncio
async def test_a_weak_models_criteria_and_feedback_are_read_leniently():
    written = Recorder({"criteria": [
        {"id": "a", "claims": [{"text": "Opens", "level": "proactive"}, {"text": "  "}, "junk"]},
        {"id": "a", "claims": [{"text": "Scores", "level": "challenge"}]},
        {"id": "p", "scope": "process", "claims": [{"text": "Honest"}]}]})
    criteria = await pipeline.write_criteria("Make a report", {"producer": {}}, producer=written)
    assert [(c["id"], [x["level"] for x in c["claims"]]) for c in criteria] == [("a", ["floor"]), ("a-2", ["challenge"])]
    placed = await pipeline.feedback_claims("Task", "Hard to read", "TypeError", [{"id": "quality", "claims": []}], {},
                                            producer=Recorder({"claims": [{"criterion": "quality", "text": "Readable"}]}))
    assert placed == [{"criterion": "quality", "text": "Readable", "from": "user_note"}]   # missing "from": first input


TOPIC_WORDS = ("猫", "楼梯", "两只手", "跳跳猫", "extra hand", "two hands", "pony tail", "ponytail",
               "Plants vs. Zombies", "Knuffle Bunny", "Caterpillar")


def policy_texts():
    return [(name, value) for name, value in vars(prompts).items() if name.isupper() and isinstance(value, str)] + [
        (path.name, path.read_text()) for path in (PACKAGE / "metrics").glob("*.md")]


def test_prompts_and_guides_have_no_task_specific_rules_or_numeric_quality_thresholds():
    # These are execution time budgets, not quality requirements; keep them tied to code constants.
    operational_limits = {f"{checks.PYTHON_SECONDS} s", f"{checks.MAX_WAIT_MS} ms"}
    for name, text in policy_texts():
        for term in TOPIC_WORDS:
            assert term.casefold() not in text.casefold(), (name, term)
        values = set(re.findall(r"\b\d+(?:\.\d+)?\s*(?:ms|s|秒|%|px)\b", text))
        assert values <= operational_limits, (name, values - operational_limits)


def test_input_material_mentions_are_conditional():
    for name, text in policy_texts():
        for match in re.finditer(r"\./_inputs/", text):
            sentence = re.split(r"[.!?]\s+(?=[A-Z])", text[:match.start()])[-1] + text[match.start():]
            sentence = re.split(r"[.!?]\s+(?=[A-Z])", sentence)[0]
            assert re.search(r"when (?:given|the workspace|.*?lists any)|inputs.*?when", sentence, re.S), name


def test_browser_actions_and_check_types_are_implemented():
    actions = set(re.findall(r'"type": "([a-z_]+)"', prompts.BROWSER_ACTIONS))
    implemented = {n.comparators[0].value for n in ast.walk(ast.parse(inspect.getsource(checks.run_experiment)))
                   if isinstance(n, ast.Compare) and isinstance(n.left, ast.Name) and n.left.id == "kind"
                   and isinstance(n.comparators[0], ast.Constant)}
    assert actions <= implemented and implemented - actions <= {"snapshot"}   # an alias models often write
    tools = set(re.findall(r'"tool": "([a-z_]+)"', prompts.CHECK_TOOLS))
    assert tools <= code_strings(checks.run_check)
    assert tools == {name for _, name in evidence_agent.TOOL_BY_FIELD}


