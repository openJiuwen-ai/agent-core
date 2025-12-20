#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from typing import List, Tuple


class BaseSemanticStore(ABC):
    """
    Abstract base class defining a unified interface for semantic storage.

    This class defines the interface for storing, deleting, and searching vector embeddings in a table-based structure.
    Concrete implementations must handle the embedding generation internally during the `add_docs` and `search` method.
    """

    @abstractmethod
    async def add_docs(self, docs: List[Tuple[str, str]], table_name: str) -> bool:
        """
        Add documents to a specified table after generating their embeddings.

        Args:
            docs (List[Tuple[str, str]]): A list of (id, text) tuples where id is a unique identifier
                and text is the raw string to be embedded.
            table_name (str): The name of the table where the embeddings will be stored.

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        pass

    @abstractmethod
    async def delete_docs(self, ids: List[str], table_name: str) -> bool:
        """
        Delete documents from a specified table by their ids.

        Args:
            ids (List[str]): A list of unique document ids whose embeddings should be removed.
            table_name (str): The name of the table from which to delete embeddings.

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        pass

    @abstractmethod
    async def search(self, query: str, table_name: str, top_k: int) -> List[Tuple[str, float]]:
        """
        Search for the top-k most similar documents.

        The query string is embedded internally before similarity comparison.

        Args:
            query (str): The raw query string to embed and search for.
            table_name (str): The name of the table to search within.
            top_k (int): The number of most similar results to return.

        Returns:
            List[Tuple[str, float]]: A list of (id, score) tuples where `id`
                is the unique identifier of the matched document and `score`
                is the similarity score, with higher values indicating greater similarity.
        """
        pass

    @abstractmethod
    async def delete_table(self, table_name: str) -> bool:
        """
        Delete an entire table and all its stored embeddings.

        Args:
            table_name (str): The name of the table to delete.

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        pass