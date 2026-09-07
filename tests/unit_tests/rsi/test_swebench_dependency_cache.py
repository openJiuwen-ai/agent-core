# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Dependency caching must preserve the official revision and network bounds."""

import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.rsi.harness_rsi.evaluator import swebench_runtime as runtime
from openjiuwen.rsi.harness_rsi.evaluator._swebench_official_support import sitecustomize


@pytest.fixture
def manifests(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    folder = workspace / "requirements"
    folder.mkdir(parents=True)
    (folder / "py3.txt").write_text("-r base.txt\nextra\n", encoding="utf-8")
    (folder / "base.txt").write_text("dependency==2\n", encoding="utf-8")
    monkeypatch.setattr(runtime, "_DEPENDENCY_CACHE_ROOT", tmp_path / "cache")
    return workspace


def test_requirement_directory_files_and_old_cache_are_refreshed(manifests):
    config = {"repo": "org/repo", "base_commit": "abc"}
    cache = runtime._dependency_cache_dir(config)
    cache.mkdir(parents=True)
    (cache / "cache.json").write_text('{"files": []}', encoding="utf-8")

    assert runtime._cache_official_dependency_files(config, manifests) == cache
    assert (cache / "requirements/py3.txt").is_file()
    assert (cache / "requirements/base.txt").read_text() == "dependency==2\n"
    assert json.loads((cache / "cache.json").read_text())["version"] == 2


def test_setup_revision_is_not_replaced_with_base_revision(manifests, monkeypatch):
    request = Mock(
        return_value=SimpleNamespace(
            status_code=200,
            content=b"dependency==1\n",
            raise_for_status=Mock(),
        )
    )
    monkeypatch.setattr("requests.get", request)
    cache = runtime._cache_official_dependency_files(
        {"repo": "org/repo", "base_commit": "base", "environment_setup_commit": "setup"},
        manifests,
    )
    assert (cache / "requirements/base.txt").read_text() == "dependency==1\n"
    assert request.call_count == 2
    for call in request.call_args_list:
        assert "/org/repo/setup/requirements/" in call.args[0]
        assert call.kwargs["timeout"] == (10, 60)


def test_failed_fetch_does_not_publish_a_complete_cache(manifests, monkeypatch):
    import requests

    monkeypatch.setattr("requests.get", Mock(side_effect=requests.ReadTimeout("stalled")))
    config = {"repo": "org/repo", "base_commit": "base", "environment_setup_commit": "setup"}
    with pytest.raises(requests.ReadTimeout):
        runtime._cache_official_dependency_files(config, manifests)
    assert not (runtime._dependency_cache_dir(config) / "cache.json").exists()


def test_official_request_timeout_is_local_and_preserves_explicit_values():
    original = SimpleNamespace(get=Mock(return_value="response"), sentinel=object())
    bounded = sitecustomize._BoundedRequests(original)
    assert bounded.get("https://example.com/manifest", headers={"x": "y"}) == "response"
    original.get.assert_called_once_with("https://example.com/manifest", headers={"x": "y"}, timeout=(10, 60))
    bounded.get("https://example.com/manifest", timeout=3)
    assert original.get.call_args.kwargs["timeout"] == 3
    assert bounded.sentinel is original.sentinel


def test_official_timeout_guard_is_loaded_without_a_cache():
    assert runtime._official_python_prefix(
        python_path="/python",
        support_dir="/support",
        dependency_cache_root=None,
    ) == ["env", "PYTHONPATH=/support", "/python"]


def test_missing_included_file_does_not_silently_drop_requirements(manifests):
    (manifests / "requirements/base.txt").unlink()
    with pytest.raises(FileNotFoundError):
        sitecustomize._read_requirements(manifests / "requirements/py3.txt", manifests)


def test_official_cache_is_used_only_for_the_matching_revision(manifests, monkeypatch):
    config = {"repo": "org/repo", "base_commit": "abc"}
    cache = runtime._cache_official_dependency_files(config, manifests)
    monkeypatch.setenv("ACH_SWEBENCH_DEPENDENCY_CACHE", str(cache))
    modules = {}
    for name in [
        "swebench",
        "swebench.harness",
        "swebench.harness.constants",
        "swebench.harness.test_spec",
        "swebench.harness.test_spec.python",
    ]:
        modules[name] = ModuleType(name)
        monkeypatch.setitem(sys.modules, name, modules[name])
    constants = modules["swebench.harness.constants"]
    constants.MAP_REPO_TO_REQS_PATHS = {"org/repo": ["requirements/py3.txt"]}
    constants.MAP_REPO_TO_ENV_YML_PATHS = {}
    official = modules["swebench.harness.test_spec.python"]
    original = Mock(return_value="remote requirements")
    official.get_requirements_by_commit = original
    official.get_environment_yml_by_commit = Mock()
    official.requests = SimpleNamespace(get=Mock())
    sitecustomize._install_local_dependency_cache()
    assert "dependency==2" in official.get_requirements_by_commit("org/repo", "abc")
    original.assert_not_called()
    assert official.get_requirements_by_commit("org/repo", "other") == "remote requirements"
    original.assert_called_once_with("org/repo", "other")
