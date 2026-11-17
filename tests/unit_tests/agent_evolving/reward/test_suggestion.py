"""Suggestion reads the saved score and stays off unless the only guide is game."""

import json

import pytest

from openjiuwen.agent_evolving.reward import suggestion


def test_personal_assistant_score_does_not_request_a_design():
    policy = suggestion.suggestion_policy({}, ["personal_assistant"])
    assert policy["status"] == "skipped" and policy["reason"] == "domain_not_enabled"


def test_one_game_guide_enables_suggestion():
    policy = suggestion.suggestion_policy({}, ["game"])
    assert policy["status"] == "enabled" and policy["domain"] == "game"


def test_mixed_or_missing_guides_do_not_pick_a_domain():
    assert suggestion.suggestion_policy({}, ["game", "personal_assistant"])["reason"] == "domain_unknown"
    assert suggestion.suggestion_policy({}, [])["reason"] == "domain_unknown"


@pytest.mark.asyncio
async def test_suggestion_reads_the_saved_score(tmp_path):
    result = {"meta": {"task": "做一个能玩的塔防", "guides": ["game"]},
              "total": {"summary": "能玩，开局看不清"},
              "sub_category_items": [{"rubric": "开局能看清目标", "status": "unmet",
                                      "comment": "目标在画面外", "type": "floor"}]}
    suggestion.write_evidence(tmp_path, result)
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))["meta"]["guides"] == ["game"]

    class Model:
        async def json(self, system, user):
            assert "开局能看清目标" in user
            return {"preserve": ["规则能玩"], "improve": ["把目标放进开局画面"], "explore": []}

    text = await suggestion.suggest(tmp_path, Model(), "zh")
    assert "把目标放进开局画面" in text and "规则能玩" in text
