# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Load runtime extensions from a session-local runtime directory."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

from openjiuwen.auto_harness.schema import (
    RuntimeExtensionArtifact,
)
from openjiuwen.harness.harness_config.loader import (
    HarnessConfigLoader,
    ResolvedHarnessConfig,
)

_OFFICIAL_PREFIX = "openjiuwen.extensions.harness."
_RUNTIME_PREFIX = "openjiuwen_runtime_extensions"


def load_runtime_rails(
    runtime_ext: RuntimeExtensionArtifact,
    *,
    session_id: str,
) -> list[type[Any]]:
    """Load rail classes declared by a runtime extension manifest."""
    resolved = _load_runtime_config(runtime_ext)
    rails: list[type[Any]] = []
    for spec in (
        resolved.config.resources.rails
        if resolved.config.resources
        else []
    ):
        if spec.type != "package":
            continue
        if not spec.module or not spec.class_name:
            continue
        rails.append(
            _load_runtime_class(
                runtime_ext=runtime_ext,
                session_id=session_id,
                module_name=spec.module,
                class_name=spec.class_name,
            )
        )
    return rails


def load_runtime_tools(
    runtime_ext: RuntimeExtensionArtifact,
    *,
    session_id: str,
) -> list[type[Any]]:
    """Load tool classes declared by a runtime extension manifest."""
    resolved = _load_runtime_config(runtime_ext)
    tools: list[type[Any]] = []
    for spec in (
        resolved.config.resources.tools
        if resolved.config.resources
        else []
    ):
        if spec.type != "package":
            continue
        if not spec.module or not spec.class_name:
            continue
        tools.append(
            _load_runtime_class(
                runtime_ext=runtime_ext,
                session_id=session_id,
                module_name=spec.module,
                class_name=spec.class_name,
            )
        )
    return tools


def load_runtime_skill_dirs(
    runtime_ext: RuntimeExtensionArtifact,
) -> list[str]:
    """Return absolute skill directory paths declared by a runtime extension.

    Resolves relative dirs from ``resources.skills.dirs``
    against the extension root.
    """
    resolved = _load_runtime_config(runtime_ext)
    resources = resolved.config.resources
    if not resources or not resources.skills:
        return []
    root = Path(runtime_ext.runtime_path).resolve()
    dirs: list[str] = []
    for d in resources.skills.dirs:
        skill_path = root / d
        if skill_path.is_dir():
            dirs.append(str(skill_path))
    return dirs


def _load_runtime_config(
    runtime_ext: RuntimeExtensionArtifact,
) -> ResolvedHarnessConfig:
    return HarnessConfigLoader.load(
        runtime_ext.config_path,
    )


def _load_runtime_class(
    *,
    runtime_ext: RuntimeExtensionArtifact,
    session_id: str,
    module_name: str,
    class_name: str,
) -> type[Any]:
    extension_name = runtime_ext.extension_name
    prefix = f"{_OFFICIAL_PREFIX}{extension_name}"
    if not (
        module_name == prefix
        or module_name.startswith(f"{prefix}.")
    ):
        raise ValueError(
            "Runtime module does not belong to runtime extension "
            f"'{extension_name}': {module_name}"
        )

    relative_module = module_name[len(prefix):].lstrip(".")
    root = Path(runtime_ext.runtime_path).resolve()
    unique_base = (
        f"{_RUNTIME_PREFIX}.{session_id}.{extension_name}"
    )

    # Register the official prefix namespace hierarchy
    # BEFORE _ensure_package, because _ensure_package may
    # exec __init__.py which contains absolute imports like
    # ``from openjiuwen.extensions.harness.<ext>.tools…``.
    _ensure_official_namespace(
        extension_name=extension_name,
        extension_root=root,
    )

    _ensure_package(
        module_name=unique_base,
        package_path=root,
    )

    if not relative_module:
        target_module = unique_base
        module_path = root / "__init__.py"
    else:
        parts = relative_module.split(".")
        current_path = root
        current_module = unique_base
        official_module = prefix
        for part in parts[:-1]:
            current_path = current_path / part
            current_module = f"{current_module}.{part}"
            official_module = f"{official_module}.{part}"
            _ensure_package(
                module_name=current_module,
                package_path=current_path,
            )
            # Mirror under official prefix
            _ensure_package(
                module_name=official_module,
                package_path=current_path,
            )
        target_module = f"{unique_base}.{relative_module}"
        module_path = root.joinpath(
            *parts
        ).with_suffix(".py")

    module = _load_module_from_path(
        module_name=target_module,
        module_path=module_path,
        package_path=(
            module_path.parent
            if module_path.name == "__init__.py"
            else None
        ),
    )

    # Alias under the official prefix so cross-module
    # imports within the extension work.
    official_name = (
        f"{prefix}.{relative_module}"
        if relative_module
        else prefix
    )
    sys.modules.setdefault(official_name, module)

    return getattr(module, class_name)


def _ensure_official_namespace(
    *,
    extension_name: str,
    extension_root: Path,
) -> None:
    """Register ``openjiuwen.extensions.harness.<ext>`` hierarchy.

    Generated extension code uses absolute imports like
    ``from openjiuwen.extensions.harness.<ext>.tools.helper
    import …``.  The ``openjiuwen.extensions.harness``
    sub-package does not exist in the installed tree, so we
    inject synthetic namespace packages into ``sys.modules``
    to make these imports resolve at runtime.

    We also scan the extension root for sub-directories
    containing ``__init__.py`` and register them so that
    cross-module imports (e.g. rail importing from tools)
    work correctly.
    """
    prefix_parts = _OFFICIAL_PREFIX.rstrip(".").split(".")
    # Walk up from extension_root to find the ancestor
    # that corresponds to the first prefix part.
    ancestor = extension_root
    for _ in prefix_parts:
        ancestor = ancestor.parent

    # Register each level of the official prefix
    accumulated = ""
    for i, part in enumerate(prefix_parts):
        accumulated = (
            f"{accumulated}.{part}" if accumulated else part
        )
        if accumulated not in sys.modules:
            mod = types.ModuleType(accumulated)
            pkg_path = ancestor
            for p in prefix_parts[: i + 1]:
                pkg_path = pkg_path / p
            mod.__path__ = [str(pkg_path)]  # type: ignore[attr-defined]
            sys.modules[accumulated] = mod

    # Register the extension package itself
    ext_fqn = f"{_OFFICIAL_PREFIX}{extension_name}"
    if ext_fqn not in sys.modules:
        mod = types.ModuleType(ext_fqn)
        mod.__path__ = [str(extension_root)]  # type: ignore[attr-defined]
        sys.modules[ext_fqn] = mod

    # Register all sub-packages (directories with
    # __init__.py) so cross-module imports work.
    # Load each __init__.py via _load_module_from_path
    # instead of creating empty synthetic modules, so
    # that re-exports inside __init__.py resolve correctly
    # when a sibling module triggers an import chain.
    for init_file in sorted(
        extension_root.rglob("__init__.py")
    ):
        pkg_dir = init_file.parent
        if pkg_dir == extension_root:
            continue
        relative = pkg_dir.relative_to(extension_root)
        sub_fqn = (
            f"{ext_fqn}."
            + str(relative)
            .replace("/", ".")
            .replace("\\", ".")
        )
        if sub_fqn not in sys.modules:
            _load_module_from_path(
                module_name=sub_fqn,
                module_path=init_file,
                package_path=pkg_dir,
            )


def _ensure_package(
    *,
    module_name: str,
    package_path: Path,
) -> types.ModuleType:
    parent_name, _, _ = module_name.rpartition(".")
    if parent_name:
        _ensure_package(
            module_name=parent_name,
            package_path=package_path.parent,
        )

    existing = sys.modules.get(module_name)
    if isinstance(existing, types.ModuleType):
        return existing

    init_path = package_path / "__init__.py"
    if init_path.is_file():
        return _load_module_from_path(
            module_name=module_name,
            module_path=init_path,
            package_path=package_path,
        )

    module = types.ModuleType(module_name)
    module.__path__ = [str(package_path)]  # type: ignore[attr-defined]
    sys.modules[module_name] = module
    return module


def _load_module_from_path(
    *,
    module_name: str,
    module_path: Path,
    package_path: Path | None,
) -> types.ModuleType:
    existing = sys.modules.get(module_name)
    if isinstance(existing, types.ModuleType):
        return existing
    if not module_path.is_file():
        raise FileNotFoundError(
            f"Runtime extension module not found: {module_path}"
        )
    kwargs: dict[str, Any] = {}
    if package_path is not None:
        kwargs["submodule_search_locations"] = [
            str(package_path)
        ]
    spec = importlib.util.spec_from_file_location(
        module_name,
        module_path,
        **kwargs,
    )
    if spec is None or spec.loader is None:
        raise ImportError(
            f"Cannot create import spec for {module_path}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


__all__ = [
    "load_runtime_rails",
    "load_runtime_skill_dirs",
    "load_runtime_tools",
]
