# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""EX-02: plan, prepare, active records, and lifecycle decisions."""

import sys
from pathlib import Path

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.extension.errors import ExtensionFailure
from openjiuwen.core.extension.manifest import load_manifest
from openjiuwen.core.extension.orchestrator import Orchestrator
from openjiuwen.core.extension.runtime import ExtensionRegistrar, Runtime
from openjiuwen.core.extension.schema.manifest import PackageDependency
from openjiuwen.core.extension.schema.records import ActivePackageRecord
from tests.unit_tests.core.extension.conftest import make_active, make_manifest, write_package


def test_prepare_collects_user_and_builtin_and_rejects_core(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    extension = tmp_path / "extensions" / "ext" / "extension.py"
    extension.write_text(
        "def create_extension():\n"
        "    return Extension()\n\n"
        "class Extension:\n"
        "    def register(self, registrar):\n"
        "        def handler(event, ops, next_handler):\n"
        "            return 'ok'\n"
        "        registrar.register_event_handler('tool.call', handler, {}, 'user')\n"
        "        registrar.register_event_handler("
        "'tool.call', handler, {'tool': 'Bash'}, 'builtin')\n",
        encoding="utf-8",
    )
    manifest = load_manifest(tmp_path)
    runtime = Runtime()
    plan = Orchestrator().build_plan(tmp_path, manifest, "install")
    prepared = runtime.prepare(plan)

    assert prepared.error is None
    assert prepared.candidate is not None
    assert [item.handler_level for item in prepared.candidate.declarations] == [
        "user",
        "builtin",
    ]
    runtime.record_active(runtime.active_record_for(manifest, tmp_path, prepared.candidate))

    extension.write_text(
        "def create_extension():\n"
        "    return Extension()\n\n"
        "class Extension:\n"
        "    def register(self, registrar):\n"
        "        def handler(event, ops, next_handler):\n"
        "            return None\n"
        "        registrar.register_event_handler('tool.call', handler, {}, 'core')\n",
        encoding="utf-8",
    )
    failed = runtime.prepare(
        Orchestrator().build_plan(tmp_path, manifest, "reload", runtime.get_active("demo"))
    )

    assert failed.candidate is None
    assert failed.error is not None
    assert failed.error.status == StatusCode.EXTENSION_HANDLER_LEVEL_FORBIDDEN
    assert runtime.get_active("demo") is not None


def test_registrar_rejects_non_callable_handler() -> None:
    registrar = ExtensionRegistrar()
    with pytest.raises(ExtensionFailure) as caught:
        registrar.register_event_handler("tool.call", object(), {})  # type: ignore[arg-type]
    assert caught.value.status == StatusCode.EXTENSION_LOAD_FAILED


def test_registrar_rejects_non_mapping_match_pattern() -> None:
    registrar = ExtensionRegistrar()

    def handler(event, ops, next_handler):
        return None

    with pytest.raises(ExtensionFailure) as caught:
        registrar.register_event_handler("tool.call", handler, ["not", "a", "map"])  # type: ignore[arg-type]
    assert caught.value.status == StatusCode.EXTENSION_LOAD_FAILED


def test_a_new_prepare_sees_new_code_and_keeps_the_old_callable(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    manifest = load_manifest(tmp_path)
    runtime = Runtime()
    first = runtime.prepare(Orchestrator().build_plan(tmp_path, manifest, "install"))
    assert first.candidate is not None
    old_handler = first.candidate.declarations[0].handler
    old_module = first.candidate.module_names[0]

    extension = tmp_path / "extensions" / "ext" / "extension.py"
    extension.write_text(
        extension.read_text(encoding="utf-8").replace("VALUE = 1", "VALUE = 2"),
        encoding="utf-8",
    )
    record = runtime.active_record_for(manifest, tmp_path, first.candidate)
    runtime.record_active(record)
    second = runtime.prepare(Orchestrator().build_plan(tmp_path, manifest, "reload", record))

    assert second.candidate is not None
    assert second.candidate.module_names[0] != old_module
    assert old_handler(None, None, None) == 1
    assert second.candidate.declarations[0].handler(None, None, None) == 2
    assert old_module in sys.modules
    runtime.abort(first.candidate)
    assert old_module not in sys.modules
    assert old_handler(None, None, None) == 1


def test_prepare_failure_releases_modules(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    extension = tmp_path / "extensions" / "ext" / "extension.py"
    extension.write_text(
        "def create_extension():\n"
        "    raise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    before = set(sys.modules)
    prepared = Runtime().prepare(
        Orchestrator().build_plan(tmp_path, load_manifest(tmp_path), "install")
    )
    assert prepared.candidate is None
    assert prepared.error is not None
    assert prepared.error.status == StatusCode.EXTENSION_LOAD_FAILED
    assert not any(name.startswith("openjiuwen_ext_") for name in set(sys.modules) - before)


def test_prepare_missing_factory_fails(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    extension = tmp_path / "extensions" / "ext" / "extension.py"
    extension.write_text("VALUE = 1\n", encoding="utf-8")
    prepared = Runtime().prepare(
        Orchestrator().build_plan(tmp_path, load_manifest(tmp_path), "install")
    )
    assert prepared.candidate is None
    assert prepared.error is not None
    assert prepared.error.status == StatusCode.EXTENSION_LOAD_FAILED


def test_uninstall_stops_when_busy_or_required_and_does_not_drop_the_record() -> None:
    runtime = Runtime()
    runtime.record_active(make_active("base"))
    runtime.record_active(make_active("user", requires=(PackageDependency("base", ">=1.0.0"),)))

    in_use = runtime.uninstall_decision("base")
    assert in_use is not None
    assert in_use.status == StatusCode.EXTENSION_PACKAGE_IN_USE
    with pytest.raises(ExtensionFailure):
        runtime.drop_active("base")

    runtime.drop_active("user")
    runtime.begin_calls(["base"])
    busy = runtime.uninstall_decision("base")
    assert busy is not None
    assert busy.status == StatusCode.EXTENSION_PACKAGE_BUSY
    with pytest.raises(ExtensionFailure):
        runtime.drop_active("base")
    assert runtime.get_active("base") is not None

    runtime.end_calls(["base"])
    assert runtime.uninstall_decision("base") is None
    runtime.drop_active("base")
    assert runtime.get_active("base") is None


def test_end_calls_without_begin_raises() -> None:
    runtime = Runtime()
    with pytest.raises(ExtensionFailure) as caught:
        runtime.end_calls(["missing"])
    assert caught.value.status == StatusCode.EXTENSION_INFLIGHT_MISMATCH


def test_uninstall_missing_package_is_not_installed() -> None:
    error = Runtime().uninstall_decision("absent")
    assert error is not None
    assert error.status == StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED


def test_package_relative_imports_reach_sibling_parent_and_child(tmp_path: Path) -> None:
    extension_dir = tmp_path / "extensions" / "ext"
    helpers = extension_dir / "helpers"
    helpers.mkdir(parents=True)
    (tmp_path / "extensions" / "common.py").write_text("SHARED = 4\n", encoding="utf-8")
    (helpers / "inner.py").write_text("CHILD = 5\n", encoding="utf-8")
    (helpers / "__init__.py").write_text("from .inner import CHILD\n", encoding="utf-8")
    (extension_dir / "util.py").write_text("SIBLING = 3\n", encoding="utf-8")
    (extension_dir / "handler.py").write_text(
        "from .util import SIBLING\n\n"
        "def build():\n"
        "    return SIBLING\n",
        encoding="utf-8",
    )
    (extension_dir / "extension.py").write_text(
        "from . import handler\n"
        "from .handler import build\n"
        "from .helpers import CHILD\n"
        "from ..common import SHARED\n\n"
        "def create_extension():\n"
        "    return Extension()\n\n"
        "class Extension:\n"
        "    def register(self, registrar):\n"
        "        def on_call(event, ops, next_handler):\n"
        "            return (build(), CHILD, SHARED)\n"
        "        registrar.register_event_handler('tool.call', on_call, {}, 'builtin')\n",
        encoding="utf-8",
    )
    (tmp_path / "manifest.yaml").write_text(
        "schemaVersion: 1\n"
        "package:\n"
        "  id: demo\n"
        "  version: 1.0.0\n"
        "extensions:\n"
        "  - id: ext\n"
        "    runtime: backend\n"
        "    module: extensions/ext/extension.py\n",
        encoding="utf-8",
    )
    manifest = load_manifest(tmp_path)
    prepared = Runtime().prepare(Orchestrator().build_plan(tmp_path, manifest, "install"))

    assert prepared.error is None
    assert prepared.candidate is not None
    assert prepared.candidate.declarations[0].handler(None, None, None) == (3, 5, 4)


def test_plan_orders_extensions_and_advances_generation(tmp_path: Path) -> None:
    base = tmp_path / "extensions" / "base" / "extension.py"
    child = tmp_path / "extensions" / "child" / "extension.py"
    for path in (base, child):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "def create_extension():\n"
            "    return type('E', (), {'register': lambda self, registrar: None})()\n",
            encoding="utf-8",
        )
    (tmp_path / "manifest.yaml").write_text(
        "schemaVersion: 1\n"
        "package:\n"
        "  id: demo\n"
        "  version: 1.0.0\n"
        "requires:\n"
        "  host: '>=0.2.5'\n"
        "extensions:\n"
        "  - id: child\n"
        "    runtime: backend\n"
        "    module: extensions/child/extension.py\n"
        "    requires:\n"
        "      - id: base\n"
        "  - id: base\n"
        "    runtime: backend\n"
        "    module: extensions/base/extension.py\n",
        encoding="utf-8",
    )
    manifest = load_manifest(tmp_path)
    orchestrator = Orchestrator()
    install_plan = orchestrator.build_plan(tmp_path, manifest, "install")
    assert [item.id for item in install_plan.extensions] == ["base", "child"]
    assert install_plan.target_generation == 1
    assert manifest.requires_host == ">=0.2.5"

    record = ActivePackageRecord(
        package_id="demo",
        version="1.0.0",
        root=tmp_path,
        generation=1,
        requires_packages=(),
    )
    reload_plan = orchestrator.build_plan(tmp_path, manifest, "reload", record)
    uninstall_plan = orchestrator.build_plan(tmp_path, manifest, "uninstall", record)

    assert reload_plan.previous_generation == 1
    assert reload_plan.target_generation == 2
    assert [item.id for item in uninstall_plan.extensions] == ["child", "base"]


def test_plan_rejects_extension_cycle(tmp_path: Path) -> None:
    for name in ("a", "b"):
        path = tmp_path / "extensions" / name / "extension.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "def create_extension():\n"
            "    return type('E', (), {'register': lambda self, registrar: None})()\n",
            encoding="utf-8",
        )
    (tmp_path / "manifest.yaml").write_text(
        "schemaVersion: 1\n"
        "package:\n"
        "  id: demo\n"
        "  version: 1.0.0\n"
        "extensions:\n"
        "  - id: a\n"
        "    runtime: backend\n"
        "    module: extensions/a/extension.py\n"
        "    requires:\n"
        "      - id: b\n"
        "  - id: b\n"
        "    runtime: backend\n"
        "    module: extensions/b/extension.py\n"
        "    requires:\n"
        "      - id: a\n",
        encoding="utf-8",
    )
    with pytest.raises(ExtensionFailure) as caught:
        Orchestrator().build_plan(tmp_path, load_manifest(tmp_path), "install")
    assert caught.value.status == StatusCode.EXTENSION_DEPENDENCY_CYCLE


def test_plan_install_rejects_when_already_active(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    manifest = load_manifest(tmp_path)
    previous = make_active("demo")
    with pytest.raises(ExtensionFailure) as caught:
        Orchestrator().build_plan(tmp_path, manifest, "install", previous)
    assert caught.value.status == StatusCode.EXTENSION_PACKAGE_EXISTS


def test_plan_reload_and_uninstall_require_active_record(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    manifest = load_manifest(tmp_path)
    with pytest.raises(ExtensionFailure) as reload_caught:
        Orchestrator().build_plan(tmp_path, manifest, "reload")
    assert reload_caught.value.status == StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED

    with pytest.raises(ExtensionFailure) as uninstall_caught:
        Orchestrator().build_plan(tmp_path, manifest, "uninstall")
    assert uninstall_caught.value.status == StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED


def test_runtime_check_reload_delegates() -> None:
    runtime = Runtime()
    runtime.record_active(make_active("base", "1.0.0"))
    assert runtime.check_reload("base", make_manifest("base", "1.1.0")) is None
    assert runtime.check_reload("missing", make_manifest("missing")) is not None


def test_active_packages_are_sorted_by_id() -> None:
    runtime = Runtime()
    runtime.record_active(make_active("b"))
    runtime.record_active(make_active("a"))
    assert [item.package_id for item in runtime.active_packages()] == ["a", "b"]
