# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Third-party provider resolution: in-process registration and installed entry points."""

from __future__ import annotations

import hashlib
import sys
import uuid
from importlib.metadata import EntryPoint, PathDistribution
from pathlib import Path
from collections.abc import Iterator
from typing import Any

import pytest

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.deep_agent_spec import BuiltinToolSpec, ModelSpec
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec, PromptSectionSpec
from openjiuwen.harness_protocol import HarnessCapability, HarnessCard, HarnessProvider, ModelChoice, ModelEndpoint
from openjiuwen.harness_providers import (
    HARNESS_PROVIDER_ENTRY_POINT_GROUP,
    available_providers,
    build_harness_context,
    create_harness,
    register_harness_provider,
    resolve_provider,
    unregister_harness_provider,
)
from openjiuwen.harness_providers import factory
from openjiuwen.harness_providers.skills import SkillSource, install_skills
from tests.test_logger import logger

_THIS_MODULE = "tests.unit_tests.harness_providers.test_provider_discovery"


class FakeHarness:
    """Stands in for a harness; records the configuration it was created with."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config


class FakeProvider:
    """A provider without a ``model_config`` hook."""

    @property
    def card(self) -> HarnessCard:
        return HarnessCard(name="fake-cli", implementation_version="0.1.0")

    @staticmethod
    def create(config: dict[str, Any]) -> FakeHarness:
        return FakeHarness(dict(config))


class EndpointAwareProvider(FakeProvider):
    """A provider mapping built-in and endpoint models onto its own fields."""

    @property
    def card(self) -> HarnessCard:
        return HarnessCard(
            name="fake-cli",
            implementation_version="0.1.0",
            capabilities=frozenset({HarnessCapability.ENDPOINT_MODELS}),
        )

    @staticmethod
    def model_config(choice: ModelChoice, *, fallback: ModelChoice | None = None) -> dict[str, Any]:
        _ = fallback
        fragment: dict[str, Any] = {"model_name": choice.model}
        if choice.endpoint is not None:
            fragment.update(base_url=choice.endpoint.api_base, token=choice.endpoint.api_key)
        return fragment


class BuiltinModelProvider(FakeProvider):
    """A provider running only models its CLI offers on its own login."""

    @staticmethod
    def model_config(choice: ModelChoice, *, fallback: ModelChoice | None = None) -> dict[str, Any]:
        _ = fallback
        return {"model": choice.model}


def _manifest(**overrides: Any) -> AgentTemplateSpec:
    values: dict[str, Any] = {
        "agent_card": AgentCard(id="expert", name="expert", description="An expert agent"),
        "model": ModelSpec(
            model_client_config=ModelClientConfig(client_provider="openai", api_key="secret", api_base="https://llm"),
            model_request_config=ModelRequestConfig(model="gpt-x"),
        ),
        "prompt_sections": [PromptSectionSpec(name="identity", content={"en": "You are an expert."})],
    }
    values.update(overrides)
    return AgentTemplateSpec(**values)


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> list[EntryPoint]:
    """Replace the installed entry points with a controllable list."""
    entries: list[EntryPoint] = []
    monkeypatch.setattr(factory, "_provider_entry_points", lambda: tuple(entries))
    return entries


def test_entry_point_group_is_the_published_name() -> None:
    assert HARNESS_PROVIDER_ENTRY_POINT_GROUP == "openjiuwen.harness_providers"


def test_installed_entry_point_resolves_and_creates_harnesses(installed: list[EntryPoint]) -> None:
    installed.append(EntryPoint("fakecli", f"{_THIS_MODULE}:FakeProvider", HARNESS_PROVIDER_ENTRY_POINT_GROUP))
    provider = resolve_provider("fakecli")
    assert isinstance(provider, HarnessProvider)
    assert provider.card.name == "fake-cli"
    assert "fakecli" in available_providers()
    harness = create_harness(_manifest(model=None), provider="fakecli", config={"cwd": "/w"})
    logger.info("third-party config: %s", harness.config)
    assert isinstance(harness, FakeHarness)
    assert harness.config == {"cwd": "/w"}
    # A provider without model_config cannot take the manifest's model.
    with pytest.raises(ValueError, match="does not configure models"):
        create_harness(_manifest(), provider="fakecli")


def test_model_config_maps_the_endpoint_and_explicit_config_wins(installed: list[EntryPoint]) -> None:
    installed.append(EntryPoint("aware", f"{_THIS_MODULE}:EndpointAwareProvider", HARNESS_PROVIDER_ENTRY_POINT_GROUP))
    harness = create_harness(_manifest(), provider="aware", config={"token": "override"})
    assert harness.config == {"token": "override", "model_name": "gpt-x", "base_url": "https://llm"}


def test_endpoint_models_need_the_card_capability(installed: list[EntryPoint]) -> None:
    installed.append(EntryPoint("builtin", f"{_THIS_MODULE}:BuiltinModelProvider", HARNESS_PROVIDER_ENTRY_POINT_GROUP))
    endpoint = ModelEndpoint(api_base="https://gw", api_key="k", provider="openai")

    assert factory.provider_model_config("builtin", ModelChoice(model="vendor/a")) == {"model": "vendor/a"}
    with pytest.raises(ValueError, match="does not take endpoint models"):
        factory.provider_model_config("builtin", ModelChoice(model="gpt-x", endpoint=endpoint))
    with pytest.raises(ValueError, match="does not take endpoint models"):
        create_harness(_manifest(), provider="builtin")


def test_third_party_providers_reject_deepagent_only_sections(installed: list[EntryPoint]) -> None:
    installed.append(EntryPoint("fakecli", f"{_THIS_MODULE}:FakeProvider", HARNESS_PROVIDER_ENTRY_POINT_GROUP))
    with pytest.raises(ValueError, match="DeepAgent framework"):
        create_harness(_manifest(tools=[BuiltinToolSpec(type="core.web_search")]), provider="fakecli")


def test_third_party_context_renders_the_manifest_prompt(installed: list[EntryPoint]) -> None:
    installed.append(EntryPoint("fakecli", f"{_THIS_MODULE}:FakeProvider", HARNESS_PROVIDER_ENTRY_POINT_GROUP))
    context = build_harness_context(_manifest(), provider="fakecli", host_session_id="h", language="en")
    assert "You are an expert." in context.system_prompt


def test_broken_or_non_provider_entry_points_fail_loudly(installed: list[EntryPoint]) -> None:
    installed.append(EntryPoint("broken", "no_such_module_for_tests:Provider", HARNESS_PROVIDER_ENTRY_POINT_GROUP))
    installed.append(EntryPoint("notprovider", f"{_THIS_MODULE}:FakeHarness", HARNESS_PROVIDER_ENTRY_POINT_GROUP))
    with pytest.raises(ValueError, match="cannot be loaded"):
        resolve_provider("broken")
    with pytest.raises(ValueError, match="could not be instantiated"):
        resolve_provider("notprovider")
    with pytest.raises(ValueError, match="unknown harness provider"):
        resolve_provider("missing")


def test_builtin_names_win_over_installed_entry_points(installed: list[EntryPoint]) -> None:
    installed.append(EntryPoint("codex", f"{_THIS_MODULE}:FakeProvider", HARNESS_PROVIDER_ENTRY_POINT_GROUP))
    assert resolve_provider("codex").card.name == "codex"
    assert available_providers().count("codex") == 1


def test_in_process_registration(installed: list[EntryPoint]) -> None:
    _ = installed
    with pytest.raises(ValueError):
        register_harness_provider("claudecode", FakeProvider)
    register_harness_provider("private", FakeProvider)
    try:
        assert resolve_provider("private").card.name == "fake-cli"
        assert "private" in available_providers()
    finally:
        unregister_harness_provider("private")
    with pytest.raises(ValueError):
        resolve_provider("private")


def test_install_skills_accepts_a_third_party_scan_dir(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: example\ndescription: Test skill\n---\nbody\n")
    project = tmp_path / "project"
    project.mkdir()
    installed = install_skills((SkillSource(str(source)),), provider="fake-cli", cwd=str(project), scan_dir=".fake/skills")
    assert installed == (project / ".fake" / "skills" / "example",)
    with pytest.raises(ValueError, match="no skill discovery directory"):
        install_skills((SkillSource(str(source)),), provider="fake-cli", cwd=str(project))
    with pytest.raises(ValueError, match="escapes"):
        install_skills((SkillSource(str(source)),), provider="fake-cli", cwd=str(project), scan_dir="../outside")


def _install_provider(site: Path, package: str, version: str) -> list[EntryPoint]:
    """Write (or overwrite) an installed provider distribution under ``site``."""
    source = (
        "from openjiuwen.harness_protocol import HarnessCard\n\n\n"
        "class Provider:\n"
        "    @property\n"
        "    def card(self):\n"
        f"        return HarnessCard(name='hot', implementation_version={version!r})\n\n"
        "    @staticmethod\n"
        "    def create(config):\n"
        "        return config\n"
    )
    (site / package).mkdir(exist_ok=True)
    (site / package / "__init__.py").write_text(source, encoding="utf-8")
    info = site / f"{package}-0.1.0.dist-info"
    info.mkdir(exist_ok=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {package}\nVersion: 0.1.0\n", encoding="utf-8")
    (info / "entry_points.txt").write_text(
        f"[{HARNESS_PROVIDER_ENTRY_POINT_GROUP}]\nhot = {package}:Provider\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(source.encode()).hexdigest()
    (info / "RECORD").write_text(
        f"{package}/__init__.py,sha256={digest},{len(source)}\n{package}-0.1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    return list(PathDistribution(info).entry_points)


@pytest.fixture
def site_packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A private site-packages on ``sys.path``, as a frozen host extracts provider wheels."""
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    yield tmp_path
    for name in [name for name in sys.modules if name.startswith("hot_provider_")]:
        sys.modules.pop(name, None)


def test_a_provider_replaced_on_disk_is_reimported_for_new_harnesses(
    site_packages: Path,
    installed: list[EntryPoint],
) -> None:
    package = f"hot_provider_{uuid.uuid4().hex}"
    installed[:] = _install_provider(site_packages, package, "1")
    old = resolve_provider("hot")
    assert old.card.implementation_version == "1"

    installed[:] = _install_provider(site_packages, package, "22")
    new = resolve_provider("hot")

    assert new.card.implementation_version == "22"
    # Objects built from the old import keep running the old code.
    assert old.card.implementation_version == "1"
    assert type(new) is not type(old)


def test_an_unchanged_provider_is_not_reimported(site_packages: Path, installed: list[EntryPoint]) -> None:
    package = f"hot_provider_{uuid.uuid4().hex}"
    installed[:] = _install_provider(site_packages, package, "1")
    first = resolve_provider("hot")
    second = resolve_provider("hot")
    assert type(first) is type(second)
