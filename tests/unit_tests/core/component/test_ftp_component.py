#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Unit tests for FTP Component
"""

import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.workflow import (
    FTPComponent,
    FTPComponentConfig,
    FTPCredentials,
    FTPConnectionConfig,
    FTPSSLConfig,
    FTPOperationConfig,
    FTPOperationType,
    FTPProtocol,
    FTPUploadConfig,
    FTPDownloadConfig,
    FTPListConfig,
    FTPDeleteConfig,
    FTPRenameConfig,
    FTPCreateDirectoryConfig,
    FTPGetInfoConfig,
    Workflow,
    Start,
    End,
    WorkflowCard,
    create_workflow_session,
)

from openjiuwen.core.workflow.components.tool.ftp.ftp_service import FTPService

logger = logging.getLogger(__name__)


def test_component_creation():
    """Test that we can create the FTP component"""
    logger.info("Testing FTP Component creation...")

    # Create a basic configuration
    config = FTPComponentConfig(
        credentials=FTPCredentials(
            host="ftp.example.com",
            port=21,
            username="testuser",
            password="testpass",
            protocol=FTPProtocol.FTP,
        ),
        connection=FTPConnectionConfig(
            timeout=30,
            passive_mode=True,
        ),
        operations=[
            FTPOperationConfig(
                operation_type=FTPOperationType.LIST,
                list=FTPListConfig(
                    remote_path="/",
                ),
            ),
        ],
    )

    # Create the component
    component = FTPComponent(config=config)

    logger.info("✓ FTP Component created successfully!")
    logger.info(f"Component type: {type(component)}")
    logger.info(f"Config host: {component.config.credentials.host}")

    assert component is not None
    assert component.config.credentials.host == "ftp.example.com"
    assert component.config.credentials.protocol == FTPProtocol.FTP


def test_ftps_component_creation():
    """Test FTPS component creation with SSL config"""
    logger.info("Testing FTPS Component creation...")

    config = FTPComponentConfig(
        credentials=FTPCredentials(
            host="ftps.example.com",
            port=21,
            username="testuser",
            password="testpass",
            protocol=FTPProtocol.FTPS,
        ),
        ssl_config=FTPSSLConfig(
            verify_ssl=True,
            ssl_version="TLSv1.2",
        ),
        operations=[],
    )

    component = FTPComponent(config=config)

    assert component is not None
    assert component.config.credentials.protocol == FTPProtocol.FTPS
    assert component.config.ssl_config.verify_ssl is True


def test_sftp_component_creation():
    """Test SFTP component creation with SSH key"""
    logger.info("Testing SFTP Component creation...")

    config = FTPComponentConfig(
        credentials=FTPCredentials(
            host="sftp.example.com",
            port=22,
            username="testuser",
            private_key="/path/to/key",
            private_key_passphrase="keypass",
            protocol=FTPProtocol.SFTP,
        ),
        operations=[],
    )

    component = FTPComponent(config=config)

    assert component is not None
    assert component.config.credentials.protocol == FTPProtocol.SFTP
    assert component.config.credentials.private_key == "/path/to/key"


@pytest.mark.asyncio
async def test_executable_creation():
    """Test that we can create the executable"""
    logger.info("Testing executable creation...")

    config = FTPComponentConfig(
        credentials=FTPCredentials(
            host="ftp.example.com",
            username="testuser",
            password="testpass",
        ),
        operations=[],
    )

    component = FTPComponent(config=config)
    executable = component.executable

    logger.info("✓ Executable created successfully!")
    logger.info(f"Executable type: {type(executable)}")

    assert executable is not None


def test_multiple_operations_config():
    """Test component with multiple operations"""
    logger.info("Testing component with multiple operations...")

    config = FTPComponentConfig(
        credentials=FTPCredentials(
            host="ftp.example.com",
            username="testuser",
            password="testpass",
        ),
        operations=[
            FTPOperationConfig(
                operation_type=FTPOperationType.LIST,
                list=FTPListConfig(remote_path="/data"),
            ),
            FTPOperationConfig(
                operation_type=FTPOperationType.UPLOAD,
                upload=FTPUploadConfig(
                    local_path="/local/file.txt",
                    remote_path="/remote/file.txt",
                ),
            ),
            FTPOperationConfig(
                operation_type=FTPOperationType.DOWNLOAD,
                download=FTPDownloadConfig(
                    remote_path="/remote/data.csv",
                    local_path="/local/data.csv",
                ),
            ),
        ],
    )

    component = FTPComponent(config=config)

    assert len(component.config.operations) == 3
    assert component.config.operations[0].operation_type == FTPOperationType.LIST
    assert component.config.operations[1].operation_type == FTPOperationType.UPLOAD
    assert component.config.operations[2].operation_type == FTPOperationType.DOWNLOAD


@pytest.mark.asyncio
async def test_upload_operation_with_mock():
    """Test upload operation with mocked FTP service"""
    logger.info("Testing upload operation with mock...")

    # Create config
    config = FTPComponentConfig(
        credentials=FTPCredentials(
            host="ftp.example.com",
            username="testuser",
            password="testpass",
        ),
        operations=[
            FTPOperationConfig(
                operation_type=FTPOperationType.UPLOAD,
                upload=FTPUploadConfig(
                    local_path="/local/test.txt",
                    remote_path="/remote/test.txt",
                ),
            ),
        ],
    )

    component = FTPComponent(config=config)
    executable = component.executable

    # Mock FTPService
    mock_service = AsyncMock(spec=FTPService)
    mock_service.execute_operation.return_value = {
        "ok": True,
        "operation": "upload",
        "data": {
            "bytes_transferred": 1024,
            "duration_ms": 500,
        },
        "error": None,
    }

    # Patch the service
    with patch.object(executable.__class__, '_ensure_ftp_service', return_value=mock_service):
        # Create mock session and context
        mock_session = MagicMock()
        mock_context = MagicMock()

        # Execute
        result = await executable.invoke({}, mock_session, mock_context)

        # Verify
        assert result["ok"] is True
        assert len(result["operations"]) == 1
        assert result["operations"][0]["operation"] == "upload"
        assert result["operations"][0]["data"]["bytes_transferred"] == 1024


@pytest.mark.asyncio
async def test_download_operation_with_mock():
    """Test download operation with mocked FTP service"""
    logger.info("Testing download operation with mock...")

    config = FTPComponentConfig(
        credentials=FTPCredentials(
            host="ftp.example.com",
            username="testuser",
            password="testpass",
        ),
        operations=[
            FTPOperationConfig(
                operation_type=FTPOperationType.DOWNLOAD,
                download=FTPDownloadConfig(
                    remote_path="/remote/file.txt",
                    local_path="/local/file.txt",
                ),
            ),
        ],
    )

    component = FTPComponent(config=config)
    executable = component.executable

    # Mock FTPService
    mock_service = AsyncMock(spec=FTPService)
    mock_service.execute_operation.return_value = {
        "ok": True,
        "operation": "download",
        "data": {
            "bytes_transferred": 2048,
            "local_path": "/local/file.txt",
            "content": None,
        },
        "error": None,
    }

    with patch.object(executable.__class__, '_ensure_ftp_service', return_value=mock_service):
        mock_session = MagicMock()
        mock_context = MagicMock()

        result = await executable.invoke({}, mock_session, mock_context)

        assert result["ok"] is True
        assert result["operations"][0]["operation"] == "download"
        assert result["operations"][0]["data"]["bytes_transferred"] == 2048


@pytest.mark.asyncio
async def test_list_operation_with_mock():
    """Test list directory operation with mocked FTP service"""
    logger.info("Testing list operation with mock...")

    config = FTPComponentConfig(
        credentials=FTPCredentials(
            host="ftp.example.com",
            username="testuser",
            password="testpass",
        ),
        operations=[
            FTPOperationConfig(
                operation_type=FTPOperationType.LIST,
                list=FTPListConfig(
                    remote_path="/data",
                    pattern="*.txt",
                ),
            ),
        ],
    )

    component = FTPComponent(config=config)
    executable = component.executable

    # Mock FTPService
    mock_service = AsyncMock(spec=FTPService)
    mock_service.execute_operation.return_value = {
        "ok": True,
        "operation": "list",
        "data": {
            "items": [
                {
                    "name": "file1.txt",
                    "type": "file",
                    "size": 1024,
                    "modified": "2026-04-01T10:00:00",
                },
                {
                    "name": "file2.txt",
                    "type": "file",
                    "size": 2048,
                    "modified": "2026-04-02T11:00:00",
                },
            ]
        },
        "error": None,
    }

    with patch.object(executable.__class__, '_ensure_ftp_service', return_value=mock_service):
        mock_session = MagicMock()
        mock_context = MagicMock()

        result = await executable.invoke({}, mock_session, mock_context)

        assert result["ok"] is True
        assert len(result["operations"][0]["data"]["items"]) == 2
        assert result["operations"][0]["data"]["items"][0]["name"] == "file1.txt"


@pytest.mark.asyncio
async def test_stream_execution():
    """Test stream execution yields individual results"""
    logger.info("Testing stream execution...")

    config = FTPComponentConfig(
        credentials=FTPCredentials(
            host="ftp.example.com",
            username="testuser",
            password="testpass",
        ),
        operations=[
            FTPOperationConfig(
                operation_type=FTPOperationType.LIST,
                list=FTPListConfig(remote_path="/"),
            ),
            FTPOperationConfig(
                operation_type=FTPOperationType.UPLOAD,
                upload=FTPUploadConfig(
                    local_path="/local/test.txt",
                    remote_path="/remote/test.txt",
                ),
            ),
        ],
    )

    component = FTPComponent(config=config)
    executable = component.executable

    # Mock FTPService
    mock_service = AsyncMock(spec=FTPService)
    mock_service.execute_operation.side_effect = [
        {"ok": True, "operation": "list", "data": {"items": []}, "error": None},
        {"ok": True, "operation": "upload", "data": {"bytes_transferred": 100}, "error": None},
    ]

    with patch.object(executable.__class__, '_ensure_ftp_service', return_value=mock_service):
        mock_session = MagicMock()
        mock_context = MagicMock()

        results = []
        async for result in executable.stream({}, mock_session, mock_context):
            results.append(result)

        assert len(results) == 2
        assert results[0]["operation"] == "list"
        assert results[1]["operation"] == "upload"


@pytest.mark.asyncio
async def test_error_handling():
    """Test error handling when operation fails"""
    logger.info("Testing error handling...")

    config = FTPComponentConfig(
        credentials=FTPCredentials(
            host="ftp.example.com",
            username="testuser",
            password="testpass",
        ),
        operations=[
            FTPOperationConfig(
                operation_type=FTPOperationType.UPLOAD,
                upload=FTPUploadConfig(
                    local_path="/nonexistent/file.txt",
                    remote_path="/remote/file.txt",
                ),
            ),
        ],
    )

    component = FTPComponent(config=config)
    executable = component.executable

    # Mock FTPService with error
    mock_service = AsyncMock(spec=FTPService)
    mock_service.execute_operation.return_value = {
        "ok": False,
        "operation": "upload",
        "data": None,
        "error": "File not found: /nonexistent/file.txt",
    }

    with patch.object(executable.__class__, '_ensure_ftp_service', return_value=mock_service):
        mock_session = MagicMock()
        mock_context = MagicMock()

        result = await executable.invoke({}, mock_session, mock_context)

        assert result["ok"] is False
        assert result["operations"][0]["ok"] is False
        assert "File not found" in result["operations"][0]["error"]


def test_all_operation_types():
    """Test that all operation types can be configured"""
    logger.info("Testing all operation types...")

    operations = [
        FTPOperationConfig(
            operation_type=FTPOperationType.UPLOAD,
            upload=FTPUploadConfig(local_path="/local", remote_path="/remote"),
        ),
        FTPOperationConfig(
            operation_type=FTPOperationType.DOWNLOAD,
            download=FTPDownloadConfig(remote_path="/remote", local_path="/local"),
        ),
        FTPOperationConfig(
            operation_type=FTPOperationType.LIST,
            list=FTPListConfig(remote_path="/"),
        ),
        FTPOperationConfig(
            operation_type=FTPOperationType.DELETE,
            delete=FTPDeleteConfig(remote_path="/file"),
        ),
        FTPOperationConfig(
            operation_type=FTPOperationType.RENAME,
            rename=FTPRenameConfig(old_path="/old", new_path="/new"),
        ),
        FTPOperationConfig(
            operation_type=FTPOperationType.CREATE_DIRECTORY,
            create_directory=FTPCreateDirectoryConfig(remote_path="/newdir"),
        ),
        FTPOperationConfig(
            operation_type=FTPOperationType.GET_INFO,
            get_info=FTPGetInfoConfig(remote_path="/file"),
        ),
    ]

    config = FTPComponentConfig(
        credentials=FTPCredentials(
            host="ftp.example.com",
            username="testuser",
            password="testpass",
        ),
        operations=operations,
    )

    component = FTPComponent(config=config)

    assert len(component.config.operations) == 7
    operation_types = [op.operation_type for op in component.config.operations]
    assert FTPOperationType.UPLOAD in operation_types
    assert FTPOperationType.DOWNLOAD in operation_types
    assert FTPOperationType.LIST in operation_types
    assert FTPOperationType.DELETE in operation_types
    assert FTPOperationType.RENAME in operation_types
    assert FTPOperationType.CREATE_DIRECTORY in operation_types
    assert FTPOperationType.GET_INFO in operation_types


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
