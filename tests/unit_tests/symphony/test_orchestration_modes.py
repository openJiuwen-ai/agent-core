import pytest

from openjiuwen.symphony.orchestration import OrchestrationConfig
from openjiuwen.symphony.orchestration.modes import (
    BUILTIN_MODES,
    available_modes,
    is_supported,
    planner_factory,
    register_mode,
    registered_modes,
    unregister_mode,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Keep the process-global registry isolated between tests."""

    for name in registered_modes():
        unregister_mode(name)
    yield
    for name in registered_modes():
        unregister_mode(name)


def test_builtin_modes_are_the_default_surface() -> None:
    assert BUILTIN_MODES == ("fast", "beam")
    assert available_modes() == ("fast", "beam")
    assert registered_modes() == ()


@pytest.mark.parametrize("mode", ["fast", "beam"])
def test_builtin_modes_are_accepted_by_config(mode: str) -> None:
    assert OrchestrationConfig(mode=mode).mode == mode


def test_builtin_modes_have_no_factory() -> None:
    """Built-ins keep their in-service dispatch; only extras go through a factory."""

    assert planner_factory("fast") is None
    assert planner_factory("beam") is None


def test_unregistered_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported orchestration mode: plan"):
        OrchestrationConfig(mode="plan")


def test_error_lists_available_modes() -> None:
    with pytest.raises(ValueError, match="Available modes: fast, beam"):
        OrchestrationConfig(mode="nope")


def test_registered_mode_becomes_available() -> None:
    def factory(**_: object) -> object:
        return object()

    register_mode("plan", factory)

    assert registered_modes() == ("plan",)
    assert available_modes() == ("fast", "beam", "plan")
    assert is_supported("plan")
    assert planner_factory("plan") is factory
    assert OrchestrationConfig(mode="plan").mode == "plan"


def test_registered_mode_is_removed_on_unregister() -> None:
    register_mode("plan", lambda **_: None)
    unregister_mode("plan")

    assert available_modes() == ("fast", "beam")
    with pytest.raises(ValueError):
        OrchestrationConfig(mode="plan")


def test_unregister_unknown_mode_is_a_no_op() -> None:
    unregister_mode("never-registered")

    assert available_modes() == ("fast", "beam")


@pytest.mark.parametrize("mode", ["fast", "beam"])
def test_builtin_modes_cannot_be_overridden(mode: str) -> None:
    with pytest.raises(ValueError, match="Cannot override built-in orchestration mode"):
        register_mode(mode, lambda **_: None)


@pytest.mark.parametrize("name", ["", "   ", None])
def test_blank_mode_name_is_rejected(name: object) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        register_mode(name, lambda **_: None)  # type: ignore[arg-type]


def test_non_callable_factory_is_rejected() -> None:
    with pytest.raises(TypeError, match="must be callable"):
        register_mode("plan", "not-callable")  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["", "   ", None, "unknown"])
def test_is_supported_rejects_unknown_names(name: object) -> None:
    assert not is_supported(name)  # type: ignore[arg-type]


def test_mode_name_is_trimmed_on_registration() -> None:
    register_mode("  plan  ", lambda **_: None)

    assert is_supported("plan")
    assert available_modes() == ("fast", "beam", "plan")


def test_other_config_validation_is_unchanged() -> None:
    """The refactor must not alter the non-mode invariants."""

    with pytest.raises(ValueError, match="top_k and max_depth must be positive"):
        OrchestrationConfig(top_k=0)
    with pytest.raises(ValueError, match="top_k and max_depth must be positive"):
        OrchestrationConfig(max_depth=0)
    with pytest.raises(ValueError, match="min_edge_confidence must be between 0 and 1"):
        OrchestrationConfig(min_edge_confidence=1.5)
