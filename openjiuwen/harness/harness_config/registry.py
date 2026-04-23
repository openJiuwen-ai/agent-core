# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""HarnessConfigRegistry: discover and manage installed harness config packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Optional, Set, Union

if TYPE_CHECKING:
    from openjiuwen.core.foundation.llm.model import Model
    from openjiuwen.harness.deep_agent import DeepAgent


@dataclass
class HarnessConfigInfo:
    """Metadata for a registered harness config."""

    id: str
    name: str
    version: Optional[str] = None
    package_name: Optional[str] = None
    config_path: Optional[Path] = None
    enabled: bool = True


class HarnessConfigRegistry:
    """Discover and manage harness configs registered via Python entry_points.

    Harness configs expose themselves through::

        [project.entry-points."openjiuwen.harness_config"]
        my-task = "my_custom_pack:get_config_path"

    where ``get_config_path`` is a zero-argument callable that returns the
    path to the package's ``harness_config.yaml``::

        # my_custom_pack/__init__.py
        from pathlib import Path
        def get_config_path() -> Path:
            return Path(__file__).parent / "harness_config.yaml"

    After ``pip install my-custom-pack``, ``HarnessConfigRegistry.discover()``
    returns a ``HarnessConfigInfo`` entry with ``config_path`` populated.
    Call ``HarnessConfigRegistry.load("my-task", model=model)`` to build a
    ``DeepAgent`` directly from the installed config.
    """

    _cache: ClassVar[Optional[List[HarnessConfigInfo]]] = None
    _disabled: ClassVar[Set[str]] = set()

    @classmethod
    def discover(cls) -> List[HarnessConfigInfo]:
        """Return all installed and enabled harness configs (result is cached)."""
        if cls._cache is None:
            cls._cache = cls._scan_entry_points()
        return [r for r in cls._cache if r.id not in cls._disabled]

    @classmethod
    def get(cls, config_id: str) -> Optional[HarnessConfigInfo]:
        """Return the ``HarnessConfigInfo`` for *config_id*, or ``None`` if not found."""
        return next((r for r in cls.discover() if r.id == config_id), None)

    @classmethod
    def load(
        cls,
        config_id: str,
        model: "Model",
        params: Optional[Dict[str, Any]] = None,
        workspace_root: Optional[Union[str, Path]] = None,
    ) -> "DeepAgent":
        """Discover, load, and build a harness config by id.

        Equivalent to::

            info   = HarnessConfigRegistry.get(config_id)
            resolved = HarnessConfigLoader.load(info.config_path, params=params)
            return HarnessConfigBuilder.build(resolved, model=model)

        Args:
            config_id:      The entry_point name registered under
                            ``openjiuwen.harness_config``.
            model:          Pre-constructed ``Model`` instance for LLM calls.
            params:         Jinja2 render parameters for the config YAML.
            workspace_root: Override the workspace root path.

        Raises:
            KeyError:   *config_id* is not installed or is disabled.
            ValueError: The package's ``get_config_path()`` did not return a
                        valid path during the discovery scan.
        """
        from openjiuwen.harness.harness_config.builder import HarnessConfigBuilder
        from openjiuwen.harness.harness_config.loader import HarnessConfigLoader

        info = cls.get(config_id)
        if info is None:
            installed = [r.id for r in cls.discover()]
            raise KeyError(f"HarnessConfig '{config_id}' not found or is disabled. Installed: {installed}")
        if info.config_path is None:
            raise ValueError(
                f"HarnessConfig '{config_id}' has no config_path. "
                "Ensure the package's get_config_path() returns a valid Path."
            )

        resolved = HarnessConfigLoader.load(info.config_path, params=params, workspace_root=workspace_root)
        return HarnessConfigBuilder.build(resolved, model=model, workspace_root=workspace_root)

    @classmethod
    def disable(cls, config_id: str) -> None:
        """Disable a config by id (survives cache invalidation)."""
        cls._disabled.add(config_id)

    @classmethod
    def enable(cls, config_id: str) -> None:
        """Re-enable a previously disabled config."""
        cls._disabled.discard(config_id)

    @classmethod
    def inspect(cls, package_name: str) -> List[HarnessConfigInfo]:
        """Return all configs (including disabled) from *package_name*."""
        if cls._cache is None:
            cls._cache = cls._scan_entry_points()
        return [r for r in cls._cache if r.package_name == package_name]

    @classmethod
    def invalidate_cache(cls) -> None:
        """Flush the in-memory cache so the next call re-scans entry_points."""
        cls._cache = None

    @classmethod
    def _scan_entry_points(cls) -> List[HarnessConfigInfo]:
        results: List[HarnessConfigInfo] = []
        try:
            from importlib.metadata import entry_points

            for ep in entry_points(group="openjiuwen.harness_config"):
                try:
                    dist = ep.dist
                    config_path = cls._resolve_config_path(ep)
                    results.append(
                        HarnessConfigInfo(
                            id=ep.name,
                            name=ep.name,
                            version=dist.version if dist else None,
                            package_name=dist.name if dist else None,
                            config_path=config_path,
                        )
                    )
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        return results

    @staticmethod
    def _resolve_config_path(ep: Any) -> Optional[Path]:
        """Call ep.load()() to get the yaml path from get_config_path()."""
        try:
            get_path = ep.load()
            result = get_path()
            return Path(result).resolve()
        except Exception:  # noqa: BLE001
            return None


__all__ = ["HarnessConfigInfo", "HarnessConfigRegistry"]
