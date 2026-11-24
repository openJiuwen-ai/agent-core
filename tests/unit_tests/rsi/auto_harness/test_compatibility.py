# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compatibility tests for the relocated AutoHarness package."""

import importlib
import sys

import openjiuwen.auto_harness as legacy_auto_harness
import openjiuwen.rsi.harness_rsi.auto_harness as rsi_auto_harness


def test_legacy_package_reexports_rsi_public_api():
    assert legacy_auto_harness.AutoHarnessConfig is rsi_auto_harness.AutoHarnessConfig
    assert legacy_auto_harness.AutoHarnessOrchestrator is rsi_auto_harness.AutoHarnessOrchestrator
    assert (
        legacy_auto_harness.create_auto_harness_orchestrator
        is rsi_auto_harness.create_auto_harness_orchestrator
    )


def test_legacy_submodule_imports_alias_rsi_modules():
    """Old jiuwenswarm import paths must keep working while swarm migrates later."""
    cases = [
        "schema",
        "contexts",
        "pipelines",
        "infra.git_auth",
        "pipelines.extended_evolve_pipeline",
        "stages.activate",
    ]
    for suffix in cases:
        legacy_name = f"openjiuwen.auto_harness.{suffix}"
        rsi_name = f"openjiuwen.rsi.harness_rsi.auto_harness.{suffix}"
        legacy_mod = importlib.import_module(legacy_name)
        rsi_mod = importlib.import_module(rsi_name)
        assert legacy_mod is rsi_mod
        assert sys.modules[legacy_name] is sys.modules[rsi_name]


def test_legacy_jiuwenswarm_head_import_block():
    """Exact import shapes used by jiuwenswarm HEAD before swarm-side migration."""
    from openjiuwen.auto_harness import (  # noqa: F401
        AutoHarnessConfig,
        AutoHarnessOrchestrator,
        create_auto_harness_orchestrator,
    )
    from openjiuwen.auto_harness.contexts import (  # noqa: F401
        TaskContext,
        TaskRuntime,
    )
    from openjiuwen.auto_harness.infra.git_auth import (  # noqa: F401
        build_git_auth_env,
    )
    from openjiuwen.auto_harness.pipelines import (  # noqa: F401
        EXTENDED_EVOLVE_PIPELINE,
        META_EVOLVE_PIPELINE,
    )
    from openjiuwen.auto_harness.pipelines.extended_evolve_pipeline import (  # noqa: F401
        ExtensionTaskPipeline,
    )
    from openjiuwen.auto_harness.schema import (  # noqa: F401
        OptimizationTask,
        load_auto_harness_config,
    )
    from openjiuwen.auto_harness.stages.activate import (  # noqa: F401
        ExtendActivateStage,
    )

    assert AutoHarnessConfig is rsi_auto_harness.AutoHarnessConfig
    assert META_EVOLVE_PIPELINE is not None
    assert callable(load_auto_harness_config)
    assert callable(build_git_auth_env)
