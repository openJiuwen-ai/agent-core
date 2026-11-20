# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""File plugins retain legacy construction and can opt into runtime context."""

import json
from types import SimpleNamespace

import pytest

from openjiuwen.harness.resources.extension_loader import load_plugin_package
from openjiuwen.harness.resources.extension_resolver import resolve_plugin_parts
from openjiuwen.harness.schema.build_context import BuildContext

pytestmark = pytest.mark.level0


def _package(tmp_path, source):
    (tmp_path / "rail.py").write_text(source, encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "package_type": "plugin", "id": "test",
        "rails": [{"file": "rail.py", "class": "ExampleRail"}],
    }), encoding="utf-8")
    return load_plugin_package(manifest)


def test_legacy_file_rail_keeps_zero_argument_constructor(tmp_path):
    spec = _package(tmp_path, """
from openjiuwen.harness.rails.base import DeepAgentRail
class ExampleRail(DeepAgentRail):
    def __init__(self):
        super().__init__()
        self.marker = 'legacy'
""")
    parts = resolve_plugin_parts(spec, BuildContext(extras={"source_root": str(tmp_path)}))
    assert parts.rails[0].marker == "legacy"


def test_context_factory_receives_same_runtime_handle_and_no_manifest_params(tmp_path):
    spec = _package(tmp_path, """
from openjiuwen.harness.rails.base import DeepAgentRail
class ExampleRail(DeepAgentRail):
    @classmethod
    def from_plugin_context(cls, *, params, context):
        assert params == {}
        rail = cls()
        rail.marker = context.extras['runtime']
        return rail
""")
    runtime = object()
    context = BuildContext(extras={"source_root": str(tmp_path), "runtime": runtime})
    assert resolve_plugin_parts(spec, context).rails[0].marker is runtime


@pytest.mark.parametrize("body", ["return object()", "return None"])
def test_factory_rejects_non_rail_result(tmp_path, body):
    spec = _package(tmp_path, f"""
class ExampleRail:
    @classmethod
    def from_plugin_context(cls, *, params, context):
        {body}
""")
    with pytest.raises(TypeError, match="AgentRail"):
        resolve_plugin_parts(spec, BuildContext(extras={"source_root": str(tmp_path)}))


def test_async_factory_is_rejected_without_leaking_coroutine(tmp_path):
    spec = _package(tmp_path, """
class ExampleRail:
    @classmethod
    async def from_plugin_context(cls, *, params, context):
        return None
""")
    with pytest.raises(TypeError, match="synchronous"):
        resolve_plugin_parts(spec, BuildContext(extras={"source_root": str(tmp_path)}))


@pytest.mark.asyncio
async def test_partial_rail_initialization_is_unwound_before_outer_rollback():
    from openjiuwen.harness.extension_binder import _bind_rail
    from openjiuwen.harness.rails.base import DeepAgentRail

    rail = DeepAgentRail()
    resources = []

    async def register(value):
        resources.append(value)
        raise ValueError("init failed after adding a tool")

    async def unregister(value):
        resources.remove(value)

    agent = SimpleNamespace(find_rail_by_name=lambda _: None, register_rail=register, unregister_rail=unregister)
    with pytest.raises(ValueError, match="init failed"):
        await _bind_rail(agent, rail)
    assert resources == []
