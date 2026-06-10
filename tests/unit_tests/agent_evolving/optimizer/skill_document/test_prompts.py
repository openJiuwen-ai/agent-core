# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for prompt template loader."""

import pytest

from openjiuwen.agent_evolving.optimizer.skill_document.prompts import (
    load_skill_opt_prompt,
)


REQUIRED_TEMPLATES = [
    "analyst_error",
    "analyst_success",
    "merge_failure",
    "merge_success",
    "merge_final",
    "ranking",
    "slow_update",
    "meta_skill",
]


class TestLoadSkillOptPrompt:
    @staticmethod
    @pytest.mark.parametrize("name", REQUIRED_TEMPLATES)
    def test_load_required_template(name):
        content = load_skill_opt_prompt(name)
        assert isinstance(content, str)
        assert len(content) > 0

    @staticmethod
    def test_nonexistent_raises_file_not_found():
        with pytest.raises(FileNotFoundError, match="not found"):
            load_skill_opt_prompt("nonexistent_template")

    @staticmethod
    def test_analyst_error_has_content():
        content = load_skill_opt_prompt("analyst_error")
        assert "edit" in content.lower() or "patch" in content.lower()

    @staticmethod
    def test_templates_are_nonempty():
        for name in REQUIRED_TEMPLATES:
            content = load_skill_opt_prompt(name)
            assert len(content) > 10, f"Template {name} is too short"
