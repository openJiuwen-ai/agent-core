# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field

from openjiuwen.core.workflow.components.base import ComponentConfig, WorkflowComponentMetadata


class FTPProtocol(str, Enum):
    """FTP protocol types."""
    FTP = "ftp"  # Standard FTP (port 21)
    FTPS = "ftps"  # FTP over SSL/TLS (port 21)
    SFTP = "sftp"  # SSH File Transfer Protocol (port 22)


class FTPOperationType(str, Enum):
    """FTP operation types."""
    UPLOAD = "upload"  # Upload file to remote server
    DOWNLOAD = "download"  # Download file from remote server
    LIST = "list"  # List directory contents
    DELETE = "delete"  # Delete file or directory
    RENAME = "rename"  # Rename/move file or directory
    CREATE_DIRECTORY = "create_directory"  # Create directory
    DELETE_DIRECTORY = "delete_directory"  # Delete directory (recursive)
    GET_INFO = "get_info"  # Get file/directory metadata


class FTPCredentials(BaseModel):
    """FTP server credentials."""
    host: str = Field(..., description="FTP server hostname or IP address")
    port: int = Field(21, description="FTP server port (21 for FTP/FTPS, 22 for SFTP)")
    username: str = Field(..., description="FTP username")
    password: Optional[str] = Field(None, description="FTP password")
    private_key: Optional[str] = Field(None, description="SSH private key path for SFTP")
    private_key_passphrase: Optional[str] = Field(None, description="SSH private key passphrase for SFTP")
    protocol: FTPProtocol = Field(FTPProtocol.FTP, description="FTP protocol type")


class FTPSSLConfig(BaseModel):
    """SSL/TLS configuration for FTPS."""
    verify_ssl: bool = Field(True, description="Verify SSL certificate")
    ssl_version: Optional[str] = Field(None, description="SSL/TLS version (TLSv1.2, TLSv1.3, etc.)")
    cert_file: Optional[str] = Field(None, description="Client certificate file path")
    key_file: Optional[str] = Field(None, description="Client key file path")


class FTPConnectionConfig(BaseModel):
    """FTP connection configuration."""
    timeout: int = Field(30, description="Connection timeout in seconds")
    passive_mode: bool = Field(True, description="Use passive mode for FTP/FTPS")
    encoding: str = Field("utf-8", description="Character encoding")
    keepalive_interval: int = Field(30, description="Keepalive interval in seconds")
    max_retries: int = Field(3, description="Maximum number of retries on transient errors")
    retry_delay: float = Field(1.0, description="Initial retry delay in seconds (exponential backoff)")


class FTPUploadConfig(BaseModel):
    """Configuration for file upload operation."""
    local_path: Optional[str] = Field(None, description="Local file path to upload")
    remote_path: str = Field(..., description="Remote destination path")
    create_directories: bool = Field(True, description="Auto-create parent directories")
    overwrite: bool = Field(True, description="Overwrite existing file")
    binary_mode: bool = Field(True, description="Binary transfer mode (vs ASCII)")
    content: Optional[str] = Field(None, description="Direct content to upload (instead of file)")


class FTPDownloadConfig(BaseModel):
    """Configuration for file download operation."""
    remote_path: str = Field(..., description="Remote file path to download")
    local_path: Optional[str] = Field(None, description="Local destination path (None = return content)")
    binary_mode: bool = Field(True, description="Binary transfer mode (vs ASCII)")
    overwrite: bool = Field(True, description="Overwrite existing local file")


class FTPListConfig(BaseModel):
    """Configuration for directory listing operation."""
    remote_path: str = Field("/", description="Directory path to list")
    recursive: bool = Field(False, description="List recursively")
    pattern: Optional[str] = Field(None, description="Glob pattern filter (e.g., '*.txt')")
    include_hidden: bool = Field(False, description="Include hidden files (starting with .)")


class FTPDeleteConfig(BaseModel):
    """Configuration for file/directory deletion operation."""
    remote_path: str = Field(..., description="File or directory path to delete")
    recursive: bool = Field(False, description="Delete directory recursively")
    ignore_missing: bool = Field(False, description="Don't error if file/directory doesn't exist")


class FTPRenameConfig(BaseModel):
    """Configuration for rename/move operation."""
    old_path: str = Field(..., description="Current file/directory path")
    new_path: str = Field(..., description="New file/directory path")
    overwrite: bool = Field(False, description="Overwrite destination if exists")


class FTPCreateDirectoryConfig(BaseModel):
    """Configuration for directory creation operation."""
    remote_path: str = Field(..., description="Directory path to create")
    recursive: bool = Field(True, description="Create parent directories if needed")
    ignore_exists: bool = Field(True, description="Don't error if directory already exists")


class FTPGetInfoConfig(BaseModel):
    """Configuration for getting file/directory metadata."""
    remote_path: str = Field(..., description="File or directory path to get info for")


class FTPOperationConfig(BaseModel):
    """Configuration for a single FTP operation."""
    operation_type: FTPOperationType = Field(..., description="Type of FTP operation to perform")
    upload: Optional[FTPUploadConfig] = Field(None, description="Upload operation config")
    download: Optional[FTPDownloadConfig] = Field(None, description="Download operation config")
    list: Optional[FTPListConfig] = Field(None, description="List operation config")
    delete: Optional[FTPDeleteConfig] = Field(None, description="Delete operation config")
    rename: Optional[FTPRenameConfig] = Field(None, description="Rename operation config")
    create_directory: Optional[FTPCreateDirectoryConfig] = Field(None, description="Create directory config")
    get_info: Optional[FTPGetInfoConfig] = Field(None, description="Get info operation config")


class FTPComponentConfig(ComponentConfig):
    """Main configuration for FTP workflow component."""
    credentials: FTPCredentials = Field(..., description="FTP server credentials")
    connection: FTPConnectionConfig = Field(
        default_factory=FTPConnectionConfig,
        description="Connection configuration"
    )
    ssl_config: Optional[FTPSSLConfig] = Field(None, description="SSL/TLS configuration for FTPS")
    operations: List[FTPOperationConfig] = Field(
        default_factory=list,
        description="List of FTP operations to execute"
    )
    metadata: Optional[WorkflowComponentMetadata] = Field(None, description="Component metadata")
