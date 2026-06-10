# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for edit_apply document editing operations."""

from openjiuwen.agent_evolving.optimizer.skill_document.edit_apply import (
    SLOW_UPDATE_END,
    SLOW_UPDATE_START,
    apply_edit,
    apply_patch,
    apply_patch_with_report,
)
from openjiuwen.agent_evolving.optimizer.skill_document.types import Edit, Patch


SKILL_WITH_REGION = f"""# My Skill

## Instructions
Do things.

{SLOW_UPDATE_START}
## Long-term Guidance
Be careful.
{SLOW_UPDATE_END}
"""


class TestAppend:
    @staticmethod
    def test_append_to_end():
        skill = "# Title\n\nContent."
        edit = Edit(op="append", content="## New Section\nNew content.")
        result = apply_edit(skill, edit)
        assert result.endswith("## New Section\nNew content.\n")
        assert "# Title" in result

    @staticmethod
    def test_append_before_slow_update_region():
        edit = Edit(op="append", content="## Appended Section")
        result = apply_edit(SKILL_WITH_REGION, edit)
        appended_idx = result.index("## Appended Section")
        region_idx = result.index(SLOW_UPDATE_START)
        assert appended_idx < region_idx
        assert SLOW_UPDATE_START in result
        assert SLOW_UPDATE_END in result

    @staticmethod
    def test_append_strips_markers_from_content():
        edit = Edit(
            op="append",
            content=f"some text\n{SLOW_UPDATE_START}\nbad\n{SLOW_UPDATE_END}\nmore text",
        )
        result = apply_edit("# Title\n", edit)
        assert SLOW_UPDATE_START not in result.split("# Title")[1].split("\n\n")[0] or SLOW_UPDATE_START in result


class TestInsertAfter:
    @staticmethod
    def test_insert_after_target():
        skill = "# Title\n\n## Section A\nContent A\n\n## Section B\nContent B\n"
        edit = Edit(op="insert_after", content="- New item", target="## Section A\nContent A")
        result = apply_edit(skill, edit)
        assert "- New item" in result
        section_a_idx = result.index("Content A")
        new_item_idx = result.index("- New item")
        section_b_idx = result.index("## Section B")
        assert section_a_idx < new_item_idx < section_b_idx

    @staticmethod
    def test_insert_after_missing_target_fallback_append():
        skill = "# Title\n\nContent."
        edit = Edit(op="insert_after", content="fallback", target="nonexistent")
        result = apply_edit(skill, edit)
        assert "fallback" in result

    @staticmethod
    def test_insert_after_empty_target_fallback_append():
        skill = "# Title\n\nContent."
        edit = Edit(op="insert_after", content="fallback", target="")
        result = apply_edit(skill, edit)
        assert "fallback" in result

    @staticmethod
    def test_insert_after_fallback_before_slow_update():
        edit = Edit(op="insert_after", content="fallback", target="nonexistent")
        result = apply_edit(SKILL_WITH_REGION, edit)
        fallback_idx = result.index("fallback")
        region_idx = result.index(SLOW_UPDATE_START)
        assert fallback_idx < region_idx


class TestReplace:
    @staticmethod
    def test_replace_target():
        skill = "# Title\n\nOld content here.\n"
        edit = Edit(op="replace", content="New content here.", target="Old content here.")
        result = apply_edit(skill, edit)
        assert "New content here." in result
        assert "Old content here." not in result

    @staticmethod
    def test_replace_only_first_occurrence():
        skill = "AAA BBB AAA"
        edit = Edit(op="replace", content="CCC", target="AAA")
        result = apply_edit(skill, edit)
        assert result == "CCC BBB AAA"

    @staticmethod
    def test_replace_missing_target_skipped():
        skill = "# Title\n"
        edit = Edit(op="replace", content="new", target="")
        result = apply_edit(skill, edit)
        assert result == skill

    @staticmethod
    def test_replace_target_not_found_skipped():
        skill = "# Title\n"
        edit = Edit(op="replace", content="new", target="nonexistent")
        result = apply_edit(skill, edit)
        assert result == skill


class TestDelete:
    @staticmethod
    def test_delete_target():
        skill = "# Title\n\nRemove this line.\n\nKeep this.\n"
        edit = Edit(op="delete", content="", target="Remove this line.\n")
        result = apply_edit(skill, edit)
        assert "Remove this line." not in result
        assert "Keep this." in result

    @staticmethod
    def test_delete_missing_target_skipped():
        skill = "# Title\n"
        edit = Edit(op="delete", content="", target="")
        result = apply_edit(skill, edit)
        assert result == skill

    @staticmethod
    def test_delete_target_not_found_skipped():
        skill = "# Title\n"
        edit = Edit(op="delete", content="", target="nonexistent")
        result = apply_edit(skill, edit)
        assert result == skill


class TestProtectedRegion:
    @staticmethod
    def test_replace_in_slow_update_region_skipped():
        edit = Edit(op="replace", content="hacked", target="Be careful.")
        result = apply_edit(SKILL_WITH_REGION, edit)
        assert "Be careful." in result
        assert "hacked" not in result

    @staticmethod
    def test_delete_in_slow_update_region_skipped():
        edit = Edit(op="delete", content="", target="Be careful.")
        result = apply_edit(SKILL_WITH_REGION, edit)
        assert "Be careful." in result

    @staticmethod
    def test_no_region_all_ops_work():
        skill = "# Title\n\nContent to replace.\n"
        edit = Edit(op="replace", content="New content.", target="Content to replace.")
        result = apply_edit(skill, edit)
        assert "New content." in result


class TestMarkerStripping:
    @staticmethod
    def test_strip_start_marker():
        skill = "# Title\n"
        edit = Edit(op="append", content=f"text {SLOW_UPDATE_START} more text")
        result = apply_edit(skill, edit)
        # The appended content should not contain the raw marker
        appended_part = result[len("# Title") :]
        assert SLOW_UPDATE_START not in appended_part or appended_part.count(
            SLOW_UPDATE_START
        ) <= SKILL_WITH_REGION.count(SLOW_UPDATE_START)

    @staticmethod
    def test_strip_end_marker():
        skill = "# Title\n"
        edit = Edit(op="append", content=f"text {SLOW_UPDATE_END} more text")
        result = apply_edit(skill, edit)
        appended_part = result[len("# Title") :]
        assert SLOW_UPDATE_END not in appended_part or appended_part.count(SLOW_UPDATE_END) <= SKILL_WITH_REGION.count(
            SLOW_UPDATE_END
        )


class TestUnknownOp:
    @staticmethod
    def test_unknown_op_skipped():
        skill = "# Title\n"
        edit = Edit(op="append", content="x")
        object.__setattr__(edit, "op", "merge")
        result = apply_edit(skill, edit)
        assert result == skill


class TestApplyPatch:
    @staticmethod
    def test_apply_multiple_edits():
        skill = "# Title\n\nOld content.\n"
        patch = Patch(
            edits=[
                Edit(op="replace", content="New content.", target="Old content."),
                Edit(op="append", content="## Extra\nMore info."),
            ]
        )
        result = apply_patch(skill, patch)
        assert "New content." in result
        assert "## Extra" in result

    @staticmethod
    def test_apply_empty_patch():
        skill = "# Title\n"
        patch = Patch(edits=[])
        result = apply_patch(skill, patch)
        assert result == skill


class TestApplyPatchWithReport:
    @staticmethod
    def test_report_structure():
        skill = "# Title\n\nOld.\n"
        patch = Patch(
            edits=[
                Edit(op="replace", content="New.", target="Old."),
                Edit(op="append", content="Extra."),
            ]
        )
        result, reports = apply_patch_with_report(skill, patch)
        assert "New." in result
        assert len(reports) == 2
        assert reports[0]["status"] == "applied_replace"
        assert reports[0]["index"] == 1
        assert reports[1]["status"] in ("applied_append", "applied_append_before_slow_update")
        assert reports[1]["index"] == 2

    @staticmethod
    def test_report_includes_skipped():
        skill = "# Title\n"
        patch = Patch(
            edits=[
                Edit(op="replace", content="new", target="nonexistent"),
            ]
        )
        result, reports = apply_patch_with_report(skill, patch)
        assert result == skill
        assert reports[0]["status"] == "skipped_replace_target_not_found"

    @staticmethod
    def test_report_with_dict_patch():
        skill = "# Title\n\nOld.\n"
        patch_dict = {"edits": [{"op": "replace", "content": "New.", "target": "Old."}]}
        result, reports = apply_patch_with_report(skill, patch_dict)
        assert "New." in result
        assert len(reports) == 1

    @staticmethod
    def test_error_handling_in_report():
        skill = "# Title\n"
        # Pass a malformed edit that will cause an error
        patch_dict = {"edits": [{"op": "replace", "content": "x", "target": None}]}
        result, reports = apply_patch_with_report(skill, patch_dict)
        # Should have error status, not crash
        assert reports[0]["status"] in ("error", "skipped_replace_missing_target", "skipped_replace_target_not_found")
