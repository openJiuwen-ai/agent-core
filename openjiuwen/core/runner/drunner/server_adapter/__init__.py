import importlib
from importlib.metadata import entry_points
from typing import Callable


SERVER_ADAPTERS_ENTRY_POINT_GROUP = "openjiuwen.server_adapters"

_CUSTOM_SERVER_ADAPTERS: dict[str, Callable[..., object]] = {}

_OFFICIAL_SERVER_ADAPTER_BOOTSTRAP = {
    "A2A": "openjiuwen.extensions.a2a",
}


def register_server_adapter(name: str, factory: Callable[..., object]) -> None:
    _CUSTOM_SERVER_ADAPTERS[name] = factory


def _bootstrap_official_server_adapter(protocol: str) -> None:
    module_name = _OFFICIAL_SERVER_ADAPTER_BOOTSTRAP.get(protocol)
    if not module_name:
        return
    try:
        importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - optional plugin bootstrap should be best-effort
        return


def _resolve_entry_point(protocol: str, kwargs: dict) -> object | None:
    try:
        eps = entry_points(group=SERVER_ADAPTERS_ENTRY_POINT_GROUP)
    except Exception:
        return None

    for ep in eps:
        if ep.name != protocol:
            continue
        try:
            cls = ep.load()
        except Exception:
            return None
        try:
            return cls(**kwargs)
        except Exception:
            return None
    return None


def create_server_adapter(protocol: str, **kwargs) -> object | None:
    if protocol not in _CUSTOM_SERVER_ADAPTERS:
        _bootstrap_official_server_adapter(protocol)

    if protocol in _CUSTOM_SERVER_ADAPTERS:
        return _CUSTOM_SERVER_ADAPTERS[protocol](**kwargs)

    return _resolve_entry_point(protocol, kwargs)


__all__ = ["create_server_adapter", "register_server_adapter"]
