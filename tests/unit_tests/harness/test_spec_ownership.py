# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Spec ownership / boundary contracts after Spec sink + cold/hot unification."""

from __future__ import annotations

import os
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest

from openjiuwen.agent_teams.rails.registration import (
    ensure_harness_elements_registered,
)
from openjiuwen.harness.manifest import ensure_builtin_elements_registered, get_catalog
from openjiuwen.harness.schema.deep_agent_spec import (
    BuiltinToolSpec,
    DeepAgentSpec,
    RailSpec,
    SubAgentSpec,
    _RAIL_PROVIDER_REGISTRY,
    _SUBAGENT_PROVIDER_REGISTRY,
    _TOOL_PROVIDER_REGISTRY,
)

pytestmark = pytest.mark.level0

_AGENT_CORE_ROOT = Path(__file__).resolve().parents[3]

# Run in a child process: purging ``agent_teams`` from ``sys.modules`` in-process
# leaves SQLModel tables on the shared metadata and breaks later reimports.
_ISOLATION_SNIPPET = """
import sys

for mod_name in list(sys.modules):
    if mod_name.startswith("openjiuwen.agent_teams"):
        del sys.modules[mod_name]
    if mod_name.startswith("openjiuwen.harness.manifest"):
        del sys.modules[mod_name]

import openjiuwen.harness.manifest.registration as reg

reg._REGISTERED = False
from openjiuwen.harness.manifest import ensure_builtin_elements_registered

ensure_builtin_elements_registered()
leaked = [m for m in sys.modules if m.startswith("openjiuwen.agent_teams")]
assert not leaked, leaked
"""


class TestTeamReexportIdentity:
    """Team re-exports are identity-identical to harness Spec types."""

    def test_team_reexports_are_same_classes(self) -> None:
        """agent_teams schema re-exports point at the same harness classes."""
        from openjiuwen.agent_teams.schema.build_context import (
            BuildContext as TeamBuildContext,
        )
        from openjiuwen.agent_teams.schema.deep_agent_spec import (
            BuiltinToolSpec as TeamBuiltinToolSpec,
            DeepAgentSpec as TeamDeepAgentSpec,
            RailSpec as TeamRailSpec,
            SubAgentSpec as TeamSubAgentSpec,
        )
        from openjiuwen.harness.schema.build_context import BuildContext as HarnessBuildContext

        assert TeamDeepAgentSpec is DeepAgentSpec
        assert TeamRailSpec is RailSpec
        assert TeamSubAgentSpec is SubAgentSpec
        assert TeamBuiltinToolSpec is BuiltinToolSpec
        assert TeamBuildContext is HarnessBuildContext


class TestBuildSurfaceAndRetiredSymbols:
    """Leaf + DeepAgentSpec expose .build; retired cold-start symbols are gone."""

    def test_leaf_and_deep_agent_spec_have_build(self) -> None:
        """RailSpec / BuiltinToolSpec / SubAgentSpec / DeepAgentSpec expose .build."""
        assert callable(getattr(RailSpec, "build", None))
        assert callable(getattr(BuiltinToolSpec, "build", None))
        assert callable(getattr(SubAgentSpec, "build", None))
        assert callable(getattr(DeepAgentSpec, "build", None))
        assert callable(getattr(DeepAgentSpec, "resolve_parts", None))

    def test_from_spec_and_planner_are_gone(self) -> None:
        """DeepAgent.from_spec and resources.planner are retired."""
        from openjiuwen.harness import deep_agent as deep_agent_module

        assert not hasattr(deep_agent_module.DeepAgent, "from_spec")
        with pytest.raises(ModuleNotFoundError):
            import_module("openjiuwen.harness.resources.planner")
        assert "expert_harnesses" not in DeepAgentSpec.model_fields


class TestRegistryOwnershipSmoke:
    """Catalog / provider ownership smoke after Spec sink."""

    def test_ask_user_rail_and_subagent_prefix_without_filesystem_tool_group(self) -> None:
        """core.ask_user rail + core.subagent.* registered; no ask_user_tool / filesystem tool group."""
        ensure_builtin_elements_registered()
        catalog = get_catalog()
        assert "core.ask_user" in catalog
        assert "core.ask_user" in _RAIL_PROVIDER_REGISTRY
        assert "core.ask_user_tool" not in catalog
        assert "core.ask_user_tool" not in _TOOL_PROVIDER_REGISTRY
        assert "core.subagent.explore_agent" in catalog
        assert "core.subagent.explore_agent" in _SUBAGENT_PROVIDER_REGISTRY
        assert "core.filesystem" not in catalog
        assert "core.filesystem" not in _TOOL_PROVIDER_REGISTRY

    def test_team_ensure_keeps_dual_subagent_names(self) -> None:
        """After team ensure, core.explore_agent and core.subagent.explore_agent coexist."""
        ensure_harness_elements_registered()
        catalog = get_catalog()
        assert "core.explore_agent" in catalog
        assert "core.subagent.explore_agent" in catalog
        assert "core.explore_agent" in _SUBAGENT_PROVIDER_REGISTRY
        assert "core.subagent.explore_agent" in _SUBAGENT_PROVIDER_REGISTRY

    def test_catalog_keys_are_unique(self) -> None:
        """Catalog remains name-keyed without duplicate keys after ensure."""
        ensure_builtin_elements_registered()
        catalog = get_catalog()
        assert len(catalog) == len(set(catalog.keys()))


class TestEnsureBuiltinIsolation:
    """Harness ensure must not import agent_teams."""

    def test_ensure_builtin_does_not_import_agent_teams(self) -> None:
        """ensure_builtin_elements_registered does not pull openjiuwen.agent_teams."""
        env = os.environ.copy()
        root = str(_AGENT_CORE_ROOT)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = root if not existing else f"{root}{os.pathsep}{existing}"

        result = subprocess.run(
            [sys.executable, "-c", _ISOLATION_SNIPPET],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
