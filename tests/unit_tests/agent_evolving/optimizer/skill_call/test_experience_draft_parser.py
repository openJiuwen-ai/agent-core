# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for experience draft parsing fallbacks."""

from __future__ import annotations

from openjiuwen.agent_evolving.optimizer.skill_call.experience_draft_parser import (
    parse_experience_draft,
)
from openjiuwen.agent_evolving.signal.base import EvolutionTarget


class TestParseExperienceDraftFallbacks:
    @staticmethod
    def test_invalid_section_falls_back_to_troubleshooting(caplog):
        draft = parse_experience_draft(
            {
                "action": "append",
                "section": "Unknown",
                "target": "body",
                "content": "fix it",
            }
        )
        assert draft is not None
        assert draft.patch.section == "Troubleshooting"
        assert "invalid section" in caplog.text
        assert "Unknown" in caplog.text

    @staticmethod
    def test_invalid_target_falls_back_to_body(caplog):
        draft = parse_experience_draft(
            {
                "action": "append",
                "section": "Instructions",
                "target": "invalid",
                "content": "fix it",
            }
        )
        assert draft is not None
        assert draft.patch.target == EvolutionTarget.BODY
        assert "invalid target" in caplog.text
        assert "invalid" in caplog.text
