# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
from unittest.mock import AsyncMock, patch

import pytest
from botocore.exceptions import ClientError

from openjiuwen.core.foundation.store.object.aioboto_storage_client import AioBotoClient


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("OBS_ACCESS_KEY_ID", "test-ak")
    monkeypatch.setenv("OBS_SECRET_ACCESS_KEY", "test-sk")
    monkeypatch.setenv("OBS_SERVER", "https://obs.r.com")
    monkeypatch.setenv("OBS_BUCKET", "test-bucket")
    monkeypatch.setenv("OBS_REGION", "test-region")


@pytest.fixture
def mock_s3_client():
    s3 = AsyncMock()
    s3.__aenter__.return_value = s3
    s3.__aexit__.return_value = None
    return s3


@pytest.fixture
def client(mock_s3_client, mock_env):
    with patch.object(AioBotoClient, "create_client", return_value=mock_s3_client):
        yield AioBotoClient()


@pytest.mark.asyncio
async def test_create_bucket_success(client, mock_s3_client):
    await client.create_bucket("test-bucket", "ap-southeast-1")

    mock_s3_client.create_bucket.assert_awaited_once_with(
        Bucket="test-bucket",
        CreateBucketConfiguration={"LocationConstraint": "ap-southeast-1"},
    )


@pytest.mark.asyncio
async def test_create_bucket_error(client, mock_s3_client, caplog):
    mock_s3_client.create_bucket.side_effect = ClientError(
        {"Error": {"Code": "Error", "Message": "create failed"}},
        "CreateBucket",
    )

    with caplog.at_level("ERROR"):
        await client.create_bucket(
            bucket_name="test-bucket",
            location="ap-southeast-1",
        )

    # create was attempted
    mock_s3_client.create_bucket.assert_awaited_once_with(
        Bucket="test-bucket",
        CreateBucketConfiguration={"LocationConstraint": "ap-southeast-1"},
    )

    # error was logged
    assert any(
        'Create Bucket "test-bucket" failed' in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_delete_bucket_success(client, mock_s3_client):
    await client.delete_bucket("test-bucket")

    mock_s3_client.delete_bucket.assert_awaited_once_with(Bucket="test-bucket")


@pytest.mark.asyncio
async def test_delete_bucket_error(client, mock_s3_client, caplog):
    mock_s3_client.delete_bucket.side_effect = ClientError(
        {"Error": {"Code": "Error", "Message": "delete failed"}},
        "DeleteBucket",
    )

    with caplog.at_level("ERROR"):
        await client.delete_bucket("test-bucket")

    # delete was attempted
    mock_s3_client.delete_bucket.assert_awaited_once_with(Bucket="test-bucket")

    # error was logged
    assert any(
        'Delete Bucket "test-bucket" failed' in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_upload_file_success(client, mock_s3_client, tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("hello")

    await client.upload_file(
        bucket_name="bucket",
        object_name="obj",
        file_path=file_path,
    )

    mock_s3_client.upload_fileobj.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_file_error(client, mock_s3_client, tmp_path, caplog):
    file_path = tmp_path / "test.txt"
    file_path.write_text("hello")

    mock_s3_client.upload_fileobj.side_effect = ClientError(
        {"Error": {"Code": "Error", "Message": "upload failed"}},
        "UploadFile",
    )

    with caplog.at_level("ERROR"):
        await client.upload_file(
            bucket_name="bucket",
            object_name="obj",
            file_path=file_path,
        )

    # upload was attempted
    mock_s3_client.upload_fileobj.assert_awaited_once()

    # error was logged
    assert any('Upload "obj" failed' in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_download_file_success(client, mock_s3_client, tmp_path):
    file_path = tmp_path / "out.txt"

    # Mock download_fileobj to write data into Fileobj
    async def mock_download_fileobj(*, Bucket, Key, Fileobj):
        Fileobj.write(b"hello world")

    mock_s3_client.download_fileobj.side_effect = mock_download_fileobj

    await client.download_file(
        bucket_name="bucket",
        object_name="obj",
        file_path=file_path,
    )

    mock_s3_client.download_fileobj.assert_awaited_once()

    assert os.path.isfile(file_path)
    assert os.path.getsize(file_path) > 0


@pytest.mark.asyncio
async def test_delete_object_error(client, mock_s3_client, caplog):
    mock_s3_client.delete_object.side_effect = ClientError(
        {"Error": {"Code": "Error", "Message": "delete object failed"}},
        "DeleteObject",
    )

    with caplog.at_level("ERROR"):
        await client.delete_object(
            bucket_name="bucket",
            object_name="obj",
        )

    # delete was attempted
    mock_s3_client.delete_object.assert_awaited_once_with(
        Bucket="bucket",
        Key="obj",
    )

    # error was logged
    assert any(
        'Delete file "obj" failed' in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def er(client, mock_s3_client):
    await client.delete_object("bucket", "obj")

    mock_s3_client.delete_object.assert_awaited_once_with(
        Bucket="bucket",
        Key="obj",
    )


@pytest.mark.asyncio
async def test_delete_object_error(client, mock_s3_client, caplog):
    mock_s3_client.delete_object.side_effect = ClientError(
        {"Error": {"Code": "Error", "Message": "delete failed"}},
        "DeleteObject",
    )

    with caplog.at_level("ERROR"):
        await client.delete_object(
            bucket_name="bucket",
            object_name="obj",
        )

    # delete was attempted
    mock_s3_client.delete_object.assert_awaited_once_with(
        Bucket="bucket",
        Key="obj",
    )

    # error was logged
    assert any(
        'Delete file "obj" failed' in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_list_objects_success(client, mock_s3_client):
    mock_s3_client.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "a.txt"},
            {"Key": "b.txt"},
        ]
    }

    result = await client.list_objects("bucket", "prefix")

    assert result == [{"Key": "a.txt"}, {"Key": "b.txt"}]

    mock_s3_client.list_objects_v2.assert_awaited_once_with(
        Bucket="bucket",
        Prefix="prefix",
        MaxKeys=100,
    )


@pytest.mark.asyncio
async def test_list_objects_error(client, mock_s3_client):
    mock_s3_client.list_objects_v2.side_effect = ClientError(
        {"Error": {"Code": "Error", "Message": "list failed"}},
        "ListObjectsV2",
    )

    result = await client.list_objects("bucket", "prefix")

    assert result is None
