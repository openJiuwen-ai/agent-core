# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Make official SWE-bench TestSpec creation use pristine local manifests."""

from __future__ import annotations

import os
import json
from pathlib import Path


class _BoundedRequests:
    """Bound only TestSpec dependency reads, not unrelated Requests users."""

    def __init__(self, original):
        self._original = original

    def get(self, *args, **kwargs):
        kwargs.setdefault("timeout", (10, 60))
        return self._original.get(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._original, name)


def _install_local_dependency_cache() -> None:
    from swebench.harness.constants import (  # pylint: disable=import-outside-toplevel
        MAP_REPO_TO_ENV_YML_PATHS,
        MAP_REPO_TO_REQS_PATHS,
    )
    from swebench.harness.test_spec import python as test_spec_python

    if not isinstance(test_spec_python.requests, _BoundedRequests):
        test_spec_python.requests = _BoundedRequests(test_spec_python.requests)
    cache_value = os.environ.get("ACH_SWEBENCH_DEPENDENCY_CACHE", "").strip()
    if not cache_value:
        return
    cache_root = Path(cache_value)
    marker = cache_root / "cache.json"
    if not marker.is_file():
        return
    manifest = json.loads(marker.read_text(encoding="utf-8"))
    if manifest.get("version") != 2:
        return
    cached_files = set(manifest.get("files", []))

    original_requirements = test_spec_python.get_requirements_by_commit
    original_environment = test_spec_python.get_environment_yml_by_commit

    def cached_requirements(repo: str, commit: str) -> str:
        if repo != manifest.get("repo") or commit != manifest.get("environment_setup_commit"):
            return original_requirements(repo, commit)
        paths = MAP_REPO_TO_REQS_PATHS.get(repo, [])
        for relative in paths:
            candidate = cache_root / relative
            if relative not in cached_files or not candidate.is_file():
                continue
            try:
                return _read_requirements(candidate, cache_root, cached_files)
            except FileNotFoundError:
                return original_requirements(repo, commit)
        return original_requirements(repo, commit)

    def cached_environment(repo: str, commit: str, env_name: str) -> str:
        if repo != manifest.get("repo") or commit != manifest.get("environment_setup_commit"):
            return original_environment(repo, commit, env_name)
        paths = MAP_REPO_TO_ENV_YML_PATHS.get(repo, [])
        for relative in paths:
            candidate = cache_root / relative
            if relative not in cached_files or not candidate.is_file():
                continue
            lines = candidate.read_text(encoding="utf-8").splitlines()
            return "\n".join(f"name: {env_name}" if line.startswith("name:") else line for line in lines)
        return original_environment(repo, commit, env_name)

    test_spec_python.get_requirements_by_commit = cached_requirements
    test_spec_python.get_environment_yml_by_commit = cached_environment


def _read_requirements(
    path: Path,
    cache_root: Path,
    cached_files: set[str] | None = None,
) -> str:
    """Mirror SWE-bench recursive requirements expansion from local files."""
    original: list[str] = []
    additional: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("-r"):
            nested = (path.parent / stripped[2:].strip()).resolve()
            try:
                relative = nested.relative_to(cache_root.resolve()).as_posix()
            except ValueError:
                continue
            if nested.is_file() and (cached_files is None or relative in cached_files):
                additional.extend(
                    nested_line
                    for nested_line in nested.read_text(encoding="utf-8").splitlines()
                    if not _excluded_requirement(nested_line)
                )
            else:
                raise FileNotFoundError(nested)
        elif not _excluded_requirement(line):
            original.append(line)
    additional.append("\n".join(original))
    return "\n".join(additional)


def _excluded_requirement(line: str) -> bool:
    stripped = line.strip()
    return any(stripped.startswith(prefix) for prefix in ("-e .", "#", ".[test"))


try:
    _install_local_dependency_cache()
except (ImportError, OSError, ValueError):
    # If the installed SWE-bench version changes, fall back to its official
    # behavior; the host runtime will still classify/retry transport failures.
    pass
