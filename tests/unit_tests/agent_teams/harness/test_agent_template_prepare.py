# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AgentTemplate snapshot (spec) import: DeepAgent API + NativeHarness._prepare.

Covers the in-memory counterpart of the file-backed ``load_agent_template``:
- ``DeepAgent.load_agent_template_spec`` resolves an in-memory snapshot without
  touching disk (no ``source_root``), still sets ``_parent_model``, and wraps
  failures in ``DEEPAGENT_LOAD_AGENT_TEMPLATE_ERROR``.
- ``DeepAgentSpec.agent_template_spec`` round-trips through JSON and is ignored
  by ``resolve_parts``/``build`` (the team harness host is the only consumer).
- ``NativeHarness._prepare`` mounts the snapshot AFTER ``ensure_initialized``
  (template skills must bind the already-initialized SkillUseRail), exactly
  once, and never lets a half-mounted member run: failures propagate with
  ``_prepared`` left False.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.harness.native_harness import NativeHarness
from openjiuwen.harness import deep_agent as deep_agent_module
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec
from tests.unit_tests.agent_teams.harness.fixtures import make_spec


def _snapshot(agent_id: str = "member1", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agent_card": {"id": agent_id, "name": agent_id, "description": "test template"},
        "prompt_sections": [
            {"name": "identity", "content": {"cn": "专家身份", "en": "expert identity"}, "priority": 10}
        ],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# DeepAgentSpec field behavior
# ---------------------------------------------------------------------------


def test_agent_template_spec_field_json_round_trip() -> None:
    """The plain-dict snapshot survives dump/validate unchanged."""
    spec = DeepAgentSpec(agent_template_spec=_snapshot())

    restored = DeepAgentSpec.model_validate_json(spec.model_dump_json())

    assert restored.agent_template_spec == _snapshot()


def test_agent_template_spec_field_defaults_to_none() -> None:
    """Existing constructors stay untouched: the field is None by default."""
    assert DeepAgentSpec().agent_template_spec is None


def test_resolve_parts_ignores_agent_template_spec() -> None:
    """Cold-build paths must not consume the snapshot (host-only field)."""
    spec = DeepAgentSpec(agent_template_spec=_snapshot())

    parts = spec.resolve_parts()

    assert parts.config is not None  # resolve succeeded; snapshot simply ignored


# ---------------------------------------------------------------------------
# DeepAgent.load_agent_template_spec
# ---------------------------------------------------------------------------


class _Host:
    """Minimal stand-in exposing the two collaborators the method touches."""

    deep_config = SimpleNamespace(model="parent-model")

    def _new_extension_context(self, context: BuildContext | None) -> BuildContext:
        if context is None:
            return BuildContext(language="cn")
        return context.derive()

    async def _apply_extension_parts(self, parts: object, *, source_uri: str | None) -> tuple:
        return parts, source_uri


@pytest.mark.asyncio
async def test_load_agent_template_spec_resolves_in_memory_without_source_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-memory resolve: no disk access, no source_root, _parent_model set."""
    template = AgentTemplateSpec.model_validate(_snapshot())
    resolved_parts = object()
    captured: dict[str, object] = {}

    def _resolve(value: AgentTemplateSpec, context: BuildContext) -> object:
        captured["template"] = value
        captured["context"] = context
        return resolved_parts

    monkeypatch.setattr(deep_agent_module, "resolve_agent_template_parts", _resolve)

    result = await DeepAgent.load_agent_template_spec(_Host(), template)  # type: ignore[arg-type]

    assert result == (resolved_parts, None)  # parts forwarded, source_uri=None
    assert captured["template"] is template
    context = captured["context"]
    assert isinstance(context, BuildContext)
    assert context.extras["_parent_model"] == "parent-model"
    assert "source_root" not in context.extras


@pytest.mark.asyncio
async def test_load_agent_template_spec_rejects_relative_paths() -> None:
    """Snapshot paths must be absolute; the real resolver rejects relative ones."""
    template = AgentTemplateSpec.model_validate(_snapshot(skills=[{"dir": "relative/skill"}]))

    with pytest.raises(Exception) as exc_info:
        await DeepAgent.load_agent_template_spec(_Host(), template)  # type: ignore[arg-type]

    assert "relative/skill" in str(exc_info.value) or "absolute" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_load_agent_template_spec_wraps_resolver_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolver failures surface as DEEPAGENT_LOAD_AGENT_TEMPLATE_ERROR wrappers."""

    def _boom(value: AgentTemplateSpec, context: BuildContext) -> object:
        raise ValueError("broken snapshot")

    monkeypatch.setattr(deep_agent_module, "resolve_agent_template_parts", _boom)
    template = AgentTemplateSpec.model_validate(_snapshot())

    with pytest.raises(Exception) as exc_info:
        await DeepAgent.load_agent_template_spec(_Host(), template)  # type: ignore[arg-type]

    assert "broken snapshot" in str(exc_info.value)


# ---------------------------------------------------------------------------
# NativeHarness._prepare snapshot mounting
# ---------------------------------------------------------------------------


def _recorder() -> list[tuple[str, Any]]:
    return []


@pytest.mark.asyncio
async def test_prepare_mounts_snapshot_after_initialize_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Order + idempotency: ensure_initialized first, one snapshot mount."""
    spec = make_spec()
    spec.agent_template_spec = _snapshot()
    harness = NativeHarness(spec)
    calls: list[tuple[str, Any]] = _recorder()

    async def _ensure_initialized() -> None:
        calls.append(("initialize", None))

    async def _load(template: AgentTemplateSpec, *, context: object | None = None) -> object:
        calls.append(("load", (template.agent_card.id, context)))
        return object()

    monkeypatch.setattr(harness, "ensure_initialized", _ensure_initialized)
    monkeypatch.setattr(harness, "load_agent_template_spec", _load)

    await harness._prepare()
    await harness._prepare()

    assert calls == [("initialize", None), ("load", ("member1", harness._build_context))]
    assert harness._prepared is True


@pytest.mark.asyncio
async def test_prepare_without_snapshot_preserves_existing_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None regression: no snapshot -> loader never runs, behavior unchanged."""
    harness = NativeHarness(make_spec())
    calls: list[str] = []

    async def _ensure_initialized() -> None:
        calls.append("initialize")

    async def _unexpected_load(*args: object, **kwargs: object) -> object:
        raise AssertionError("template loader must not run")

    monkeypatch.setattr(harness, "ensure_initialized", _ensure_initialized)
    monkeypatch.setattr(harness, "load_agent_template_spec", _unexpected_load)

    await harness._prepare()

    assert calls == ["initialize"]
    assert harness._prepared is True


@pytest.mark.asyncio
async def test_prepare_invalid_snapshot_blocks_runnable_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A snapshot that fails model_validate propagates; _prepared stays False."""
    spec = make_spec()
    spec.agent_template_spec = {"prompt_sections": "not-a-list"}
    harness = NativeHarness(spec)

    async def _ensure_initialized() -> None:
        return None

    async def _unexpected_load(*args: object, **kwargs: object) -> object:
        raise AssertionError("template loader must not run")

    monkeypatch.setattr(harness, "ensure_initialized", _ensure_initialized)
    monkeypatch.setattr(harness, "load_agent_template_spec", _unexpected_load)

    with pytest.raises(Exception):
        await harness._prepare()

    assert harness._prepared is False


@pytest.mark.asyncio
async def test_prepare_mount_failure_blocks_runnable_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed hot-mount propagates; no retry, no degraded run, _prepared False."""
    spec = make_spec()
    spec.agent_template_spec = _snapshot()
    harness = NativeHarness(spec)

    async def _ensure_initialized() -> None:
        return None

    async def _failing_load(template: AgentTemplateSpec, *, context: object | None = None) -> object:
        raise RuntimeError("bind failed")

    monkeypatch.setattr(harness, "ensure_initialized", _ensure_initialized)
    monkeypatch.setattr(harness, "load_agent_template_spec", _failing_load)

    with pytest.raises(RuntimeError, match="bind failed"):
        await harness._prepare()

    assert harness._prepared is False


@pytest.mark.asyncio
async def test_start_and_run_once_funnel_through_prepare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both model-call entries call _prepare before doing any work."""
    harness = NativeHarness(make_spec())
    calls: list[str] = []

    async def _prepare() -> None:
        calls.append("prepare")
        raise RuntimeError("sentinel")  # abort right after the funnel point

    monkeypatch.setattr(harness, "_prepare", _prepare)

    with pytest.raises(RuntimeError, match="sentinel"):
        await harness.start()
    with pytest.raises(RuntimeError, match="sentinel"):
        await harness.run_once("hi")

    assert calls == ["prepare", "prepare"]
