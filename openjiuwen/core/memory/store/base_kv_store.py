#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from typing import List


class BaseKVStore(ABC):
    """
    Abstract base class defining a unified interface for a key-value storage.
    """

    @abstractmethod
    async def set(self, key: str, value: str):
        """
        Store or overwrite a key-value pair.

        Args:
            key (str): The unique string identifier for the entry.
            value (str): The string payload to associate with the key.
        """
        pass

    @abstractmethod
    async def exclusive_set(self, key: str, value: str, expiry: int | None = None) -> bool:
        """
        Atomically set a key-value pair only if the key does not already exist.
        Args:
            key (str): the string key to set.
            value (str): The string value to associate with the key.
            expiry (int | None): Optional expiry time for the key-value pair.
        Returns:
            bool: True if the key-value pair was successfully set, False if the key already existed.
        """
        pass

    @abstractmethod
    async def get(self, key: str) -> str | None:
        """
        Retrieve the value associated with the given key.

        Args:
            key (str): The string key to look up.

        Returns:
            str | None: The stored string value, or None if the key is absent.
        """
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """
        Check whether a key exists in the store.

        Args:
            key (str): The string key to check.

        Returns:
            bool: True if the key exists, False otherwise.
        """
        pass

    @abstractmethod
    async def delete(self, key: str):
        """
        Remove the specified key from the store.

        Args:
            key (str): The string key to delete. No action is taken if the key does not exist.
        """
        pass

    @abstractmethod
    async def get_by_prefix(self, prefix: str) -> dict[str, str]:
        """
        Retrieve all key-value pairs whose keys start with the given prefix.

        Args:
            prefix (str): The string prefix to match against existing keys.

        Returns:
            dict[str, str]: A dictionary mapping every matching key to its corresponding value.
        """
        pass

    @abstractmethod
    async def delete_by_prefix(self, prefix: str):
        """
        Remove all key-value pairs whose keys start with the given prefix.

        Args:
            prefix (str): The string prefix to match against existing keys.
        """
        pass

    @abstractmethod
    async def mget(self, keys: List[str]) -> List[str | None]:
        """
        Bulk-retrieve values for multiple keys in a single operation.

        Args:
            keys (List[str]): An list of string keys to fetch.

        Returns:
            List[str | None]: A list of string values (or None) in the same order as the input ``keys``.
        """
        pass