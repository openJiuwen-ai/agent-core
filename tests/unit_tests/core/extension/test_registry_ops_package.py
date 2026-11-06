# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Package methods after Registry installs and Runtime publishes a real package."""

from pathlib import Path

import pytest

from openjiuwen.core.events import Event, HandlerLevel, SessionContext
from openjiuwen.core.extension.management.context import unbind_context
from tests.unit_tests.core.extension.conftest import BUSINESS_EVENT, build_registry, write_package

_BODY = f"""\
def create_extension():
    return Extension()


class Extension:
    def register(self, registrar):
        async def handler(event, ops, next_handler):
            return (
                await ops.package.name(),
                await ops.package.root(),
                ops.package_metadata.package_version,
            )
        registrar.register_event_handler("{BUSINESS_EVENT}", handler, {{}}, "LEVEL")
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [HandlerLevel.USER, HandlerLevel.BUILTIN])
async def test_installed_package_reads_its_runtime_metadata(level: HandlerLevel, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_package(source, "actual.package", version="2.1.0", body=_BODY.replace("LEVEL", level.value))
    store = tmp_path / "installed"
    registry, bus = await build_registry(store)
    session = SessionContext("session", str(source), {"package_id": "forged.package", "root": str(source)})
    event = Event(
        BUSINESS_EVENT,
        {"tool": "Bash", "package_id": "forged.package", "root": source},
    )
    try:
        reports = await registry.install(source)
        assert reports[0].status == "SUCCESS"
        expected_root = (store / "actual.package" / "2.1.0").resolve()

        # The manifest's displayName is Demo; the root is the installed copy, not the source.
        assert await bus.trigger(event, session) == ("actual.package", expected_root, "2.1.0")
        assert expected_root.is_absolute()
        assert expected_root != source.resolve()
        assert (expected_root / "manifest.json").is_file()

        removed = await registry.uninstall("actual.package")
        assert removed.status == "SUCCESS"
        assert registry.runtime.get_active("actual.package") is None
        assert await bus.trigger(event, session) == ("core", event.event_input)
    finally:
        # Release imported extension modules even when an assertion fails.
        if registry.runtime.get_active("actual.package") is not None:
            registry.runtime.unpublish("actual.package", bus)
        unbind_context(bus)
