# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for update_modes helper module."""

from openjiuwen.agent_evolving.optimizer.skill_document.update_modes import (
    FULL_REWRITE_MINIBATCH_MODE,
    PATCH_MODE,
    REWRITE_MODE,
    describe_item,
    get_payload_items,
    is_full_rewrite_minibatch_mode,
    is_rewrite_mode,
    normalize_update_mode,
    payload_key,
    payload_label,
    set_payload_items,
    short_item_summary,
    truncate_payload,
)


class TestNormalizeUpdateMode:
    @staticmethod
    def test_none_returns_patch():
        assert normalize_update_mode(None) == PATCH_MODE

    @staticmethod
    def test_empty_returns_patch():
        assert normalize_update_mode("") == PATCH_MODE

    @staticmethod
    def test_patch_alias():
        assert normalize_update_mode("patch") == PATCH_MODE
        assert normalize_update_mode("edits") == PATCH_MODE

    @staticmethod
    def test_rewrite_aliases():
        assert normalize_update_mode("rewrite") == REWRITE_MODE
        assert normalize_update_mode("rewrite_from_suggestions") == REWRITE_MODE
        assert normalize_update_mode("suggestions") == REWRITE_MODE
        assert normalize_update_mode("rewrite_suggestions") == REWRITE_MODE

    @staticmethod
    def test_full_rewrite_aliases():
        assert normalize_update_mode("full_rewrite") == FULL_REWRITE_MINIBATCH_MODE
        assert normalize_update_mode("full_rewrite_minibatch") == FULL_REWRITE_MINIBATCH_MODE
        assert normalize_update_mode("minibatch_full_rewrite") == FULL_REWRITE_MINIBATCH_MODE
        assert normalize_update_mode("skill_rewrite_minibatch") == FULL_REWRITE_MINIBATCH_MODE

    @staticmethod
    def test_case_insensitive():
        assert normalize_update_mode("PATCH") == PATCH_MODE
        assert normalize_update_mode("Rewrite") == REWRITE_MODE

    @staticmethod
    def test_unknown_defaults_to_patch():
        assert normalize_update_mode("unknown_mode") == PATCH_MODE


class TestIsRewriteMode:
    @staticmethod
    def test_rewrite():
        assert is_rewrite_mode("rewrite") is True

    @staticmethod
    def test_patch():
        assert is_rewrite_mode("patch") is False

    @staticmethod
    def test_none():
        assert is_rewrite_mode(None) is False


class TestIsFullRewriteMinibatchMode:
    @staticmethod
    def test_full_rewrite():
        assert is_full_rewrite_minibatch_mode("full_rewrite") is True

    @staticmethod
    def test_patch():
        assert is_full_rewrite_minibatch_mode("patch") is False

    @staticmethod
    def test_none():
        assert is_full_rewrite_minibatch_mode(None) is False


class TestPayloadKey:
    @staticmethod
    def test_patch():
        assert payload_key("patch") == "edits"

    @staticmethod
    def test_none():
        assert payload_key(None) == "edits"

    @staticmethod
    def test_rewrite():
        assert payload_key("rewrite") == "revise_suggestions"

    @staticmethod
    def test_full_rewrite():
        assert payload_key("full_rewrite") == "skill_candidates"


class TestPayloadLabel:
    @staticmethod
    def test_patch_plural():
        assert payload_label("patch") == "edits"

    @staticmethod
    def test_patch_singular():
        assert payload_label("patch", singular=True) == "edit"

    @staticmethod
    def test_patch_title():
        assert payload_label("patch", title=True) == "Edits"

    @staticmethod
    def test_rewrite_plural():
        assert payload_label("rewrite") == "suggestions"

    @staticmethod
    def test_full_rewrite_singular_title():
        assert payload_label("full_rewrite", singular=True, title=True) == "Skill Candidate"


class TestGetSetPayloadItems:
    @staticmethod
    def test_get_from_patch_container():
        container = {"edits": [{"op": "append", "content": "x"}]}
        items = get_payload_items(container, "patch")
        assert len(items) == 1
        assert items[0]["op"] == "append"

    @staticmethod
    def test_get_from_none_returns_empty():
        assert get_payload_items(None, "patch") == []

    @staticmethod
    def test_get_from_non_dict_returns_empty():
        assert get_payload_items("not a dict", "patch") == []

    @staticmethod
    def test_get_missing_key_returns_empty():
        assert get_payload_items({}, "patch") == []

    @staticmethod
    def test_get_non_list_value_returns_empty():
        assert get_payload_items({"edits": "not a list"}, "patch") == []

    @staticmethod
    def test_set_items():
        container = {}
        edits = [{"op": "append", "content": "x"}]
        result = set_payload_items(container, edits, "patch")
        assert result["edits"] == edits

    @staticmethod
    def test_set_items_rewrite():
        container = {}
        suggestions = [{"type": "add", "title": "t"}]
        result = set_payload_items(container, suggestions, "rewrite")
        assert result["revise_suggestions"] == suggestions


class TestTruncatePayload:
    @staticmethod
    def test_truncate_to_max():
        container = {"edits": [{"op": "a"}, {"op": "b"}, {"op": "c"}]}
        result = truncate_payload(container, 2, "patch")
        assert len(result["edits"]) == 2

    @staticmethod
    def test_no_truncate_when_under_max():
        container = {"edits": [{"op": "a"}]}
        result = truncate_payload(container, 5, "patch")
        assert len(result["edits"]) == 1

    @staticmethod
    def test_negative_max_no_truncate():
        container = {"edits": [{"op": "a"}, {"op": "b"}]}
        result = truncate_payload(container, -1, "patch")
        assert len(result["edits"]) == 2


class TestDescribeItem:
    @staticmethod
    def test_patch_mode():
        item = {"op": "append", "content": "new section", "target": ""}
        text = describe_item(item, "patch")
        assert "op=append" in text
        assert "content=" in text

    @staticmethod
    def test_patch_with_target():
        item = {"op": "replace", "content": "new", "target": "old"}
        text = describe_item(item, "patch")
        assert "target=" in text

    @staticmethod
    def test_rewrite_mode():
        item = {"type": "add", "title": "New rule", "instruction": "Add X"}
        text = describe_item(item, "rewrite")
        assert "type=add" in text
        assert "title=" in text

    @staticmethod
    def test_full_rewrite_mode():
        item = {"title": "Skill", "change_summary": ["change1"], "new_skill": "content"}
        text = describe_item(item, "full_rewrite")
        assert "title=" in text
        assert "change_summary=" in text

    @staticmethod
    def test_truncation():
        item = {"op": "append", "content": "x" * 300}
        text = describe_item(item, "patch", max_chars=50)
        assert len(text) <= 50
        assert text.endswith("...")

    @staticmethod
    def test_non_dict_returns_empty():
        assert describe_item("not a dict", "patch") == ""


class TestShortItemSummary:
    @staticmethod
    def test_patch_mode():
        item = {"op": "append", "content": "new section", "target": ""}
        result = short_item_summary(item, "patch")
        assert result["op"] == "append"
        assert "content" in result

    @staticmethod
    def test_rewrite_mode():
        item = {"type": "add", "title": "Rule", "instruction": "Do X"}
        result = short_item_summary(item, "rewrite")
        assert result["type"] == "add"
        assert result["title"] == "Rule"

    @staticmethod
    def test_full_rewrite_mode():
        item = {"title": "Skill", "change_summary": ["c1", "c2"], "source_type": "failure"}
        result = short_item_summary(item, "full_rewrite")
        assert result["title"] == "Skill"
        assert result["source_type"] == "failure"

    @staticmethod
    def test_content_truncated():
        item = {"op": "append", "content": "x" * 300, "target": ""}
        result = short_item_summary(item, "patch", max_chars=50)
        assert len(result["content"]) <= 50
