# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph Store Factory

Factory class for creating and managing graph store backend instances
"""

from threading import Lock
from typing import Dict, Optional, Type

from openjiuwen.core.common.logging import logger

from .config import GraphConfig
from .graph_backend import GraphStore


class GraphStoreFactory:
    """Factory class to assemble graph store instances"""

    class_map: Dict[str, Type[GraphStore]] = dict()
    __thread_lock: Lock = Lock()

    def __init__(self, *args, **kwargs):
        raise RuntimeError(f"Please do not instantiate {self.__class__.__name__}")

    @classmethod
    def register_backend(cls, name: str, backend: Type[GraphStore], force: bool = False):
        """Register graph store backend for storing / representing graph.

        Args:
            name (str): Name for the new graph store backend.
            backend (Type[GraphBackend]): Class for the new graph store backend.
            force (bool, optional): Whether to force register. Defaults to False.

        Raises:
            KeyError: 1) name is empty or 2) name already registered and force=False.
            NotImplementedError: backend did not implement the GraphStore Protocol.
        """
        with cls.__thread_lock:
            if not name:
                raise KeyError("Backend name cannot be registered as an empty value.")
            if name in cls.class_map and not force:
                raise KeyError(f"Entry [{name}] -> {cls.class_map[name]} already exists.")
            if not isinstance(backend, GraphStore):
                err_msg = f"{name} did not implement GraphStore Protocol!"
                if not force:
                    raise NotImplementedError(err_msg)
                logger.warning(err_msg)
            cls.class_map[name] = backend
            logger.info("Graph Store registered: %s", name)

    @classmethod
    def from_config(cls, config: GraphConfig, backend_name: Optional[str] = None, **kwargs) -> GraphStore:
        """Fetch a GraphStore instance by configuration file.

        Args:
            config (GraphConfig): Database configuration.
            backend_name (Optional[str], optional): If not None, overwrites database backend choice in config. \
                Defaults to None.

        Raises:
            KeyError: The database backend choice has not been registered in GraphStoreFactory.

        Returns:
            GraphStore: instance of graph store.
        """
        with cls.__thread_lock:
            name = backend_name or config.backend
            backend_cls = cls.class_map.get(name)
            if backend_cls:
                return backend_cls.from_config(config, **kwargs)
            raise KeyError(f"Backend type [{name}] does not exist.")
