# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Skill 审批卡协议（Web / CLI 契约）。"""

from __future__ import annotations

from openjiuwen.harness.security.skill_authorization.approval_card import (
    SKILL_APPROVAL_CARD_KIND,
    SKILL_APPROVAL_CARD_VERSION,
    SKILL_APPROVAL_PAYLOAD_SCHEMA,
    SkillApprovalAction,
    SkillApprovalCard,
    SkillPermissionDiff,
)
from openjiuwen.harness.security.skill_authorization.models import SkillTrustLevel


def test_payload_schema_freezes_three_actions() -> None:
    assert SKILL_APPROVAL_PAYLOAD_SCHEMA["required"] == ["action"]
    assert SKILL_APPROVAL_PAYLOAD_SCHEMA["properties"]["action"]["enum"] == [
        action.value for action in SkillApprovalAction
    ]


def test_card_to_dict_is_stable() -> None:
    card = SkillApprovalCard(
        skill_name="demo",
        source="hub",
        version="1.2.3",
        trust=SkillTrustLevel.BUILTIN,
        permissions_hash="abc",
        agent_scope_id="scope-1",
        diff=SkillPermissionDiff(widened=("tools.write_file=ask",), rejected=("tools.bash=allow",)),
        actions=(SkillApprovalAction.APPROVE_ONCE.value,),
    )

    assert card.to_dict() == {
        "kind": SKILL_APPROVAL_CARD_KIND,
        "schema_version": SKILL_APPROVAL_CARD_VERSION,
        "skill_name": "demo",
        "source": "hub",
        "version": "1.2.3",
        "trust": "builtin",
        "permissions_hash": "abc",
        "agent_scope_id": "scope-1",
        "cached_decision": None,
        "diff": {
            "widened": ["tools.write_file=ask"],
            "tightened": [],
            "rejected": ["tools.bash=allow"],
        },
        "actions": ["approve_once"],
    }
