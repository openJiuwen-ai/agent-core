"""Report-backed domain loading, process ownership and lifecycle regressions."""

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from tests.unit_tests.core.extension.conftest import (
    BUSINESS_EVENT,
    echo_core_handler,
    match_event_input,
    write_package,
)

from openjiuwen.core.events import ExtensionEventBus
from openjiuwen.core.extension import ExtensionFailure, Registry
from openjiuwen.core.extension.install_report import PackageInstallReport, PackageInstallState
from openjiuwen.core.extension.manifest import load_manifest
from openjiuwen.core.extension.schema.manifest import PackageDependency
from openjiuwen.core.extension.schema.records import ActivationReport
from openjiuwen.core.extension.selection import build_startup_plan


def _write(root, package_id, domain, version="1.0.0", requires=()):
    write_package(root, package_id, version, requires=requires)
    path = root / "manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if domain is not None:
        raw["package"]["domain"] = domain
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


async def _registry(root, domain, *, timeout=1.0):
    bus = ExtensionEventBus()
    bus.register_event_type(BUSINESS_EVENT, match_event_input, echo_core_handler)
    registry = Registry()
    await registry.init(bus)
    await registry.open_install_dir(root, report_path=root.parent / "report.yaml", domain=domain,
                                    poll_interval=0.01, dependency_timeout=timeout, lock_timeout=5.0)
    return registry, bus


async def _close(registry, bus):
    for record in registry.runtime.active_packages():
        registry.runtime.unpublish(record.package_id, bus)
    await registry.close()


@pytest.mark.parametrize("domain", [None, "", " ", 1, ["worker"]])
def test_domain_required_only_when_enabled(tmp_path, domain):
    root = _write(tmp_path / "source", "demo", domain)
    assert load_manifest(root).domain is None
    with pytest.raises(ExtensionFailure, match="package.domain"):
        load_manifest(root, require_domain=True)


@pytest.mark.asyncio
async def test_sdk_arbitrary_domain_and_no_domain_mode(tmp_path):
    root = tmp_path / "installed"
    _write(root / "a" / "1.0.0", "a", "worker-a")
    _write(root / "b" / "1.0.0", "b", "worker-b")
    registry, bus = await _registry(root, "worker-a")
    try:
        reports = await registry.load_installed()
        assert [item.package_id for item in reports] == ["a"]
        assert [item.package_id for item in registry.runtime.active_packages()] == ["a"]
    finally:
        await _close(registry, bus)
    all_registry, all_bus = await _registry(root, None)
    try:
        assert all(item.status == "SUCCESS" for item in await all_registry.load_installed())
        assert {item.package_id for item in all_registry.runtime.active_packages()} == {"a", "b"}
    finally:
        await _close(all_registry, all_bus)


@pytest.mark.asyncio
async def test_cross_domain_wait_and_reverse_dependency_protection(tmp_path):
    root = tmp_path / "installed"
    _write(root / "a" / "1.0.0", "a", "worker-a", requires=(PackageDependency("b", ">=1.0.0"),))
    _write(root / "b" / "1.0.0", "b", "worker-b")
    a, a_bus = await _registry(root, "worker-a")
    b, b_bus = await _registry(root, "worker-b")
    try:
        task = asyncio.create_task(a.load_installed())
        for _ in range(100):
            rows = await a.context.install_report.read_packages()
            if "a" in rows and rows["a"].status == "waiting":
                break
            await asyncio.sleep(0.01)
        assert not task.done()
        assert (await b.load_installed())[0].status == "SUCCESS"
        assert (await task)[0].status == "SUCCESS"
        assert [item.package_id for item in a.runtime.active_packages()] == ["a"]
        assert [item.package_id for item in b.runtime.active_packages()] == ["b"]
        assert (await b.uninstall("b")).error.code == "EXTENSION_PACKAGE_IN_USE"
        assert (await a.uninstall("a")).status == "SUCCESS"
        assert (await b.uninstall("b")).status == "SUCCESS"
        assert await a.context.install_report.read_packages() == {}
    finally:
        await _close(a, a_bus)
        await _close(b, b_bus)


@pytest.mark.asyncio
async def test_wait_timeout_and_cycle_are_explicit(tmp_path):
    root = tmp_path / "installed"
    _write(root / "a" / "1.0.0", "a", "a", requires=(PackageDependency("b", ""),))
    _write(root / "b" / "1.0.0", "b", "b")
    a, bus = await _registry(root, "a", timeout=0.05)
    try:
        assert (await a.load_installed())[0].error.code == "EXTENSION_DEPENDENCY_TIMEOUT"
        assert not a.runtime.active_packages()
    finally:
        await _close(a, bus)
    _write(root / "b" / "1.0.0", "b", "b", requires=(PackageDependency("a", ""),))
    a, bus = await _registry(root, "a")
    try:
        reports = await a.load_installed()
        assert {item.error.code for item in reports} == {"EXTENSION_DEPENDENCY_CYCLE"}
    finally:
        await _close(a, bus)


@pytest.mark.asyncio
async def test_restart_preserves_other_owner_and_invalidates_old_success(tmp_path):
    root = tmp_path / "installed"
    a, a_bus = await _registry(root, "a")
    b, b_bus = await _registry(root, "b")
    first_run = a.context.install_report.run_id
    source = _write(tmp_path / "source", "demo", "b")
    assert (await b.install(source))[0].status == "SUCCESS"
    await _close(a, a_bus)
    a, a_bus = await _registry(root, "a")
    try:
        assert a.context.install_report.run_id == first_run
        assert (await a.context.install_report.read_packages())["demo"].successful
        await _close(b, b_bus)
        assert not (await a.context.install_report.read_packages())["demo"].successful
        b, b_bus = await _registry(root, "b")
        assert (await b.context.install_report.read_packages())["demo"].active_version is None
        assert (await b.load_installed())[0].status == "SUCCESS"
    finally:
        await _close(a, a_bus)
        await _close(b, b_bus)


@pytest.mark.asyncio
async def test_conflicting_disk_domains_and_online_id_are_rejected(tmp_path):
    root = tmp_path / "installed"
    _write(root / "demo" / "1.0.0", "demo", "a")
    _write(root / "demo" / "2.0.0", "demo", "b", version="2.0.0")
    a, bus = await _registry(root, "a")
    try:
        reports = await a.load_installed()
        assert reports[0].error.code == "EXTENSION_DUPLICATE_PACKAGE_ID"
        source = _write(tmp_path / "source", "demo", "a", version="3.0.0")
        assert (await a.install(source))[0].error.code == "EXTENSION_DUPLICATE_PACKAGE_ID"
        assert not (root / "demo" / "3.0.0").exists()
    finally:
        await _close(a, bus)


@pytest.mark.asyncio
async def test_duplicate_owner_and_mixed_scopes_are_rejected(tmp_path):
    a, bus = await _registry(tmp_path / "installed", "a")
    try:
        with pytest.raises(ExtensionFailure):
            await _registry(tmp_path / "installed", "a")
        with pytest.raises(ExtensionFailure):
            await _registry(tmp_path / "installed", None)
    finally:
        await _close(a, bus)


@pytest.mark.asyncio
async def test_corrupt_report_is_never_replaced(tmp_path):
    path = tmp_path / "report.yaml"
    path.write_text("packages: [broken", encoding="utf-8")
    report = PackageInstallReport(path)
    with pytest.raises(ExtensionFailure, match="report"):
        await report.open(tmp_path / "installed", domain="a")
    assert path.read_text(encoding="utf-8") == "packages: [broken"


@pytest.mark.asyncio
async def test_report_defaults_and_invalid_intervals(tmp_path):
    report = PackageInstallReport(tmp_path / "report.yaml")
    assert report.poll_interval == 0.5
    assert report.dependency_timeout == 300
    for value in (0, -1, True, float("nan"), float("inf")):
        with pytest.raises(ExtensionFailure):
            PackageInstallReport(tmp_path / "report.yaml", poll_interval=value)


_PROCESS_SCRIPT = r"""
import asyncio, json, sys
from pathlib import Path
from openjiuwen.core.events import ExtensionEventBus
from openjiuwen.core.extension import Registry

BUSINESS_EVENT = "business.event"

def match_event_input(event_input, match_pattern):
    if not match_pattern:
        return True
    return isinstance(event_input, dict) and all(
        event_input.get(key) == value for key, value in match_pattern.items()
    )

async def echo_core_handler(event, ops, next_handler):
    del ops, next_handler
    return ("core", event.event_input)

async def main():
    root, domain, ready, stop = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
    bus = ExtensionEventBus()
    bus.register_event_type(BUSINESS_EVENT, match_event_input, echo_core_handler)
    registry = Registry()
    await registry.init(bus)
    await registry.open_install_dir(root, report_path=root.parent / "report.yaml", domain=domain,
                                    poll_interval=0.02, dependency_timeout=20)
    try:
        reports = (await registry.install(Path(sys.argv[5])) if len(sys.argv) > 5
                   else await registry.load_installed())
        ready.write_text(json.dumps({"reports": [[item.package_id, item.status] for item in reports],
                                     "active": [item.package_id for item in registry.runtime.active_packages()]}))
        for i in range(600):
            if stop.exists():
                break
            await asyncio.sleep(0.05)
    finally:
        for record in registry.runtime.active_packages():
            registry.runtime.unpublish(record.package_id, bus)
        await registry.close()

asyncio.run(main())
"""


@pytest.mark.parametrize("mode", ["dependent_first", "dependency_first", "simultaneous"])
def test_real_process_delay_and_crash_release(tmp_path, mode):
    """Real interpreters share YAML; no Python queue is used for process ownership."""
    root = tmp_path / "installed"
    _write(root / "a" / "1.0.0", "a", "a", requires=(PackageDependency("b", ""),))
    _write(root / "b" / "1.0.0", "b", "b")
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[4]))
    processes = []

    def start(domain):
        with (tmp_path / f"{domain}.log").open("w") as log:
            process = subprocess.Popen([sys.executable, "-c", _PROCESS_SCRIPT, str(root), domain,
                                        str(tmp_path / f"{domain}.ready"), str(tmp_path / f"{domain}.stop")],
                                       env=environment, stdout=log, stderr=log)
        processes.append(process)
        return process

    def wait_for(check, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if check():
                return
            if any(process.poll() is not None for process in processes):
                pytest.fail("child exited: " + " ".join(path.read_text() for path in tmp_path.glob("*.log")))
            time.sleep(0.03)
        pytest.fail("child timeout: " + " ".join(path.read_text() for path in tmp_path.glob("*.log")))

    try:
        def waiting():
            path = tmp_path / "report.yaml"
            if not path.exists():
                return False
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            return raw["packages"].get("a", {}).get("status") == "waiting"
        if mode == "dependent_first":
            start("a")
            wait_for(waiting)
            b = start("b")
        elif mode == "dependency_first":
            b = start("b")
            wait_for(lambda: (tmp_path / "b.ready").exists())
            start("a")
        else:
            start("a")
            b = start("b")
        wait_for(lambda: (tmp_path / "a.ready").exists() and (tmp_path / "b.ready").exists())
        assert json.loads((tmp_path / "a.ready").read_text()) == {"reports": [["a", "SUCCESS"]], "active": ["a"]}
        assert json.loads((tmp_path / "b.ready").read_text()) == {"reports": [["b", "SUCCESS"]], "active": ["b"]}
        initial_run = yaml.safe_load((tmp_path / "report.yaml").read_text())["run_id"]
        b.terminate()
        b.wait(timeout=5)

        async def observe():
            observer = PackageInstallReport(tmp_path / "report.yaml")
            await observer.open(root, domain="observer")
            try:
                rows = await observer.read_packages()
                assert rows["a"].successful
                assert not rows["b"].successful
                assert observer.run_id == initial_run
            finally:
                await observer.close()
        asyncio.run(observe())
    finally:
        for domain in ("a", "b"):
            (tmp_path / f"{domain}.stop").touch()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.asyncio
async def test_cold_restart_creates_new_run_and_empty_packages(tmp_path):
    root = tmp_path / "installed"
    a, bus = await _registry(root, "a")
    first = a.context.install_report.run_id
    assert (await a.install(_write(tmp_path / "source", "demo", "a")))[0].status == "SUCCESS"
    await _close(a, bus)
    b, bus = await _registry(root, "a")
    try:
        assert b.context.install_report.run_id != first
        assert await b.context.install_report.read_packages() == {}
        assert (await b.load_installed())[0].status == "SUCCESS"
    finally:
        await _close(b, bus)


@pytest.mark.asyncio
async def test_startup_reservation_blocks_online_writes_and_keeps_the_plan(tmp_path):
    root = tmp_path / "installed"
    _write(root / "a" / "1.0.0", "a", "a", requires=(PackageDependency("b", ""),))
    _write(root / "b" / "1.0.0", "b", "b")
    a, a_bus = await _registry(root, "a", timeout=5)
    b, b_bus = await _registry(root, "b")
    task = asyncio.create_task(a.load_installed())
    try:
        for _ in range(100):
            rows = await a.context.install_report.read_packages()
            if "a" in rows and rows["a"].status == "waiting":
                break
            await asyncio.sleep(0.01)
        plan = yaml.safe_load((tmp_path / "report.yaml").read_text())["startup_order"]
        online = await a.install(_write(tmp_path / "new", "new", "a"))
        assert online[0].error.code == "EXTENSION_PACKAGE_BUSY"
        assert not (root / "new").exists()
        assert (await b.load_installed())[0].status == "SUCCESS"
        assert (await task)[0].status == "SUCCESS"
        assert yaml.safe_load((tmp_path / "report.yaml").read_text())["startup_order"] == plan
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await _close(a, a_bus)
        await _close(b, b_bus)


@pytest.mark.asyncio
async def test_cancelled_wait_is_unknown_until_explicit_cleanup(tmp_path):
    root = tmp_path / "installed"
    _write(root / "a" / "1.0.0", "a", "a", requires=(PackageDependency("b", ""),))
    _write(root / "b" / "1.0.0", "b", "b")
    a, bus = await _registry(root, "a", timeout=5)
    task = asyncio.create_task(a.load_installed())
    try:
        for _ in range(100):
            rows = await a.context.install_report.read_packages()
            if "a" in rows and rows["a"].status == "waiting":
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        row = (await a.context.install_report.read_packages())["a"]
        assert row.status == "unknown" and row.pending is not None
        assert not a.runtime.active_packages()
        assert (await a.uninstall("a")).status == "SUCCESS"
        assert "a" not in await a.context.install_report.read_packages()
    finally:
        await _close(a, bus)


@pytest.mark.asyncio
async def test_report_commit_failure_keeps_local_result_and_query_reconciles(tmp_path, monkeypatch):
    from openjiuwen.core.events import Event
    from openjiuwen.core.extension.management import PACKAGE_QUERY_EVENT, QueryRequest
    registry, bus = await _registry(tmp_path / "installed", "a")
    service = registry.context.install_report
    write = service._write
    def fail_after_publication(data):
        if data["packages"].get("demo", {}).get("status") == "success":
            raise OSError("simulated full disk")
        write(data)
    try:
        monkeypatch.setattr(service, "_write", fail_after_publication)
        result = await registry.install(_write(tmp_path / "source", "demo", "a"))
        assert result[0].status == "SUCCESS"
        assert registry.runtime.get_active("demo") is not None
        monkeypatch.setattr(service, "_write", write)
        state = await bus.trigger(Event(PACKAGE_QUERY_EVENT, QueryRequest(bus)))
        assert state.packages[0].successful
        assert state.packages[0].active_version == "1.0.0"
    finally:
        monkeypatch.setattr(service, "_write", write)
        await _close(registry, bus)


@pytest.mark.asyncio
async def test_dead_owner_takeover_clears_stale_active_state(tmp_path):
    registry, bus = await _registry(tmp_path / "installed", "a")
    service = registry.context.install_report
    manifest = load_manifest(_write(tmp_path / "source", "demo", "a"))

    def seed_dead_owner(data):
        data["packages"]["demo"] = {
            "domain": "a",
            "process_id": None,
            "active_version": "9.9.9",
            "requires": {"ghost": ">=1.0.0"},
            "status": "success",
            "reason": None,
            "pending": None,
        }

    try:
        await service._transaction(seed_dead_owner)
        token = await service.begin_operation(manifest, "install")
        state = (await service.read_packages())["demo"]
        assert state.process_id == service.process_id
        assert state.active_version is None
        assert state.requires == {}
        assert state.pending["operation_id"] == token.operation_id
        await service.finish_operation(
            token,
            report=ActivationReport("", "demo", "install", "FAILED", None, None),
            active=None,
        )
    finally:
        await _close(registry, bus)


def test_startup_plan_preserves_exact_disk_manifest_and_rejects_conflict(tmp_path):
    manifest = load_manifest(_write(tmp_path / "demo", "demo", "a"))
    state = PackageInstallState(
        package_id="demo",
        domain="a",
        process_id="owner",
        active_version="1.0.0",
        requires={},
        status="success",
        reason=None,
        pending=None,
    )

    plan = build_startup_plan((manifest,), (state,))
    assert plan.ordered == (manifest,)
    assert plan.ordered[0] is manifest

    conflict = replace(state, domain="b")
    rejected = build_startup_plan((manifest,), (conflict,))
    assert rejected.ordered == ()
    assert rejected.rejected[0].error.code == "EXTENSION_REPORT_FAILED"


@pytest.mark.asyncio
async def test_managed_versions_skips_invalid_directory_and_keeps_other_domains(tmp_path):
    root = tmp_path / "installed"
    _write(root / "demo" / "1.0.0", "demo", "a")
    _write(root / "demo" / "2.0.0", "demo", "b", version="2.0.0")
    _write(root / "demo" / "3.0.0", "demo", None, version="3.0.0")
    registry, bus = await _registry(root, "a")
    try:
        assert registry.package_manager.managed_versions("demo") == ("1.0.0", "2.0.0")
    finally:
        await _close(registry, bus)


@pytest.mark.asyncio
async def test_report_lock_timeout_and_mismatched_root_are_explicit(tmp_path):
    import portalocker
    registry, bus = await _registry(tmp_path / "installed", "a")
    service = registry.context.install_report
    try:
        service.lock_timeout = 0.05
        with portalocker.Lock(str(service.path) + ".lock", mode="a+b", timeout=0):
            with pytest.raises(ExtensionFailure, match="lock timeout"):
                await service.read_packages()
        other = PackageInstallReport(service.path)
        with pytest.raises(ExtensionFailure, match="another installation directory"):
            await other.open(tmp_path / "other", domain="b")
    finally:
        service.lock_timeout = 5
        await _close(registry, bus)


@pytest.mark.asyncio
async def test_highest_version_and_domain_projection_are_stable(tmp_path):
    from openjiuwen.core.extension.ordering import order_key
    root = tmp_path / "installed"
    for package_id, domain in (("z", "a"), ("m", "b"), ("x", "a")):
        _write(root / package_id / "1.0.0", package_id, domain)
    _write(root / "x" / "2.0.0", "x", "a", version="2.0.0")
    registry, bus = await _registry(root, "a")
    try:
        reports = await registry.load_installed()
        expected = sorted(["z", "m", "x"], key=order_key)
        assert yaml.safe_load((tmp_path / "report.yaml").read_text())["startup_order"] == expected
        assert [row.package_id for row in reports] == [key for key in expected if key != "m"]
        assert registry.runtime.get_active("x").version == "2.0.0"
        assert (await registry.uninstall("x")).status == "SUCCESS"
        assert await registry.load_installed() == ()
    finally:
        await _close(registry, bus)


@pytest.mark.asyncio
async def test_failed_reload_preserves_shared_effective_version_and_domain(tmp_path):
    root = tmp_path / "installed"
    a, a_bus = await _registry(root, "a")
    b, b_bus = await _registry(root, "b")
    try:
        assert (await a.install(_write(tmp_path / "a-src", "a", "a")))[0].status == "SUCCESS"
        source = _write(tmp_path / "b-src", "b", "b", requires=(PackageDependency("a", "<2.0.0"),))
        assert (await b.install(source))[0].status == "SUCCESS"
        path = root / "a" / "1.0.0" / "manifest.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["package"]["version"] = "2.0.0"
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        assert (await a.reload("a")).error.code == "EXTENSION_DEPENDENT_INCOMPATIBLE"
        assert (await b.context.install_report.read_packages())["a"].active_version == "1.0.0"
        raw["package"]["version"] = "1.0.0"
        raw["package"]["domain"] = "b"
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        assert (await a.reload("a")).error.code == "EXTENSION_DOMAIN_MISMATCH"
        raw["package"]["domain"] = "a"
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        (root / "a" / "1.0.0" / raw["extensions"][0]["module"]).write_text("raise RuntimeError('broken update')")
        assert (await a.reload("a")).status == "FAILED"
        state = (await b.context.install_report.read_packages())["a"]
        assert state.status == "failed" and state.successful and state.active_version == "1.0.0"
        assert (await b.uninstall("b")).status == "SUCCESS"
        assert (await a.uninstall("a")).status == "SUCCESS"
    finally:
        await _close(b, b_bus)
        await _close(a, a_bus)


def test_real_process_simultaneous_global_id_claim(tmp_path):
    root = tmp_path / "installed"
    sources = {domain: _write(tmp_path / (domain + "-src"), "shared", domain) for domain in ("a", "b")}
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[4]))
    children = []
    try:
        for domain in ("a", "b"):
            with (tmp_path / f"{domain}.log").open("w") as log:
                children.append(subprocess.Popen(
                    [sys.executable, "-c", _PROCESS_SCRIPT, str(root), domain,
                     str(tmp_path / f"{domain}.ready"), str(tmp_path / f"{domain}.stop"), str(sources[domain])],
                    env=environment, stdout=log, stderr=log,
                ))
        deadline = time.monotonic() + 30
        while not all((tmp_path / f"{domain}.ready").exists() for domain in ("a", "b")):
            if time.monotonic() > deadline or any(child.poll() is not None for child in children):
                pytest.fail("concurrent claim failed: " + " ".join(p.read_text() for p in tmp_path.glob("*.log")))
            time.sleep(0.03)
        results = [json.loads((tmp_path / f"{domain}.ready").read_text()) for domain in ("a", "b")]
        assert sorted(item["reports"][0][1] for item in results) == ["FAILED", "SUCCESS"]
        assert sum(item["active"] == ["shared"] for item in results) == 1
        row = yaml.safe_load((tmp_path / "report.yaml").read_text())["packages"]["shared"]
        assert row["active_version"] == "1.0.0" and row["status"] == "success"
        manifest = json.loads((root / "shared" / "1.0.0" / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["package"]["domain"] == row["domain"]
    finally:
        for domain in ("a", "b"):
            (tmp_path / f"{domain}.stop").touch()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


@pytest.mark.asyncio
async def test_cancellation_during_admission_retains_cleanup_identity(tmp_path, monkeypatch):
    import threading
    registry, bus = await _registry(tmp_path / "installed", "a")
    service = registry.context.install_report
    entered, release = threading.Event(), threading.Event()
    write = service._write
    def delayed_admission(data):
        if data["packages"].get("demo", {}).get("status") == "waiting":
            entered.set()
            assert release.wait(5)
        write(data)
    monkeypatch.setattr(service, "_write", delayed_admission)
    task = asyncio.create_task(registry.install(_write(tmp_path / "source", "demo", "a")))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        state = (await service.read_packages())["demo"]
        assert state.status == "unknown" and state.pending is not None
        assert (await registry.uninstall("demo")).status == "SUCCESS"
        assert await service.read_packages() == {}
    finally:
        release.set()
        monkeypatch.setattr(service, "_write", write)
        await _close(registry, bus)


@pytest.mark.asyncio
async def test_explicit_foreign_query_is_rejected_before_startup_registration(tmp_path):
    from openjiuwen.core.events import Event
    from openjiuwen.core.extension.management import PACKAGE_QUERY_EVENT, QueryRequest
    root = tmp_path / "installed"
    _write(root / "foreign" / "1.0.0", "foreign", "b")
    registry, bus = await _registry(root, "a")
    try:
        assert await registry.context.install_report.read_packages() == {}
        with pytest.raises(ExtensionFailure, match="another domain"):
            await bus.trigger(Event(PACKAGE_QUERY_EVENT, QueryRequest(bus, "foreign")))
        result = await bus.trigger(Event(PACKAGE_QUERY_EVENT, QueryRequest(bus)))
        assert result.active == () and result.installed == ()
    finally:
        await _close(registry, bus)


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown_write_fails", [False, True])
async def test_admission_io_error_after_replace_can_be_cleaned(tmp_path, monkeypatch, unknown_write_fails):
    registry, bus = await _registry(tmp_path / "installed", "a")
    service = registry.context.install_report
    write = service._write
    def fail_flush(data):
        row = data["packages"].get("demo", {})
        if unknown_write_fails and row.get("status") == "unknown":
            raise OSError("report still unavailable")
        write(data)
        if row.get("status") == "waiting":
            raise OSError("flush failed after replace")
    try:
        monkeypatch.setattr(service, "_write", fail_flush)
        assert (await registry.install(_write(tmp_path / "source", "demo", "a")))[0].status == "FAILED"
        assert not registry.runtime.active_packages()
        monkeypatch.setattr(service, "_write", write)
        row = (await service.read_packages())["demo"]
        assert row.pending is not None
        assert row.status == ("waiting" if unknown_write_fails else "unknown")
        assert (await registry.uninstall("demo")).status == "SUCCESS"
        assert await service.read_packages() == {}
    finally:
        monkeypatch.setattr(service, "_write", write)
        await _close(registry, bus)


@pytest.mark.asyncio
async def test_startup_waits_for_admitted_foreign_install_before_its_copy_is_visible(tmp_path, monkeypatch):
    import threading
    root = tmp_path / "installed"
    _write(root / "a" / "1.0.0", "a", "a", requires=(PackageDependency("b", ""),))
    a, a_bus = await _registry(root, "a", timeout=5)
    b, b_bus = await _registry(root, "b", timeout=5)
    entered, release = threading.Event(), threading.Event()
    copy = b.package_manager.copy_into_store
    def delayed_copy(source, manifest):
        entered.set()
        assert release.wait(5)
        return copy(source, manifest)
    monkeypatch.setattr(b.package_manager, "copy_into_store", delayed_copy)
    b_task = asyncio.create_task(b.install(_write(tmp_path / "b-src", "b", "b")))
    a_task = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert not (root / "b" / "1.0.0").exists()
        a_task = asyncio.create_task(a.load_installed())
        for _ in range(100):
            rows = await a.context.install_report.read_packages()
            if "a" in rows and rows["a"].status == "waiting":
                break
            await asyncio.sleep(0.01)
        assert "a" in rows and rows["a"].status == "waiting"
        release.set()
        assert (await b_task)[0].status == "SUCCESS"
        assert (await a_task)[0].status == "SUCCESS"
    finally:
        release.set()
        for task in (a_task, b_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (a_task, b_task) if task is not None), return_exceptions=True)
        await _close(a, a_bus)
        await _close(b, b_bus)
