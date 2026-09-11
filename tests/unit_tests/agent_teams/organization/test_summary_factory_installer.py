# coding: utf-8

"""Tests for the lazy SummaryTeamFactory installer (mirrors expert adapter installer)."""

from openjiuwen.agent_teams.organization.runtime import OrganizationRuntimeManager


class _FakeSummaryTeamFactory:
    """Minimal duck-typed factory to assert the installer handed one through."""

    def __init__(self) -> None:
        self.created = True


def _make_runtime() -> OrganizationRuntimeManager:
    """Construct an OrganizationRuntimeManager without a real team runtime."""
    return OrganizationRuntimeManager(team_runtime_manager=None)


def test_set_summary_team_factory_installer_registers_callback():
    runtime = _make_runtime()
    seen = []

    def installer(org_runtime):
        seen.append(org_runtime)

    runtime.set_summary_team_factory_installer(installer)
    runtime._ensure_summary_factory()

    assert seen == [runtime]


def test_ensure_summary_factory_invokes_installer_once():
    runtime = _make_runtime()
    factory = _FakeSummaryTeamFactory()
    calls = []

    def installer(org_runtime):
        calls.append(1)
        org_runtime.set_summary_team_factory(factory)

    runtime.set_summary_team_factory_installer(installer)
    runtime._ensure_summary_factory()
    runtime._ensure_summary_factory()

    assert calls == [1]
    assert runtime._summary_team_factory is factory


def test_ensure_summary_factory_noop_when_factory_already_set():
    runtime = _make_runtime()
    factory = _FakeSummaryTeamFactory()
    runtime.set_summary_team_factory(factory)
    invoked = []

    def installer(org_runtime):
        invoked.append(1)

    runtime.set_summary_team_factory_installer(installer)
    runtime._ensure_summary_factory()

    assert invoked == []
    assert runtime._summary_team_factory is factory


def test_ensure_summary_factory_noop_without_installer():
    runtime = _make_runtime()
    runtime._ensure_summary_factory()
    assert runtime._summary_team_factory is None
