# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
FTP Component Module

This module provides FTP/FTPS/SFTP file transfer capabilities for workflows.
Supports upload, download, directory listing, file operations, and more.
"""

from openjiuwen.core.workflow.components.tool.ftp.ftp_config import (
    FTPProtocol,
    FTPOperationType,
    FTPCredentials,
    FTPSSLConfig,
    FTPConnectionConfig,
    FTPUploadConfig,
    FTPDownloadConfig,
    FTPListConfig,
    FTPDeleteConfig,
    FTPRenameConfig,
    FTPCreateDirectoryConfig,
    FTPGetInfoConfig,
    FTPOperationConfig,
    FTPComponentConfig,
)

from openjiuwen.core.workflow.components.tool.ftp.ftp_component import (
    FTPComponent,
    FTPExecutable,
)

__all__ = [
    # Enums
    "FTPProtocol",
    "FTPOperationType",

    # Configuration models
    "FTPCredentials",
    "FTPSSLConfig",
    "FTPConnectionConfig",
    "FTPUploadConfig",
    "FTPDownloadConfig",
    "FTPListConfig",
    "FTPDeleteConfig",
    "FTPRenameConfig",
    "FTPCreateDirectoryConfig",
    "FTPGetInfoConfig",
    "FTPOperationConfig",
    "FTPComponentConfig",

    # Component classes
    "FTPComponent",
    "FTPExecutable",
]
