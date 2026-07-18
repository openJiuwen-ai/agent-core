# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Spec catalog meta-providers for loading rails/tools from file/import/entry_point.

Dynamic class loading (``_load_*``) is module-private. File providers resolve
paths against ``context.extras["source_root"]`` (default ``"."``).
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.manifest import (
    ConstructionInput,
    ElementKind,
    harness_element,
    param_field,
)

RAIL_FILE = "harness.rail.file"
RAIL_IMPORT = "harness.rail.import"
RAIL_ENTRY_POINT = "harness.rail.entry_point"
TOOL_FILE = "harness.tool.file"
TOOL_IMPORT = "harness.tool.import"
TOOL_ENTRY_POINT = "harness.tool.entry_point"

# Serializes file-provider loads that mutate process-global import state
# (sys.path / sys.modules / parent-module attrs). Without it, two concurrent
# extension loads can snapshot/restore each other's aliases.
_IMPORT_STATE_LOCK = threading.RLock()


def _load_dotted_path(dotted: str) -> Any:
    module_path, _, class_name = dotted.rpartition(".")
    if not module_path or not class_name:
        raise ImportError(f"Cannot load dotted path without module and class: {dotted}")
    try:
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        raise ImportError(f"Cannot load '{dotted}': {exc}") from exc


def _load_from_entry_point(name: str, group: str) -> Any:
    try:
        from importlib.metadata import entry_points

        for entry_point in entry_points(group=group):
            if entry_point.name == name:
                return entry_point.load()
        raise ImportError(f"Entry point '{name}' not found in group '{group}'")
    except (ImportError, ValueError):
        raise
    except Exception as exc:
        raise ImportError(f"Failed to load entry point '{name}' from '{group}': {exc}") from exc


def _canonical_extension_module_name(path: Path, package_root: Path | None) -> str | None:
    """Return the canonical dotted module name for a file inside an extension package.

    Runtime extensions declare imports against ``openjiuwen.extensions.harness.<package>``
    and rely on intra-package relative imports (``from .helper import VALUE``).

    Args:
        path: Absolute path to the resolved Python file.
        package_root: Extension package root, or ``None`` for path-less specs.

    Returns:
        The canonical dotted module name, or ``None`` when ``path`` is not located
        under ``package_root`` (in which case the orphan-name fallback is used).
    """
    if package_root is None:
        return None
    root = package_root.expanduser().resolve()
    if not root.is_dir():
        return None
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    base = f"openjiuwen.extensions.harness.{root.name}"
    if not parts:
        return base
    return f"{base}." + ".".join(parts)


def _load_class_from_file(
    file_path: str | Path,
    class_name: str | None,
    *,
    package_root: Path | None = None,
) -> type[Any]:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"ExpertHarness Python file not found: {path}")
    if not class_name:
        raise ValueError(f"ExpertHarness file spec must define class_name: {path}")

    canonical_name = _canonical_extension_module_name(path, package_root)
    module_name = canonical_name or f"_openjiuwen_resource_{path.stem}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load ExpertHarness Python file: {path}")
    module = importlib.util.module_from_spec(spec)
    # Hold the import-state lock across the whole snapshot/mutate/restore window
    # so concurrent extension loads cannot clobber each other's sys.path /
    # sys.modules aliases.
    with _IMPORT_STATE_LOCK:
        with _temporary_sys_path(path.parent, path.parent.parent), _temporary_extension_alias(package_root):
            previous = sys.modules.get(module_name)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            finally:
                if previous is not None:
                    sys.modules[module_name] = previous
                else:
                    sys.modules.pop(module_name, None)
    try:
        loaded = getattr(module, class_name)
    except AttributeError as exc:
        raise ImportError(f"Cannot find class '{class_name}' in ExpertHarness Python file: {path}") from exc
    if not isinstance(loaded, type):
        raise TypeError(f"ExpertHarness entry '{class_name}' is not a class: {path}")
    return loaded


def _sys_path_top_level_names(directory: Path) -> set[str]:
    """Return importable top-level module/package names exposed by a sys.path entry."""
    names: set[str] = set()
    if not directory.is_dir():
        return names
    for entry in directory.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.is_file() and entry.suffix == ".py" and entry.stem != "__init__":
            names.add(entry.stem)
        elif entry.is_dir() and (entry / "__init__.py").is_file():
            names.add(entry.name)
    return names


def _module_resides_under(resolved_paths: tuple[Path, ...], module_name: str) -> bool:
    module = sys.modules.get(module_name)
    if module is None:
        return False
    module_file = getattr(module, "__file__", None)
    if module_file:
        try:
            module_path = Path(module_file).resolve()
        except (OSError, ValueError):
            return False
        for root in resolved_paths:
            try:
                if module_path == root or module_path.is_relative_to(root):
                    return True
            except ValueError:
                if module_path.parent == root:
                    return True
        return False
    for path_entry in getattr(module, "__path__", []) or []:
        try:
            origin = Path(path_entry).resolve()
        except (OSError, ValueError):
            continue
        for root in resolved_paths:
            try:
                if origin == root or origin.is_relative_to(root):
                    return True
            except ValueError:
                if origin == root:
                    return True
    return False


def _snapshot_shadowable_modules(candidate_names: set[str]) -> dict[str, ModuleType | None]:
    snapshots: dict[str, ModuleType | None] = {}
    for name in list(sys.modules):
        if name in candidate_names:
            snapshots[name] = sys.modules[name]
            continue
        for candidate in candidate_names:
            if name.startswith(f"{candidate}."):
                snapshots[name] = sys.modules[name]
                break
    return snapshots


def _normalize_sys_path_key(path: str | Path) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except (OSError, ValueError):
        return str(path)


def _dedupe_resolved_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        path_key = _normalize_sys_path_key(path)
        if path_key in seen:
            continue
        seen.add(path_key)
        unique.append(path)
    return tuple(unique)


def _winning_sys_path_by_name(resolved: tuple[Path, ...]) -> dict[str, Path]:
    """Map top-level importable names to the path entry that wins after prepend."""
    winners: dict[str, Path] = {}
    for path in reversed(resolved):
        for name in _sys_path_top_level_names(path):
            winners.setdefault(name, path)
    return winners


def _module_resides_on_sys_path_entry(path_entry: Path, module_name: str) -> bool:
    """True when ``module_name`` is exported as a top-level name from ``path_entry``."""
    top_level = module_name.split(".", 1)[0]
    if top_level not in _sys_path_top_level_names(path_entry):
        return False
    if not _module_resides_under((path_entry,), module_name):
        return False
    module = sys.modules.get(module_name)
    if module is None:
        return False
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return module_name != top_level
    try:
        relative = Path(module_file).resolve().relative_to(path_entry.resolve())
    except (OSError, ValueError):
        return False
    parts = relative.parts
    if module_name == top_level:
        if parts == (f"{top_level}.py",):
            return True
        return len(parts) >= 2 and parts[0] == top_level and parts[1] == "__init__.py"
    return len(parts) >= 1 and parts[0] == top_level


def _evict_conflicting_shadowed_modules(
    resolved: tuple[Path, ...],
    snapshots: dict[str, ModuleType | None],
) -> None:
    """Drop cached imports that would block extension-local resolution after prepend."""
    winners = _winning_sys_path_by_name(resolved)
    for name in list(snapshots):
        if name not in sys.modules:
            continue
        top_level = name.split(".", 1)[0]
        winning_path = winners.get(top_level)
        if winning_path is None:
            if not _module_resides_under(resolved, name):
                sys.modules.pop(name, None)
            continue
        if not _module_resides_on_sys_path_entry(winning_path, name):
            sys.modules.pop(name, None)


def _prepend_sys_path_entries(
    resolved: tuple[Path, ...],
) -> tuple[list[str], list[tuple[int, str]]]:
    """Atomically prepend paths so later args win the first sys.path slot."""
    inserted: list[str] = []
    moved: list[tuple[int, str]] = []
    path_keys = [_normalize_sys_path_key(path) for path in resolved]
    original_path = list(sys.path)
    claimed: set[int] = set()
    prefix: list[str] = []

    for path_key in reversed(path_keys):
        found_index: int | None = None
        found_entry: str | None = None
        for index, entry in enumerate(original_path):
            if index in claimed:
                continue
            if _normalize_sys_path_key(entry) == path_key:
                found_index = index
                found_entry = entry
                break
        if found_entry is not None and found_index is not None:
            claimed.add(found_index)
            prefix.append(found_entry)
            if found_index != 0:
                moved.append((found_index, found_entry))
            continue
        prefix.append(path_key)
        inserted.append(path_key)

    remaining = [entry for index, entry in enumerate(original_path) if index not in claimed]
    if prefix:
        sys.path[:] = prefix + remaining
    return inserted, moved


@contextmanager
def _temporary_sys_path(*paths: Path):
    resolved = _dedupe_resolved_paths(tuple(path.expanduser().resolve() for path in paths))
    candidate_names: set[str] = set()
    for path in resolved:
        candidate_names.update(_sys_path_top_level_names(path))
    snapshots = _snapshot_shadowable_modules(candidate_names)
    # Clear conflicting imports before prepending so a temporary sys.path entry
    # cannot shadow pre-existing modules during the load window (G.PSL.03).
    _evict_conflicting_shadowed_modules(resolved, snapshots)

    inserted, moved = _prepend_sys_path_entries(resolved)
    try:
        yield
    finally:
        # Evict modules loaded from the inserted paths so a temporary prepend
        # cannot permanently shadow pre-existing imports (G.PSL.03).
        for name in list(sys.modules):
            if name not in candidate_names and not any(
                name.startswith(f"{candidate}.") for candidate in candidate_names
            ):
                continue
            previous = snapshots.get(name)
            current = sys.modules.get(name)
            if previous is None and name not in snapshots:
                if _module_resides_under(resolved, name):
                    sys.modules.pop(name, None)
                continue
            if current is not previous and _module_resides_under(resolved, name):
                sys.modules.pop(name, None)
        for name, module in snapshots.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        for value in reversed(inserted):
            try:
                sys.path.remove(value)
            except ValueError:
                pass
        for original_index, entry in reversed(moved):
            try:
                sys.path.remove(entry)
            except ValueError:
                continue
            insert_at = min(original_index, len(sys.path))
            sys.path.insert(insert_at, entry)


@contextmanager
def _temporary_extension_alias(package_root: Path | None):
    if package_root is None:
        yield
        return

    root = package_root.expanduser().resolve()
    if not root.is_dir():
        yield
        return

    harness_name = "openjiuwen.extensions.harness"
    package_name = f"{harness_name}.{root.name}"
    subtree_prefix = f"{package_name}."
    snapshots: dict[str, ModuleType | None] = {
        harness_name: sys.modules.get(harness_name),
        package_name: sys.modules.get(package_name),
    }
    # Snapshot the whole ``<ext>`` subtree so intra-package submodules imported
    # during the load (e.g. ``<ext>.tools.helper``) are rolled back afterwards,
    # keeping each load hermetic and avoiding cross-package name collisions.
    for name in list(sys.modules):
        if name.startswith(subtree_prefix):
            snapshots[name] = sys.modules.get(name)
    attrs = _module_attrs(
        (
            ("openjiuwen.extensions", "harness"),
            (harness_name, root.name),
        )
    )
    try:
        for name in list(sys.modules):
            if name == package_name or name.startswith(subtree_prefix):
                sys.modules.pop(name, None)
        harness_module = ModuleType(harness_name)
        harness_module.__path__ = [str(root.parent)]
        package_module = ModuleType(package_name)
        package_module.__path__ = [str(root)]
        sys.modules[harness_name] = harness_module
        sys.modules[package_name] = package_module
        _set_parent_attr("openjiuwen.extensions", "harness", harness_module)
        _set_parent_attr(harness_name, root.name, package_module)
        yield
    finally:
        for name in list(sys.modules):
            if (name == package_name or name.startswith(subtree_prefix)) and name not in snapshots:
                sys.modules.pop(name, None)
        for name, module in snapshots.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        _restore_module_attrs(attrs)


def _module_attrs(pairs: tuple[tuple[str, str], ...]) -> dict[tuple[str, str], Any]:
    attrs: dict[tuple[str, str], Any] = {}
    for module_name, attr_name in pairs:
        module = sys.modules.get(module_name)
        attrs[(module_name, attr_name)] = getattr(module, attr_name, None) if module else None
    return attrs


def _set_parent_attr(parent_name: str, attr_name: str, value: ModuleType) -> None:
    try:
        parent = importlib.import_module(parent_name)
    except ImportError:
        return
    setattr(parent, attr_name, value)


def _restore_module_attrs(attrs: dict[tuple[str, str], Any]) -> None:
    for (module_name, attr_name), value in attrs.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        if value is None:
            try:
                delattr(module, attr_name)
            except AttributeError:
                pass
            continue
        setattr(module, attr_name, value)


def _source_root(context: Any) -> Path:
    """Return the Spec-side package root from extras (default ``.``)."""
    extras = getattr(context, "extras", None) or {}
    return Path(str(extras.get("source_root") or ".")).expanduser().resolve()


def _resolve_file_path(file_path: str, context: Any) -> Path:
    """Resolve ``file_path`` against ``source_root`` when relative."""
    path = Path(file_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (_source_root(context) / path).resolve()


class FileLoadInput(ConstructionInput):
    """Shared inputs for file-based meta providers."""

    file_path: str = param_field(default="", description="Python file path (absolute or relative to source_root).")
    class_name: str = param_field(default="", description="Class name to load from the file.")


class ImportLoadInput(ConstructionInput):
    """Shared inputs for import-path meta providers."""

    import_path: str = param_field(default="", description="Dotted module.ClassName path.")


class EntryPointLoadInput(ConstructionInput):
    """Shared inputs for entry-point meta providers."""

    entry_point: str = param_field(default="", description="importlib.metadata entry point name.")


def _build_rail_from_file(params: dict[str, Any], context: Any) -> Any:
    """Load an AgentRail from params.file_path + params.class_name."""
    p = dict(params)
    file_path = p.pop("file_path", None)
    class_name = p.pop("class_name", None)
    if not file_path:
        raise ValueError("harness.rail.file provider requires params.file_path")
    resolved = _resolve_file_path(str(file_path), context)
    rail_cls = _load_class_from_file(
        resolved,
        class_name,
        package_root=_source_root(context),
    )
    rail = rail_cls(**p)
    if not isinstance(rail, AgentRail):
        raise TypeError(f"Resolved rail is not an AgentRail: {resolved} (got {type(rail).__name__})")
    return rail


def _build_rail_from_import(params: dict[str, Any], context: Any) -> Any:
    """Load an AgentRail from params.import_path (dotted module.ClassName)."""
    del context  # Spec meta import path does not need BuildContext.
    p = dict(params)
    import_path = p.pop("import_path", None)
    if not import_path:
        raise ValueError("harness.rail.import provider requires params.import_path")
    cls = _load_dotted_path(import_path)
    return cls(**p)


def _build_rail_from_entry_point(params: dict[str, Any], context: Any) -> Any:
    """Load an AgentRail from importlib.metadata entry_points (group=openjiuwen.rail)."""
    del context
    p = dict(params)
    entry_point = p.pop("entry_point", None)
    if not entry_point:
        raise ValueError("harness.rail.entry_point provider requires params.entry_point")
    cls = _load_from_entry_point(entry_point, "openjiuwen.rail")
    return cls(**p)


def _build_tool_from_file(params: dict[str, Any], context: Any) -> Any:
    """Load a Tool/ToolCard from params.file_path + params.class_name."""
    p = dict(params)
    file_path = p.pop("file_path", None)
    class_name = p.pop("class_name", None)
    if not file_path:
        raise ValueError("harness.tool.file provider requires params.file_path")
    resolved = _resolve_file_path(str(file_path), context)
    tool_cls = _load_class_from_file(
        resolved,
        class_name,
        package_root=_source_root(context),
    )
    return tool_cls(**p)


def _build_tool_from_import(params: dict[str, Any], context: Any) -> Any:
    """Load a Tool from params.import_path (dotted module.ClassName)."""
    del context
    p = dict(params)
    import_path = p.pop("import_path", None)
    if not import_path:
        raise ValueError("harness.tool.import provider requires params.import_path")
    cls = _load_dotted_path(import_path)
    return cls(**p)


def _build_tool_from_entry_point(params: dict[str, Any], context: Any) -> Any:
    """Load a Tool from importlib.metadata entry_points (group=openjiuwen.tool)."""
    del context
    p = dict(params)
    entry_point = p.pop("entry_point", None)
    if not entry_point:
        raise ValueError("harness.tool.entry_point provider requires params.entry_point")
    cls = _load_from_entry_point(entry_point, "openjiuwen.tool")
    return cls(**p)


harness_element(
    kind=ElementKind.RAIL,
    name=RAIL_FILE,
    description="Load a rail class from a Python file under source_root.",
    input_model=FileLoadInput,
    builder=_build_rail_from_file,
)
harness_element(
    kind=ElementKind.RAIL,
    name=RAIL_IMPORT,
    description="Load a rail class from a dotted import path.",
    input_model=ImportLoadInput,
    builder=_build_rail_from_import,
)
harness_element(
    kind=ElementKind.RAIL,
    name=RAIL_ENTRY_POINT,
    description="Load a rail class from an openjiuwen.rail entry point.",
    input_model=EntryPointLoadInput,
    builder=_build_rail_from_entry_point,
)
harness_element(
    kind=ElementKind.TOOL,
    name=TOOL_FILE,
    description="Load a tool class from a Python file under source_root.",
    input_model=FileLoadInput,
    builder=_build_tool_from_file,
)
harness_element(
    kind=ElementKind.TOOL,
    name=TOOL_IMPORT,
    description="Load a tool class from a dotted import path.",
    input_model=ImportLoadInput,
    builder=_build_tool_from_import,
)
harness_element(
    kind=ElementKind.TOOL,
    name=TOOL_ENTRY_POINT,
    description="Load a tool class from an openjiuwen.tool entry point.",
    input_model=EntryPointLoadInput,
    builder=_build_tool_from_entry_point,
)


__all__ = [
    "RAIL_FILE",
    "RAIL_IMPORT",
    "RAIL_ENTRY_POINT",
    "TOOL_FILE",
    "TOOL_IMPORT",
    "TOOL_ENTRY_POINT",
]
