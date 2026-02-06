# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from pathlib import Path
from abc import abstractmethod, ABC


class BaseObjectStorageClient(ABC):
    """
    Base class for Object Storage client.

    This class provides the interface for basic bucket and object operations such as
    creating buckets, uploading/downloading files, listing objects, and deleting objects.
    """

    @abstractmethod
    async def upload_file(self, bucket_name, object_name, file_path):
        """
        Upload a local file to an object storage bucket.

        :param bucket_name: Name of the target bucket
        :param object_name: Object key (path/name)
        :param file_path: Local file path to upload
        """
        raise NotImplementedError()

    @abstractmethod
    async def download_file(
        self, bucket_name: str, object_name: str, file_path: str | Path
    ):
        """
        Download an object from Object Storage server

        :param bucket_name: Name of the bucket
        :param object_name: Object key to download
        :param file_path: Local file path where the object will be saved
        """
        raise NotImplementedError()

    @abstractmethod
    async def delete_object(self, bucket_name: str, object_name: str):
        """
        Delete an object from an object storage bucket.

        :param bucket_name: Name of the bucket
        :param object_name: Object key to delete
        """
        raise NotImplementedError()

    @abstractmethod
    async def create_bucket(self, bucket_name: str, location: str):
        """
        Create a new object storage bucket.

        :param bucket_name: Name of the bucket to be created
        :param location: Region/location where the bucket will be created
        """
        raise NotImplementedError()

    @abstractmethod
    async def delete_bucket(self, bucket_name: str):
        """
        Deletes an existing object storage bucket.

        :param bucket_name: Name of the bucket to be created
        """
        raise NotImplementedError()

    @abstractmethod
    async def list_objects(
        self, bucket_name: str, object_prefix: str, max_objects: int = 100
    ) -> list[dict] | None:
        """
        List objects in an object storage bucket with a given prefix.

        :param bucket_name: Name of the bucket
        :param object_prefix: Prefix to filter objects listed
        :param max_objects: Maximum number of objects to be listed at a time.
        :return: List of dict objects if successful, otherwise None
        """
        raise NotImplementedError()
