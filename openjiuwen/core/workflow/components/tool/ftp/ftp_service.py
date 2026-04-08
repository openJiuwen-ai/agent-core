# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import io
import ssl
import fnmatch
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime

from loguru import logger

from openjiuwen.core.workflow.components.tool.ftp.ftp_config import (
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
)


class FTPError(Exception):
    """Base exception for FTP errors."""
    pass


class FTPTransientError(FTPError):
    """Transient FTP error that can be retried."""
    pass


class FTPPermanentError(FTPError):
    """Permanent FTP error that should not be retried."""
    pass


class FTPClientBase(ABC):
    """Abstract base class for FTP protocol clients."""

    def __init__(self):
        self._connected = False

    @abstractmethod
    async def connect(
        self,
        credentials: FTPCredentials,
        connection_config: FTPConnectionConfig,
        ssl_config: Optional[FTPSSLConfig] = None
    ):
        """Establish connection to FTP server."""
        pass

    @abstractmethod
    async def disconnect(self):
        """Close connection to FTP server."""
        pass

    @abstractmethod
    async def upload(
        self,
        local_path: Optional[str],
        remote_path: str,
        config: FTPUploadConfig
    ) -> Dict[str, Any]:
        """Upload file to server. Returns dict with bytes_transferred and duration_ms."""
        pass

    @abstractmethod
    async def download(
        self,
        remote_path: str,
        local_path: Optional[str],
        config: FTPDownloadConfig
    ) -> Dict[str, Any]:
        """Download file from server. Returns dict with bytes_transferred, content (optional), local_path (optional)."""
        pass

    @abstractmethod
    async def list_directory(
        self,
        remote_path: str,
        config: FTPListConfig
    ) -> List[Dict[str, Any]]:
        """List directory contents. Returns list of file/directory info dicts."""
        pass

    @abstractmethod
    async def delete(
        self,
        remote_path: str,
        config: FTPDeleteConfig
    ) -> bool:
        """Delete file or directory. Returns True if deleted."""
        pass

    @abstractmethod
    async def rename(
        self,
        old_path: str,
        new_path: str,
        config: FTPRenameConfig
    ) -> Dict[str, str]:
        """Rename/move file or directory. Returns dict with old_path and new_path."""
        pass

    @abstractmethod
    async def create_directory(
        self,
        remote_path: str,
        config: FTPCreateDirectoryConfig
    ) -> Dict[str, Any]:
        """Create directory. Returns dict with created status and path."""
        pass

    @abstractmethod
    async def get_info(
        self,
        remote_path: str
    ) -> Dict[str, Any]:
        """Get file/directory metadata. Returns dict with type, size, modified, permissions."""
        pass

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected


class FTPClient(FTPClientBase):
    """FTP/FTPS client implementation using aioftp."""

    def __init__(self):
        super().__init__()
        self._client = None
        self._context = None

    async def connect(
        self,
        credentials: FTPCredentials,
        connection_config: FTPConnectionConfig,
        ssl_config: Optional[FTPSSLConfig] = None
    ):
        """Establish connection to FTP/FTPS server."""
        try:
            import aioftp

            # Create SSL context for FTPS
            ssl_context = None
            if credentials.protocol == FTPProtocol.FTPS:
                ssl_context = ssl.create_default_context()
                if ssl_config:
                    if not ssl_config.verify_ssl:
                        ssl_context.check_hostname = False
                        ssl_context.verify_mode = ssl.CERT_NONE
                    if ssl_config.cert_file and ssl_config.key_file:
                        ssl_context.load_cert_chain(ssl_config.cert_file, ssl_config.key_file)

            # Create client context
            self._context = aioftp.Client.context(
                host=credentials.host,
                port=credentials.port,
                user=credentials.username,
                password=credentials.password or "",
                ssl=ssl_context,
                socket_timeout=connection_config.timeout,
                encoding=connection_config.encoding,
            )

            # Enter context to establish connection
            self._client = await self._context.__aenter__()
            self._connected = True

            logger.debug(f"Connected to {credentials.protocol.value}://{credentials.host}:{credentials.port}")

        except Exception as e:
            logger.error(f"Failed to connect to FTP server: {e}")
            raise FTPPermanentError(f"Connection failed: {e}") from e

    async def disconnect(self):
        """Close connection to FTP/FTPS server."""
        if self._context and self._client:
            try:
                await self._context.__aexit__(None, None, None)
                self._connected = False
                logger.debug("Disconnected from FTP server")
            except Exception as e:
                logger.warning(f"Error during disconnect: {e}")

    async def upload(
        self,
        local_path: Optional[str],
        remote_path: str,
        config: FTPUploadConfig
    ) -> Dict[str, Any]:
        """Upload file to FTP server."""
        start_time = asyncio.get_event_loop().time()
        bytes_transferred = 0

        try:
            # Create parent directories if needed
            if config.create_directories:
                parent_path = str(Path(remote_path).parent)
                if parent_path != "/" and parent_path != ".":
                    await self._ensure_directory(parent_path)

            # Upload from content or file
            if config.content:
                # Upload from string content
                content_bytes = config.content.encode('utf-8')
                bytes_transferred = len(content_bytes)
                async with self._client.upload_stream(remote_path) as stream:
                    await stream.write(content_bytes)
            else:
                # Upload from file
                if not local_path:
                    raise FTPPermanentError("Either local_path or content must be provided for upload")
                await self._client.upload(
                    local_path,
                    remote_path,
                    write_into=not config.overwrite,
                )
                bytes_transferred = Path(local_path).stat().st_size

            duration_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)

            return {
                "bytes_transferred": bytes_transferred,
                "duration_ms": duration_ms,
            }

        except Exception as e:
            logger.error(f"Upload failed for {remote_path}: {e}")
            raise FTPTransientError(f"Upload failed: {e}") from e

    async def download(
        self,
        remote_path: str,
        local_path: Optional[str],
        config: FTPDownloadConfig
    ) -> Dict[str, Any]:
        """Download file from FTP server."""
        bytes_transferred = 0
        content = None

        try:
            if local_path:
                # Download to file
                await self._client.download(
                    remote_path,
                    local_path,
                    write_into=not config.overwrite,
                )
                bytes_transferred = Path(local_path).stat().st_size
            else:
                # Download to memory
                async with self._client.download_stream(remote_path) as stream:
                    content = await stream.read()
                    bytes_transferred = len(content)

            return {
                "bytes_transferred": bytes_transferred,
                "content": content,
                "local_path": local_path,
            }

        except Exception as e:
            logger.error(f"Download failed for {remote_path}: {e}")
            raise FTPTransientError(f"Download failed: {e}") from e

    async def list_directory(
        self,
        remote_path: str,
        config: FTPListConfig
    ) -> List[Dict[str, Any]]:
        """List directory contents."""
        items = []

        try:
            async for path, info in self._client.list(remote_path, recursive=config.recursive):
                # Get file info
                name = str(path)
                is_dir = info["type"] == "dir"

                # Apply filters
                if not config.include_hidden and name.startswith("."):
                    continue
                if config.pattern and not fnmatch.fnmatch(name, config.pattern):
                    continue

                # Parse modification time
                modified_str = info.get("modify", "")
                try:
                    modified = datetime.strptime(modified_str, "%Y%m%d%H%M%S").isoformat()
                except (ValueError, TypeError):
                    modified = modified_str

                items.append({
                    "name": name,
                    "type": "directory" if is_dir else "file",
                    "size": int(info.get("size", 0)),
                    "modified": modified,
                    "path": str(Path(remote_path) / name),
                })

            return items

        except Exception as e:
            logger.error(f"List directory failed for {remote_path}: {e}")
            raise FTPTransientError(f"List failed: {e}") from e

    async def delete(
        self,
        remote_path: str,
        config: FTPDeleteConfig
    ) -> bool:
        """Delete file or directory."""
        try:
            # Check if it exists
            exists = await self._path_exists(remote_path)
            if not exists:
                if config.ignore_missing:
                    return False
                raise FTPPermanentError(f"Path not found: {remote_path}")

            # Check if it's a directory
            is_dir = await self._is_directory(remote_path)

            if is_dir:
                if config.recursive:
                    await self._client.remove(remote_path)
                else:
                    await self._client.rmdir(remote_path)
            else:
                await self._client.remove(remote_path)

            return True

        except FTPPermanentError:
            raise
        except Exception as e:
            logger.error(f"Delete failed for {remote_path}: {e}")
            raise FTPTransientError(f"Delete failed: {e}") from e

    async def rename(
        self,
        old_path: str,
        new_path: str,
        config: FTPRenameConfig
    ) -> Dict[str, str]:
        """Rename/move file or directory."""
        try:
            # Check if destination exists
            if not config.overwrite and await self._path_exists(new_path):
                raise FTPPermanentError(f"Destination already exists: {new_path}")

            await self._client.rename(old_path, new_path)

            return {
                "old_path": old_path,
                "new_path": new_path,
            }

        except FTPPermanentError:
            raise
        except Exception as e:
            logger.error(f"Rename failed from {old_path} to {new_path}: {e}")
            raise FTPTransientError(f"Rename failed: {e}") from e

    async def create_directory(
        self,
        remote_path: str,
        config: FTPCreateDirectoryConfig
    ) -> Dict[str, Any]:
        """Create directory."""
        try:
            if config.recursive:
                await self._ensure_directory(remote_path)
            else:
                await self._client.make_directory(remote_path)

            return {
                "created": True,
                "path": remote_path,
            }

        except Exception as e:
            if config.ignore_exists and "exists" in str(e).lower():
                return {
                    "created": False,
                    "path": remote_path,
                }
            logger.error(f"Create directory failed for {remote_path}: {e}")
            raise FTPTransientError(f"Create directory failed: {e}") from e

    async def get_info(
        self,
        remote_path: str
    ) -> Dict[str, Any]:
        """Get file/directory metadata."""
        try:
            stat_info = await self._client.stat(remote_path)

            # Parse info
            is_dir = stat_info.get("type") == "dir"
            size = int(stat_info.get("size", 0))

            # Parse modification time
            modified_str = stat_info.get("modify", "")
            try:
                modified = datetime.strptime(modified_str, "%Y%m%d%H%M%S").isoformat()
            except (ValueError, TypeError):
                modified = modified_str

            return {
                "type": "directory" if is_dir else "file",
                "size": size,
                "modified": modified,
                "permissions": stat_info.get("unix.mode", ""),
            }

        except Exception as e:
            logger.error(f"Get info failed for {remote_path}: {e}")
            raise FTPPermanentError(f"Get info failed: {e}") from e

    async def _ensure_directory(self, path: str):
        """Recursively create directory if it doesn't exist."""
        parts = Path(path).parts
        current_path = ""

        for part in parts:
            current_path = str(Path(current_path) / part)
            try:
                await self._client.make_directory(current_path)
            except Exception:
                # Directory might already exist, ignore
                pass

    async def _path_exists(self, path: str) -> bool:
        """Check if path exists."""
        try:
            await self._client.stat(path)
            return True
        except Exception:
            return False

    async def _is_directory(self, path: str) -> bool:
        """Check if path is a directory."""
        try:
            stat_info = await self._client.stat(path)
            return stat_info.get("type") == "dir"
        except Exception:
            return False


class SFTPClient(FTPClientBase):
    """SFTP client implementation using asyncssh."""

    def __init__(self):
        super().__init__()
        self._conn = None
        self._sftp = None

    async def connect(
        self,
        credentials: FTPCredentials,
        connection_config: FTPConnectionConfig,
        ssl_config: Optional[FTPSSLConfig] = None
    ):
        """Establish connection to SFTP server."""
        try:
            import asyncssh

            # Load private key if provided
            client_keys = None
            if credentials.private_key:
                client_keys = [credentials.private_key]

            # Connect to SSH server
            self._conn = await asyncssh.connect(
                host=credentials.host,
                port=credentials.port,
                username=credentials.username,
                password=credentials.password,
                client_keys=client_keys,
                passphrase=credentials.private_key_passphrase,
                known_hosts=None,  # Disable known_hosts checking
                connect_timeout=connection_config.timeout,
                encoding=connection_config.encoding,
            )

            # Start SFTP client
            self._sftp = await self._conn.start_sftp_client()
            self._connected = True

            logger.debug(f"Connected to sftp://{credentials.host}:{credentials.port}")

        except Exception as e:
            logger.error(f"Failed to connect to SFTP server: {e}")
            raise FTPPermanentError(f"SFTP connection failed: {e}") from e

    async def disconnect(self):
        """Close connection to SFTP server."""
        if self._sftp:
            self._sftp.exit()
        if self._conn:
            self._conn.close()
            await self._conn.wait_closed()
        self._connected = False
        logger.debug("Disconnected from SFTP server")

    async def upload(
        self,
        local_path: Optional[str],
        remote_path: str,
        config: FTPUploadConfig
    ) -> Dict[str, Any]:
        """Upload file to SFTP server."""
        start_time = asyncio.get_event_loop().time()
        bytes_transferred = 0

        try:
            # Create parent directories if needed
            if config.create_directories:
                parent_path = str(Path(remote_path).parent)
                if parent_path != "/" and parent_path != ".":
                    await self._ensure_directory(parent_path)

            # Upload from content or file
            if config.content:
                content_bytes = config.content.encode('utf-8')
                bytes_transferred = len(content_bytes)
                await self._sftp.put(
                    io.BytesIO(content_bytes),
                    remote_path,
                )
            else:
                if not local_path:
                    raise FTPPermanentError("Either local_path or content must be provided for upload")
                await self._sftp.put(local_path, remote_path)
                bytes_transferred = Path(local_path).stat().st_size

            duration_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)

            return {
                "bytes_transferred": bytes_transferred,
                "duration_ms": duration_ms,
            }

        except Exception as e:
            logger.error(f"SFTP upload failed for {remote_path}: {e}")
            raise FTPTransientError(f"Upload failed: {e}") from e

    async def download(
        self,
        remote_path: str,
        local_path: Optional[str],
        config: FTPDownloadConfig
    ) -> Dict[str, Any]:
        """Download file from SFTP server."""
        bytes_transferred = 0
        content = None

        try:
            if local_path:
                await self._sftp.get(remote_path, local_path)
                bytes_transferred = Path(local_path).stat().st_size
            else:
                # Download to memory
                buffer = io.BytesIO()
                await self._sftp.get(remote_path, buffer)
                content = buffer.getvalue()
                bytes_transferred = len(content)

            return {
                "bytes_transferred": bytes_transferred,
                "content": content,
                "local_path": local_path,
            }

        except Exception as e:
            logger.error(f"SFTP download failed for {remote_path}: {e}")
            raise FTPTransientError(f"Download failed: {e}") from e

    async def list_directory(
        self,
        remote_path: str,
        config: FTPListConfig
    ) -> List[Dict[str, Any]]:
        """List SFTP directory contents."""
        items = []

        try:
            # List directory
            entries = await self._sftp.listdir(remote_path)

            for entry in entries:
                name = entry.filename

                # Apply filters
                if not config.include_hidden and name.startswith("."):
                    continue
                if config.pattern and not fnmatch.fnmatch(name, config.pattern):
                    continue

                # Get file attributes
                full_path = str(Path(remote_path) / name)
                attrs = entry.attrs

                # Determine type
                is_dir = attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY

                # Get modification time
                modified = datetime.fromtimestamp(attrs.mtime).isoformat() if attrs.mtime else ""

                items.append({
                    "name": name,
                    "type": "directory" if is_dir else "file",
                    "size": attrs.size or 0,
                    "modified": modified,
                    "path": full_path,
                })

                # Recurse if needed
                if config.recursive and is_dir:
                    sub_items = await self.list_directory(full_path, config)
                    items.extend(sub_items)

            return items

        except Exception as e:
            logger.error(f"SFTP list directory failed for {remote_path}: {e}")
            raise FTPTransientError(f"List failed: {e}") from e

    async def delete(
        self,
        remote_path: str,
        config: FTPDeleteConfig
    ) -> bool:
        """Delete file or directory via SFTP."""
        try:
            # Check if exists
            exists = await self._sftp.exists(remote_path)
            if not exists:
                if config.ignore_missing:
                    return False
                raise FTPPermanentError(f"Path not found: {remote_path}")

            # Check if directory
            attrs = await self._sftp.stat(remote_path)
            is_dir = attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY

            if is_dir:
                if config.recursive:
                    await self._sftp.rmtree(remote_path)
                else:
                    await self._sftp.rmdir(remote_path)
            else:
                await self._sftp.remove(remote_path)

            return True

        except FTPPermanentError:
            raise
        except Exception as e:
            logger.error(f"SFTP delete failed for {remote_path}: {e}")
            raise FTPTransientError(f"Delete failed: {e}") from e

    async def rename(
        self,
        old_path: str,
        new_path: str,
        config: FTPRenameConfig
    ) -> Dict[str, str]:
        """Rename/move file or directory via SFTP."""
        try:
            # Check if destination exists
            if not config.overwrite and await self._sftp.exists(new_path):
                raise FTPPermanentError(f"Destination already exists: {new_path}")

            await self._sftp.rename(old_path, new_path)

            return {
                "old_path": old_path,
                "new_path": new_path,
            }

        except FTPPermanentError:
            raise
        except Exception as e:
            logger.error(f"SFTP rename failed from {old_path} to {new_path}: {e}")
            raise FTPTransientError(f"Rename failed: {e}") from e

    async def create_directory(
        self,
        remote_path: str,
        config: FTPCreateDirectoryConfig
    ) -> Dict[str, Any]:
        """Create directory via SFTP."""
        try:
            if config.recursive:
                await self._ensure_directory(remote_path)
            else:
                await self._sftp.mkdir(remote_path)

            return {
                "created": True,
                "path": remote_path,
            }

        except Exception as e:
            if config.ignore_exists and await self._sftp.exists(remote_path):
                return {
                    "created": False,
                    "path": remote_path,
                }
            logger.error(f"SFTP create directory failed for {remote_path}: {e}")
            raise FTPTransientError(f"Create directory failed: {e}") from e

    async def get_info(
        self,
        remote_path: str
    ) -> Dict[str, Any]:
        """Get file/directory metadata via SFTP."""
        try:
            import asyncssh

            attrs = await self._sftp.stat(remote_path)

            is_dir = attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY
            modified = datetime.fromtimestamp(attrs.mtime).isoformat() if attrs.mtime else ""

            return {
                "type": "directory" if is_dir else "file",
                "size": attrs.size or 0,
                "modified": modified,
                "permissions": oct(attrs.permissions) if attrs.permissions else "",
            }

        except Exception as e:
            logger.error(f"SFTP get info failed for {remote_path}: {e}")
            raise FTPPermanentError(f"Get info failed: {e}") from e

    async def _ensure_directory(self, path: str):
        """Recursively create directory if it doesn't exist."""
        parts = Path(path).parts
        current_path = ""

        for part in parts:
            current_path = str(Path(current_path) / part)
            if not await self._sftp.exists(current_path):
                try:
                    await self._sftp.mkdir(current_path)
                except Exception:
                    pass


class FTPService:
    """FTP service with connection pooling and operation execution."""

    def __init__(self):
        self._clients: Dict[str, FTPClientBase] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    async def execute_operation(
        self,
        credentials: FTPCredentials,
        connection_config: FTPConnectionConfig,
        ssl_config: Optional[FTPSSLConfig],
        operation: FTPOperationConfig,
    ) -> Dict[str, Any]:
        """Execute single FTP operation with retry logic."""
        client_key = self._build_client_key(credentials)

        # Get or create client
        client, lock = await self._get_client(credentials, connection_config, ssl_config)

        # Execute operation with lock (one operation at a time per connection)
        async with lock:
            return await self._execute_with_retry(
                client,
                operation,
                connection_config.max_retries,
                connection_config.retry_delay,
            )

    async def cleanup(self, client_key: Optional[str] = None):
        """Close connections (specific or all)."""
        if client_key:
            if client_key in self._clients:
                await self._clients[client_key].disconnect()
                del self._clients[client_key]
                del self._locks[client_key]
        else:
            for client in self._clients.values():
                await client.disconnect()
            self._clients.clear()
            self._locks.clear()

    async def _get_client(
        self,
        credentials: FTPCredentials,
        connection_config: FTPConnectionConfig,
        ssl_config: Optional[FTPSSLConfig],
    ) -> tuple[FTPClientBase, asyncio.Lock]:
        """Get or create FTP client (connection pooling)."""
        client_key = self._build_client_key(credentials)

        if client_key not in self._clients:
            # Create new client
            client = self._create_client(credentials.protocol)
            await client.connect(credentials, connection_config, ssl_config)
            self._clients[client_key] = client
            self._locks[client_key] = asyncio.Lock()

        return self._clients[client_key], self._locks[client_key]

    def _build_client_key(self, credentials: FTPCredentials) -> str:
        """Build unique key for connection pooling."""
        return f"{credentials.protocol.value}://{credentials.username}@{credentials.host}:{credentials.port}"

    def _create_client(self, protocol: FTPProtocol) -> FTPClientBase:
        """Create protocol-specific client."""
        if protocol in (FTPProtocol.FTP, FTPProtocol.FTPS):
            return FTPClient()
        elif protocol == FTPProtocol.SFTP:
            return SFTPClient()
        else:
            raise FTPPermanentError(f"Unsupported protocol: {protocol}")

    async def _execute_with_retry(
        self,
        client: FTPClientBase,
        operation: FTPOperationConfig,
        max_retries: int,
        retry_delay: float,
    ) -> Dict[str, Any]:
        """Execute operation with retry logic."""
        last_error = None

        for attempt in range(max_retries):
            try:
                # Execute based on operation type
                result = await self._execute_operation_impl(client, operation)
                return {
                    "ok": True,
                    "operation": operation.operation_type.value,
                    "data": result,
                    "error": None,
                }

            except FTPPermanentError as e:
                # Don't retry permanent errors
                return {
                    "ok": False,
                    "operation": operation.operation_type.value,
                    "data": None,
                    "error": str(e),
                }

            except FTPTransientError as e:
                last_error = e
                if attempt < max_retries - 1:
                    # Exponential backoff
                    await asyncio.sleep(retry_delay * (2 ** attempt))
                    continue
                else:
                    return {
                        "ok": False,
                        "operation": operation.operation_type.value,
                        "data": None,
                        "error": str(e),
                    }

        # Should not reach here
        return {
            "ok": False,
            "operation": operation.operation_type.value,
            "data": None,
            "error": str(last_error) if last_error else "Unknown error",
        }

    async def _execute_operation_impl(
        self,
        client: FTPClientBase,
        operation: FTPOperationConfig,
    ) -> Any:
        """Execute operation implementation."""
        op_type = operation.operation_type

        if op_type == FTPOperationType.UPLOAD:
            if not operation.upload:
                raise FTPPermanentError("Upload config is required for upload operation")
            return await client.upload(
                operation.upload.local_path,
                operation.upload.remote_path,
                operation.upload,
            )

        elif op_type == FTPOperationType.DOWNLOAD:
            if not operation.download:
                raise FTPPermanentError("Download config is required for download operation")
            return await client.download(
                operation.download.remote_path,
                operation.download.local_path,
                operation.download,
            )

        elif op_type == FTPOperationType.LIST:
            if not operation.list:
                raise FTPPermanentError("List config is required for list operation")
            items = await client.list_directory(
                operation.list.remote_path,
                operation.list,
            )
            return {"items": items}

        elif op_type == FTPOperationType.DELETE:
            if not operation.delete:
                raise FTPPermanentError("Delete config is required for delete operation")
            deleted = await client.delete(
                operation.delete.remote_path,
                operation.delete,
            )
            return {"deleted": deleted}

        elif op_type == FTPOperationType.RENAME:
            if not operation.rename:
                raise FTPPermanentError("Rename config is required for rename operation")
            return await client.rename(
                operation.rename.old_path,
                operation.rename.new_path,
                operation.rename,
            )

        elif op_type == FTPOperationType.CREATE_DIRECTORY:
            if not operation.create_directory:
                raise FTPPermanentError("Create directory config is required")
            return await client.create_directory(
                operation.create_directory.remote_path,
                operation.create_directory,
            )

        elif op_type == FTPOperationType.GET_INFO:
            if not operation.get_info:
                raise FTPPermanentError("Get info config is required")
            return await client.get_info(operation.get_info.remote_path)

        else:
            raise FTPPermanentError(f"Unsupported operation type: {op_type}")
