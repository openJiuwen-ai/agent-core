# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify the public surface of ``openjiuwen.harness.security.skill_authorization``.

Permission semantics (declaration schema, composition, grant records) and
harness-side concerns (skill directory conventions, approval card protocol,
delegated approval routing, config reload) both live in core.
"""

import importlib

import openjiuwen.harness.security.skill_authorization as skill_authorization
from openjiuwen.harness.security.skill_authorization import models as core_models

PACKAGE_PREFIX = "openjiuwen.harness.security.skill_authorization"
EXPECTED_PACKAGE_ALL_COUNT = 40

# Symbols imported by jiuwenclaw directly from the submodule path even though
# they are not part of the package-level ``__all__``.
SUBMODULE_ONLY_SYMBOLS = {
    "grant_store": ["peek_skill_grant_store"],
    "subagent_approval_registry": [
        "ApprovalExpirySender",
        "ApprovalSender",
        "SubagentApprovalCancelled",
        "SubagentApprovalCapacityError",
    ],
}


def test_package_all_is_complete_and_importable():
    assert len(skill_authorization.__all__) == EXPECTED_PACKAGE_ALL_COUNT
    missing = [name for name in skill_authorization.__all__ if not hasattr(skill_authorization, name)]
    assert missing == []


def test_submodule_only_symbols_are_importable():
    for module_name, names in SUBMODULE_ONLY_SYMBOLS.items():
        module = importlib.import_module(f"{PACKAGE_PREFIX}.{module_name}")
        for name in names:
            assert hasattr(module, name), f"{module_name}.{name} is missing"
            assert name not in skill_authorization.__all__


def test_module_all_entries_resolve():
    module_names = [
        "",
        "approval_card",
        "composer",
        "grant_store",
        "manifest",
        "models",
        "reload",
        "schema",
        "subagent_approval_registry",
    ]
    for module_name in module_names:
        full_name = PACKAGE_PREFIX if not module_name else f"{PACKAGE_PREFIX}.{module_name}"
        module = importlib.import_module(full_name)
        for name in getattr(module, "__all__", []):
            assert hasattr(module, name), f"{full_name}.{name} is listed in __all__ but missing"


def test_approval_card_symbols_stay_available_from_models():
    # Historical import path retained for callers using ``...models`` directly.
    assert core_models.SkillApprovalCard is skill_authorization.SkillApprovalCard
    assert core_models.SkillApprovalAction is skill_authorization.SkillApprovalAction
