# coding: utf-8
"""Tests for mobile_gui skill_branch parsers."""

from openjiuwen.harness.tools.mobile_gui.skill_branch.parsers import (
    parse_load_skill_images_response,
    parse_planner_json_response,
)


def test_parse_load_skill_images_text_only_gate():
    """Stage 1 can decline images when text is sufficient (empty requests list)."""
    response = """```python
LOAD_SKILL_IMAGES({
  "visual_reference_needed": false,
  "why_not_text_only": "Menu path is clear from text.",
  "requests": []
})
```"""
    parsed, err = parse_load_skill_images_response(response, valid_image_ids={"a"})

    assert err is None
    assert parsed is not None
    assert parsed["visual_reference_needed"] is False
    assert parsed["why_not_text_only"] == "Menu path is clear from text."
    assert parsed["requests"] == []


def test_parse_load_skill_images_with_selection():
    """Stage 1 returns validated image_id requests when visual reference is needed."""
    response = """```python
LOAD_SKILL_IMAGES({
  "visual_reference_needed": true,
  "why_not_text_only": "Need layout reference.",
  "requests": [{"image_id": "landing", "reason": "Shows repo list layout."}]
})
```"""
    parsed, err = parse_load_skill_images_response(
        response,
        valid_image_ids={"landing"},
        max_images=2,
    )

    assert err is None
    assert parsed["visual_reference_needed"] is True
    req = parsed["requests"][0]
    assert req["image_id"] == "landing"
    assert "layout" in req["reason"]


def test_parse_planner_json_response_valid():
    """Stage 2 planner JSON is parsed with required guidance fields."""
    response = """```json
{
  "skill_applicability": "effective",
  "subgoal": "open settings",
  "plan": "Tap the gear icon, then verify settings home.",
  "do_not_do": "Do not tap unrelated icons.",
  "fallback_if_no_progress": "Use search in settings.",
  "expected_state": "Settings home is visible.",
  "completion_scope": "local_only"
}
```"""
    planner, err = parse_planner_json_response(response)

    assert err is None
    assert planner["skill_applicability"] == "effective"
    assert planner["subgoal"] == "open settings"
    assert planner["plan"] == "Tap the gear icon, then verify settings home."
    assert planner["do_not_do"] == "Do not tap unrelated icons."
    assert planner["fallback_if_no_progress"] == "Use search in settings."
    assert planner["expected_state"] == "Settings home is visible."
    assert planner["completion_scope"] == "local_only"


def test_parse_load_skill_images_rejects_empty_response():
    parsed, err = parse_load_skill_images_response("")
    assert parsed is None
    assert "Empty" in (err or "")


def test_parse_load_skill_images_rejects_malformed_wrapper():
    parsed, err = parse_load_skill_images_response("not a LOAD_SKILL_IMAGES call")
    assert parsed is None
    assert "LOAD_SKILL_IMAGES" in (err or "")


def test_parse_load_skill_images_rejects_invalid_json_payload():
    parsed, err = parse_load_skill_images_response(
        '```python\nLOAD_SKILL_IMAGES({not json})\n```'
    )
    assert parsed is None
    assert "JSON" in (err or "")


def test_parse_load_skill_images_rejects_unknown_image_id():
    parsed, err = parse_load_skill_images_response(
        """```python
LOAD_SKILL_IMAGES({
  "visual_reference_needed": true,
  "why_not_text_only": "Need help.",
  "requests": [{"image_id": "missing", "reason": "x"}]
})
```""",
        valid_image_ids={"known"},
    )
    assert parsed is None
    assert "Unknown image_id" in (err or "")


def test_parse_load_skill_images_rejects_exceeding_max_images():
    parsed, err = parse_load_skill_images_response(
        """```python
LOAD_SKILL_IMAGES({
  "visual_reference_needed": true,
  "why_not_text_only": "Need both.",
  "requests": [
    {"image_id": "a", "reason": "one"},
    {"image_id": "b", "reason": "two"},
    {"image_id": "c", "reason": "three"}
  ]
})
```""",
        valid_image_ids={"a", "b", "c"},
        max_images=2,
    )
    assert parsed is None
    assert "at most 2" in (err or "")


def test_parse_load_skill_images_rejects_requests_when_visual_not_needed():
    parsed, err = parse_load_skill_images_response(
        """```python
LOAD_SKILL_IMAGES({
  "visual_reference_needed": false,
  "why_not_text_only": "",
  "requests": [{"image_id": "a", "reason": "x"}]
})
```""",
        valid_image_ids={"a"},
    )
    assert parsed is None
    assert "requests must be empty" in (err or "")


def test_parse_load_skill_images_deduplicates_duplicate_image_ids():
    parsed, err = parse_load_skill_images_response(
        """```python
LOAD_SKILL_IMAGES({
  "visual_reference_needed": true,
  "why_not_text_only": "Dup ids.",
  "requests": [
    {"image_id": "step", "reason": "first"},
    {"image_id": "step", "reason": "second"}
  ]
})
```""",
        valid_image_ids={"step"},
    )
    assert err is None
    assert len(parsed["requests"]) == 1
    assert parsed["requests"][0]["reason"] == "second"


def test_parse_planner_json_response_rejects_empty_subgoal():
    planner, err = parse_planner_json_response(
        """```json
{"skill_applicability": "effective", "subgoal": "", "plan": "p",
 "do_not_do": "d", "fallback_if_no_progress": "f", "expected_state": "e",
 "completion_scope": "local_only"}
```"""
    )
    assert planner is None
    assert "subgoal" in (err or "")


def test_parse_planner_json_response_rejects_invalid_applicability():
    planner, err = parse_planner_json_response(
        """```json
{"skill_applicability": "maybe", "subgoal": "s", "plan": "p",
 "do_not_do": "d", "fallback_if_no_progress": "f", "expected_state": "e",
 "completion_scope": "local_only"}
```"""
    )
    assert planner is None
    assert "skill_applicability" in (err or "")


def test_parse_planner_json_response_rejects_invalid_completion_scope():
    planner, err = parse_planner_json_response(
        """```json
{"skill_applicability": "effective", "subgoal": "s", "plan": "p",
 "do_not_do": "d", "fallback_if_no_progress": "f", "expected_state": "e",
 "completion_scope": "done"}
```"""
    )
    assert planner is None
    assert "completion_scope" in (err or "")
