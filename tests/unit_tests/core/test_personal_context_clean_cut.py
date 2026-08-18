from __future__ import annotations

import importlib.util


def test_personal_context_public_contract() -> None:
    from openjiuwen.core.personal_context import PersonalContext
    from openjiuwen.core.personal_context.config import PersonalContextConfig
    from openjiuwen.harness.rails.personal_context import PersonalContextRail

    assert PersonalContext.__name__ == "PersonalContext"
    assert PersonalContextConfig.__name__ == "PersonalContextConfig"
    assert PersonalContextRail.__name__ == "PersonalContextRail"


def test_legacy_module_is_removed() -> None:
    legacy_module = ".".join(("openjiuwen", "core", "proactive" + "_" + "context"))

    assert importlib.util.find_spec(legacy_module) is None
