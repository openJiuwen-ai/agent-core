from __future__ import annotations

import importlib.util
from pathlib import Path


def test_personal_context_public_contract() -> None:
    from openjiuwen.harness.personal_context import PersonalContext
    from openjiuwen.harness.personal_context.config import PersonalContextConfig
    from openjiuwen.harness.rails.personal_context import PersonalContextRail

    assert PersonalContext.__name__ == "PersonalContext"
    assert PersonalContextConfig.__name__ == "PersonalContextConfig"
    assert PersonalContextRail.__name__ == "PersonalContextRail"


def test_personal_context_core_package_is_removed() -> None:
    repository_root = Path(__file__).resolve().parents[3]

    assert importlib.util.find_spec("openjiuwen.core.personal_context") is None
    assert not (repository_root / "openjiuwen" / "core" / "personal_context").exists()


def test_legacy_module_is_removed() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    legacy_package = repository_root / "openjiuwen" / "core" / "proactive_context"

    assert not any(legacy_package.rglob("*.py"))
