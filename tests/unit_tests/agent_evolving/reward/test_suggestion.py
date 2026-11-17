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


def test_screenshots_are_spread_from_the_opening_to_the_last():
    shots = [(f"check {n}", "data") for n in range(20)]
    chosen = [label for label, _ in suggestion.spread(shots)]
    assert len(chosen) == 8 and chosen[0] == "check 0" and chosen[-1] == "check 19"


@pytest.mark.asyncio
async def test_the_designer_sees_what_the_scoring_saw(tmp_path):
    result = {"meta": {"task": "做一个能玩的塔防", "guides": ["game"]},
              "total": {"summary": "能玩，开局看不清"},
              "sub_category_items": [{"rubric": "开局能看清目标", "status": "unmet",
                                      "comment": "目标在画面外", "type": "floor"}],
              "evidence": {"play": [{"tool": "browser", "output": "点了开始，敌人从桥上来" + "。" * 3000}]}}
    suggestion.write_evidence(tmp_path, result)
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))["meta"]["guides"] == ["game"]
    materials = {"screenshots": [("check 1, after start", "aGk=")], "knowledge": "塔防要有路线取舍",
                 "deliverable": {"tower.html": "function buildRoads() {}"}}

    class Model:
        vision = True

        async def json(self, system, user, images=()):
            sent = json.loads(user)
            assert "AAA-game creative director" in system
            assert sent["deliverable_text"]["tower.html"] == "function buildRoads() {}"
            assert sent["metric"] == "塔防要有路线取舍" and sent["final_reply"] == "做好了"
            assert "[cut" in sent["checks"][0]["output"] and images == [("check 1, after start", "aGk=")]
            return {"design_plan": {"target": "开局在桥南分出两条路，玩家先决定放行哪一条。",
                                    "realization": "复用 buildRoads，加一个分岔点。",
                                    "validation": "同一开局分别放行两条路，比较敌人路线。"},
                    "preserve": [{"idea": "波次和失败条件", "evidence": "check 1"}],
                    "improve": [{"idea": "桥南分岔", "delta": "短坡道放行后敌人改走近路", "gain": "开局就有取舍",
                                 "existing": "buildRoads 只有一条环路", "evidence": "check 1, after start"}],
                    "explore": []}

    text = await suggestion.suggest(tmp_path, Model(), "zh", materials=materials, final_reply="做好了")
    assert text.startswith("## 本轮主设计") and "### 桥南分岔" in text and "## 实现顺序与技术方案" in text
    assert "## 保留" in text and "整体效果如何验证" in text
