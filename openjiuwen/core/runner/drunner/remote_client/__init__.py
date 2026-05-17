import importlib
from importlib.metadata import entry_points
from typing import Callable

from openjiuwen.core.runner.drunner.remote_client.remote_client import RemoteClient
from openjiuwen.core.runner.drunner.remote_client.remote_client_config import ProtocolEnum


REMOTE_CLIENTS_ENTRY_POINT_GROUP = "openjiuwen.remote_clients"

_BUILTIN_REMOTE_CLIENT_NAMES = frozenset({"MQ"})

_CUSTOM_REMOTE_CLIENTS: dict[str, Callable[..., "RemoteClient"]] = {}

_OFFICIAL_REMOTE_CLIENT_BOOTSTRAP = {
    ProtocolEnum.A2A: "openjiuwen.extensions.a2a",
}


def register_remote_client(
    name: str, factory: Callable[..., "RemoteClient"]
) -> None:
    _CUSTOM_REMOTE_CLIENTS[name] = factory


def _bootstrap_official_remote_client(protocol: str) -> None:
    module_name = _OFFICIAL_REMOTE_CLIENT_BOOTSTRAP.get(protocol)
    if not module_name:
        return
    try:
        importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - optional plugin bootstrap should be best-effort
        return


def _resolve_builtin(protocol: str, kwargs: dict) -> "RemoteClient | None":
    if protocol == ProtocolEnum.MQ:
        from openjiuwen.core.runner.drunner.remote_client.mq_remote_clent import MqRemoteClient
        client = MqRemoteClient(**kwargs)
        return client
    return None


def _resolve_entry_point(protocol: str, kwargs: dict) -> "RemoteClient | None":
    try:
        eps = entry_points(group=REMOTE_CLIENTS_ENTRY_POINT_GROUP)
    except Exception as e:  # noqa: BLE001 — stdlib may raise on broken metadata
        return None

    for ep in eps:
        if ep.name != protocol:
            continue
        try:
            cls = ep.load()
        except Exception as e:  # noqa: BLE001 — any plugin import failure
            return None
        try:
            return cls(**kwargs)
        except Exception as e:  # noqa: BLE001 — plugin constructor failed
            return None
    return None


def create_remote_client(protocol: str, **kwargs) -> "RemoteClient | None":
    if protocol in _BUILTIN_REMOTE_CLIENT_NAMES:
        return _resolve_builtin(protocol, kwargs)

    if protocol not in _CUSTOM_REMOTE_CLIENTS:
        _bootstrap_official_remote_client(protocol)

    if protocol in _CUSTOM_REMOTE_CLIENTS:
        return _CUSTOM_REMOTE_CLIENTS[protocol](**kwargs)

    return _resolve_entry_point(protocol, kwargs)
