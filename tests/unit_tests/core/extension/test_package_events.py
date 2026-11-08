# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""EX-03: orchestration of the nine package management events."""

import asyncio
import json
from pathlib import Path

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.events.types import Event
from openjiuwen.core.extension import package_manager as package_manager_module
from openjiuwen.core.extension.errors import ExtensionFailure
from openjiuwen.core.extension.install_report import PackageOperationToken
from openjiuwen.core.extension.management import (
    EXTENSION_APPLY_EVENT,
    EXTENSION_PLAN_EVENT,
    EXTENSION_ROLLBACK_EVENT,
    PACKAGE_PARSE_EVENT,
    PACKAGE_QUERY_EVENT,
    PACKAGE_RESOLVE_EVENT,
    ApplyRequest,
    ParseRequest,
    ParseResult,
    PlanRequest,
    QueryRequest,
    QueryResult,
    ResolveRequest,
    ResolveResult,
    RollbackRequest,
)
from openjiuwen.core.extension.manifest import load_manifest
from openjiuwen.core.extension.package_manager import PackageManager
from openjiuwen.core.extension.schema.manifest import PackageDependency
from openjiuwen.core.extension.schema.records import ActivationReport
from tests.unit_tests.core.extension.conftest import (
    build_registry,
    call_business_event,
    write_package,
)

_BROKEN_BODY = "def create_extension():\n    raise RuntimeError('boom')\n"


def _source(root: Path, name: str, package_id: str, **kwargs: object) -> Path:
    """Write one package under its own directory and return that directory."""
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    write_package(path, package_id, **kwargs)  # type: ignore[arg-type]
    return path


@pytest.mark.asyncio
async def test_parse_reads_a_source_and_reports_an_invalid_one(tmp_path: Path) -> None:
    source = _source(tmp_path, "source", "demo")
    _, bus = await build_registry(tmp_path / "store")

    parsed = await bus.trigger(Event(PACKAGE_PARSE_EVENT, ParseRequest(bus=bus, source=source)))
    assert isinstance(parsed, ParseResult)
    assert parsed.error is None
    assert parsed.package is not None
    assert parsed.package.manifest.package_id == "demo"
    assert parsed.package.root == source.resolve()

    missing = await bus.trigger(
        Event(PACKAGE_PARSE_EVENT, ParseRequest(bus=bus, source=tmp_path / "absent"))
    )
    assert isinstance(missing, ParseResult)
    assert missing.package is None
    assert missing.error is not None
    assert missing.error.status == StatusCode.EXTENSION_SOURCE_INVALID


@pytest.mark.asyncio
async def test_resolve_orders_an_install_batch_and_keeps_the_reasons(tmp_path: Path) -> None:
    base = load_manifest(_source(tmp_path, "base", "base"))
    child_root = _source(
        tmp_path,
        "child",
        "child",
        requires=(PackageDependency("base", ">=1.0.0"),),
    )
    child = load_manifest(child_root)
    orphan = load_manifest(_source(tmp_path, "orphan", "orphan", requires=(PackageDependency("absent", ">=1.0.0"),)))
    _, bus = await build_registry(tmp_path / "store")

    resolved = await bus.trigger(
        Event(
            PACKAGE_RESOLVE_EVENT,
            ResolveRequest(bus=bus, operation="install", candidates=(child, orphan, base)),
        )
    )

    assert isinstance(resolved, ResolveResult)
    assert resolved.fatal is None
    assert [item.package_id for item in resolved.ordered] == ["base", "child"]
    assert [item.package_id for item in resolved.rejected] == ["orphan"]
    assert resolved.rejected[0].error.status == StatusCode.EXTENSION_DEPENDENCY_NOT_MET


@pytest.mark.asyncio
async def test_resolve_refuses_a_duplicate_batch_and_answers_uninstall(tmp_path: Path) -> None:
    manifest = load_manifest(_source(tmp_path, "source", "demo"))
    registry, bus = await build_registry(tmp_path / "store")

    duplicated = await bus.trigger(
        Event(
            PACKAGE_RESOLVE_EVENT,
            ResolveRequest(bus=bus, operation="install", candidates=(manifest, manifest)),
        )
    )
    assert isinstance(duplicated, ResolveResult)
    assert duplicated.fatal is not None
    assert duplicated.fatal.status == StatusCode.EXTENSION_DUPLICATE_PACKAGE_ID

    absent = await bus.trigger(
        Event(PACKAGE_RESOLVE_EVENT, ResolveRequest(bus=bus, operation="uninstall", package_id="demo"))
    )
    assert isinstance(absent, ResolveResult)
    assert absent.error is not None
    assert absent.error.status == StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED

    assert all(item.status == "SUCCESS" for item in await registry.install([tmp_path / "source"]))
    allowed = await bus.trigger(
        Event(PACKAGE_RESOLVE_EVENT, ResolveRequest(bus=bus, operation="uninstall", package_id="demo"))
    )
    assert isinstance(allowed, ResolveResult)
    assert allowed.error is None


@pytest.mark.asyncio
async def test_plan_requires_an_admitted_operation(tmp_path: Path) -> None:
    source = _source(tmp_path, "source", "demo")
    manifest = load_manifest(source)
    registry, bus = await build_registry(tmp_path / "store")
    forged = PackageOperationToken("run", "process", "demo", "operation")
    with pytest.raises(ExtensionFailure, match="stale"):
        await bus.trigger(Event(EXTENSION_PLAN_EVENT, PlanRequest(
            bus=bus, operation="install", manifest=manifest, package_root=source,
            operation_token=forged)))
    assert (await registry.install(source))[0].status == "SUCCESS"
    assert (await registry.install(source))[0].error.code == "EXTENSION_PACKAGE_EXISTS"


@pytest.mark.asyncio
async def test_apply_publishes_only_an_admitted_plan(tmp_path: Path) -> None:
    source = _source(tmp_path, "source", "demo")
    manifest = load_manifest(source)
    registry, bus = await build_registry(tmp_path / "store")
    service = registry.context.install_report
    token = await service.begin_operation(manifest, "install")
    await service.wait_for_dependencies(token)
    planned = await asyncio.create_task(bus.trigger(Event(EXTENSION_PLAN_EVENT, PlanRequest(
        bus=bus, operation="install", manifest=manifest, package_root=source,
        operation_token=token))))
    assert planned.plan is not None
    applied = await asyncio.create_task(bus.trigger(Event(EXTENSION_APPLY_EVENT, ApplyRequest(
        bus=bus, plan=planned.plan, manifest=manifest, operation_token=token))))
    assert applied.status == "SUCCESS"
    await service.finish_operation(token, report=applied, active=registry.runtime.get_active("demo"))
    assert (await service.read_packages())["demo"].successful
    assert await call_business_event(bus) == 1


@pytest.mark.asyncio
async def test_rollback_cleans_only_the_admitted_copy(tmp_path: Path) -> None:
    source = _source(tmp_path, "source", "demo")
    registry, bus = await build_registry(tmp_path / "store")
    manifest = load_manifest(source)
    service = registry.context.install_report
    with pytest.raises(ExtensionFailure, match="admitted"):
        registry.package_manager.copy_into_store(source, manifest)
    token = await service.begin_operation(manifest, "install")
    with service.operation_scope(token):
        copied = registry.package_manager.copy_into_store(source, manifest)
        planned = await bus.trigger(Event(EXTENSION_PLAN_EVENT, PlanRequest(
            bus=bus, operation="install", manifest=manifest, package_root=copied,
            operation_token=token)))
        prepared = registry.runtime.prepare(planned.plan)
        rolled_back = await bus.trigger(Event(EXTENSION_ROLLBACK_EVENT, RollbackRequest(
            bus=bus, plan=planned.plan, candidate=prepared.candidate, store_copy_created=True)))
    assert rolled_back.removed_store_copy
    assert not copied.exists()
    assert source.is_dir()
    await service.finish_operation(token, report=ActivationReport(
        "", "demo", "install", "FAILED", None, None), active=None)


@pytest.mark.asyncio
async def test_query_returns_the_facts_of_the_active_packages(tmp_path: Path) -> None:
    source = _source(tmp_path, "source", "demo")
    other = _source(tmp_path, "other", "other")
    registry, bus = await build_registry(tmp_path / "store")
    assert all(item.status == "SUCCESS" for item in await registry.install([source, other]))
    registry.runtime.begin_calls(["demo"])

    everything = await bus.trigger(Event(PACKAGE_QUERY_EVENT, QueryRequest(bus=bus)))
    assert isinstance(everything, QueryResult)
    assert [item.package_id for item in everything.active] == ["demo", "other"]
    assert len(everything.installed) == 2
    assert everything.inflight == {"demo": 1, "other": 0}

    one = await bus.trigger(Event(PACKAGE_QUERY_EVENT, QueryRequest(bus=bus, package_id="demo")))
    assert isinstance(one, QueryResult)
    assert [item.package_id for item in one.active] == ["demo"]
    assert [item.parent.name for item in one.installed] == ["demo"]
    assert len(one.published["demo"]) == 1


@pytest.mark.asyncio
async def test_install_skips_a_dependent_and_keeps_an_independent_package(tmp_path: Path) -> None:
    base = _source(tmp_path, "base", "base", body=_BROKEN_BODY)
    child = _source(tmp_path, "child", "child", requires=(PackageDependency("base", ">=1.0.0"),))
    alone = _source(tmp_path, "alone", "alone")
    registry, bus = await build_registry(tmp_path / "store")

    result = await registry.install([child, alone, base])

    assert any(item.status == "FAILED" for item in result)
    base_report = next(item for item in result if item.package_id == "base")
    child_report = next(item for item in result if item.package_id == "child")
    alone_report = next(item for item in result if item.package_id == "alone")
    assert base_report is not None and base_report.status == "FAILED"
    assert base_report.error is not None
    assert base_report.error.status == StatusCode.EXTENSION_LOAD_FAILED
    assert child_report is not None and child_report.status == "FAILED"
    assert child_report.error is not None
    assert child_report.error.status == StatusCode.EXTENSION_DEPENDENCY_NOT_MET
    assert alone_report is not None and alone_report.status == "SUCCESS"
    assert [item.package_id for item in registry.runtime.active_packages()] == ["alone"]


@pytest.mark.asyncio
async def test_install_refuses_a_package_the_host_version_does_not_satisfy(tmp_path: Path) -> None:
    source = _source(tmp_path, "source", "demo")
    manifest_path = source / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["requires"] = {"host": ">=9.0.0"}
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    registry, _ = await build_registry(tmp_path / "store", host_version="1.0.0")

    result = await registry.install([source])

    report = result[0]
    assert report.package_id == "demo"
    assert report.status == "FAILED"
    assert report.error is not None
    assert report.error.status == StatusCode.EXTENSION_HOST_VERSION_TOO_LOW


@pytest.mark.asyncio
async def test_install_reports_a_source_that_cannot_be_parsed(tmp_path: Path) -> None:
    good = _source(tmp_path, "source", "demo")
    registry, _ = await build_registry(tmp_path / "store")

    result = await registry.install([tmp_path / "absent", good])

    assert len(result) == 2
    broken = next(item for item in result if item.package_id == str(tmp_path / "absent"))
    assert broken.status == "FAILED"
    assert broken.error is not None
    assert broken.error.status == StatusCode.EXTENSION_SOURCE_INVALID
    demo = next(item for item in result if item.package_id == "demo")
    assert demo is not None and demo.status == "SUCCESS"


@pytest.mark.asyncio
async def test_disabled_package_is_stored_then_can_be_enabled_and_disabled(tmp_path: Path) -> None:
    source = _source(tmp_path, "source", "demo")
    document = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    document["enable"] = False
    (source / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
    store = tmp_path / "store"
    registry, bus = await build_registry(store)

    installed = (await registry.install(source))[0]

    assert installed.status == "SUCCESS"
    assert registry.runtime.get_active("demo") is None
    assert await call_business_event(bus) == ("core", {"tool": "Bash"})
    copied = store / "demo" / "1.0.0" / "manifest.json"
    assert json.loads(copied.read_text(encoding="utf-8"))["enable"] is False

    reloaded = await registry.reload("demo")

    assert reloaded.status == "SUCCESS"
    assert reloaded.operation == "reload"
    assert registry.runtime.get_active("demo") is None
    assert await call_business_event(bus) == ("core", {"tool": "Bash"})
    assert json.loads(copied.read_text(encoding="utf-8"))["enable"] is False

    enabled = await registry.set_enabled("demo", True)

    assert enabled.status == "SUCCESS"
    assert enabled.operation == "enable"
    assert await call_business_event(bus) == 1
    assert json.loads(copied.read_text(encoding="utf-8"))["enable"] is True

    disabled = await registry.set_enabled("demo", False)

    assert disabled.status == "SUCCESS"
    assert disabled.operation == "disable"
    assert registry.runtime.get_active("demo") is None
    assert await call_business_event(bus) == ("core", {"tool": "Bash"})
    assert json.loads(copied.read_text(encoding="utf-8"))["enable"] is False


@pytest.mark.asyncio
async def test_installing_a_dependent_fails_immediately_when_its_dependency_is_disabled(tmp_path: Path) -> None:
    base = _source(tmp_path, "base", "base")
    base_manifest = base / "manifest.json"
    base_document = json.loads(base_manifest.read_text(encoding="utf-8"))
    base_document["enable"] = False
    base_manifest.write_text(json.dumps(base_document), encoding="utf-8")
    child = _source(
        tmp_path,
        "child",
        "child",
        requires=(PackageDependency("base", ">=1.0.0"),),
    )
    registry, _ = await build_registry(tmp_path / "store")
    registry.context.install_report.dependency_timeout = 0.01

    reports = await registry.install([base, child])
    child_report = next(report for report in reports if report.package_id == "child")

    assert child_report.status == "FAILED"
    assert child_report.error is not None
    assert child_report.error.status == StatusCode.EXTENSION_DEPENDENCY_NOT_MET
    assert registry.runtime.get_active("child") is None


@pytest.mark.asyncio
async def test_startup_normalizes_a_legacy_installed_manifest(tmp_path: Path) -> None:
    source = _source(tmp_path, "source", "demo")
    store = tmp_path / "store"
    manager = PackageManager(store)
    copied = manager.copy_into_store(source, load_manifest(source))
    document = json.loads((copied / "manifest.json").read_text(encoding="utf-8"))
    document.pop("enable")
    (copied / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
    registry, bus = await build_registry(store)

    reports = await registry.load_installed()

    assert reports[0].status == "SUCCESS"
    assert json.loads((copied / "manifest.json").read_text(encoding="utf-8"))["enable"] is True
    assert await call_business_event(bus) == 1


@pytest.mark.asyncio
async def test_startup_loads_a_legacy_manifest_when_enable_backfill_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path, "source", "demo")
    store = tmp_path / "store"
    manager = PackageManager(store)
    copied = manager.copy_into_store(source, load_manifest(source))
    manifest_path = copied / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document.pop("enable")
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    def cannot_backfill(_root: Path, _enabled: bool) -> None:
        raise ExtensionFailure(StatusCode.EXTENSION_INSTALL_COPY_FAILED, reason="read-only manifest")

    monkeypatch.setattr(package_manager_module, "_write_manifest_enable", cannot_backfill)
    registry, bus = await build_registry(store)

    reports = await registry.load_installed()

    assert reports[0].status == "SUCCESS"
    assert "enable" not in json.loads(manifest_path.read_text(encoding="utf-8"))
    assert await call_business_event(bus) == 1


@pytest.mark.asyncio
async def test_startup_does_not_import_a_disabled_installed_package(tmp_path: Path) -> None:
    source = _source(tmp_path, "source", "demo")
    document = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    document["enable"] = False
    (source / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
    store = tmp_path / "store"
    PackageManager(store).copy_into_store(source, load_manifest(source))
    registry, bus = await build_registry(store)

    reports = await registry.load_installed()

    assert reports == ()
    assert registry.runtime.get_active("demo") is None
    assert await call_business_event(bus) == ("core", {"tool": "Bash"})


@pytest.mark.asyncio
async def test_uninstall_stops_on_a_busy_or_required_package(tmp_path: Path) -> None:
    base = _source(tmp_path, "base", "base")
    child = _source(tmp_path, "child", "child", requires=(PackageDependency("base", ">=1.0.0"),))
    registry, bus = await build_registry(tmp_path / "store")
    assert all(item.status == "SUCCESS" for item in await registry.install([base, child]))

    required = await registry.uninstall("base")
    assert required.status == "FAILED"
    assert required.error is not None
    assert required.error.status == StatusCode.EXTENSION_PACKAGE_IN_USE

    assert (await registry.uninstall("child")).status == "SUCCESS"
    registry.runtime.begin_calls(["base"])
    busy = await registry.uninstall("base")

    assert busy.status == "FAILED"
    assert busy.error is not None
    assert busy.error.status == StatusCode.EXTENSION_PACKAGE_BUSY
    assert registry.runtime.get_active("base") is not None
    assert (tmp_path / "store" / "base" / "1.0.0").is_dir()
    assert await call_business_event(bus) == 1

    registry.runtime.end_calls(["base"])
    assert (await registry.uninstall("base")).status == "SUCCESS"
    assert not (tmp_path / "store" / "base").exists()


@pytest.mark.asyncio
async def test_reload_hot_switches_without_uninstalling_first(tmp_path: Path) -> None:
    source = _source(tmp_path, "source", "demo")
    store = tmp_path / "store"
    registry, bus = await build_registry(store)
    assert all(item.status == "SUCCESS" for item in await registry.install([source]))
    published = registry.runtime.published_handlers("demo")

    entry = store / "demo" / "1.0.0" / "extensions" / "ext" / "extension.py"
    entry.write_text(entry.read_text(encoding="utf-8").replace("VALUE = 1", "VALUE = 9"), encoding="utf-8")
    reloaded = await registry.reload("demo")

    assert reloaded.status == "SUCCESS"
    assert reloaded.previous_generation == 1
    assert reloaded.current_generation == 2
    assert registry.runtime.published_handlers("demo") != published
    assert await call_business_event(bus) == 9
    assert (store / "demo" / "1.0.0").is_dir()


@pytest.mark.asyncio
async def test_reload_is_refused_when_a_dependent_rejects_the_new_version(tmp_path: Path) -> None:
    base = _source(tmp_path, "base", "base")
    child = _source(
        tmp_path,
        "child",
        "child",
        requires=(PackageDependency("base", ">=1.0.0 <2.0.0"),),
    )
    store = tmp_path / "store"
    registry, bus = await build_registry(store)
    assert all(item.status == "SUCCESS" for item in await registry.install([base, child]))

    manifest_path = store / "base" / "1.0.0" / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["package"]["version"] = "2.0.0"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    refused = await registry.reload("base")

    assert refused.status == "FAILED"
    assert refused.error is not None
    assert refused.error.status == StatusCode.EXTENSION_DEPENDENT_INCOMPATIBLE
    record = registry.runtime.get_active("base")
    assert record is not None
    assert record.generation == 1
    assert await call_business_event(bus) == 1


@pytest.mark.asyncio
async def test_reload_of_an_unknown_package_is_reported(tmp_path: Path) -> None:
    registry, _ = await build_registry(tmp_path / "store")

    result = await registry.reload("absent")

    assert result.status == "FAILED"
    assert result.error is not None
    assert result.error.status == StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED
