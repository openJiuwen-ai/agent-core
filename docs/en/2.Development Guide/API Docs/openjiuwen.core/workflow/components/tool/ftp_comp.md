# FTP Component for openJiuwen

The FTP Component is a powerful workflow component that provides comprehensive file transfer functionality for FTP, FTPS, and SFTP protocols. It allows you to automate file operations with remote servers as part of your agent workflows.

## Summary of Implementation

As of April 8, 2026, the FTP Component has been successfully implemented with the following key features:

### Core Architecture
- Inherits from `ComponentComposable` and `ComponentExecutable` as required by the openJiuwen framework
- Fully compatible with the workflow execution engine
- Follows the same patterns as HTTPRequestComponent and other components in the framework
- Service layer architecture with protocol-specific client implementations

### Implemented Features
- **Protocols**: Full support for FTP, FTPS (FTP over SSL/TLS), and SFTP (SSH File Transfer)
- **Operations**: Upload, Download, List, Delete, Rename, Create Directory, Get File Info
- **Authentication**: Username/Password for all protocols, SSH key authentication for SFTP
- **Security**: SSL/TLS encryption for FTPS, SSH encryption for SFTP, certificate verification
- **Connection Management**: Connection pooling, automatic retry with exponential backoff, keepalive
- **Advanced Features**: Template variable resolution, direct content upload, pattern-based filtering

### Files Created
- `openjiuwen/core/workflow/components/tool/ftp/ftp_config.py` - Configuration models
- `openjiuwen/core/workflow/components/tool/ftp/ftp_service.py` - Service layer and protocol clients
- `openjiuwen/core/workflow/components/tool/ftp/ftp_component.py` - Component implementation
- `openjiuwen/core/workflow/components/tool/ftp/__init__.py` - Module exports
- Updated `openjiuwen/core/workflow/__init__.py` - Added FTP component to exports
- Updated `pyproject.toml` - Added aioftp and asyncssh dependencies

### Testing
- Comprehensive unit tests with mock-based testing
- All operation types tested
- Error handling and retry logic verified
- Example usage demonstrated with 6 practical scenarios

## Features

### Protocols

#### FTP (File Transfer Protocol)
- Standard FTP over plain TCP
- Default port: 21
- Authentication: Username and password
- Passive mode support for firewall-friendly connections

#### FTPS (FTP over SSL/TLS)
- Encrypted FTP using SSL/TLS
- Default port: 21
- Authentication: Username and password
- SSL certificate verification
- Configurable TLS versions (TLSv1.2, TLSv1.3)
- Client certificate support

#### SFTP (SSH File Transfer Protocol)
- File transfer over SSH
- Default port: 22
- Authentication: Username/password or SSH private key
- Full SSH encryption
- Known hosts management

### Operations

#### Upload
- Upload files from local filesystem to remote server
- Upload content directly from strings (no local file needed)
- Auto-create parent directories
- Overwrite control
- Binary and ASCII transfer modes

#### Download
- Download files from remote server to local filesystem
- Download to memory (return content as bytes)
- Overwrite existing files control
- Binary and ASCII transfer modes

#### List Directory
- List directory contents with metadata (name, size, modified date, type)
- Recursive directory listing
- Pattern-based filtering (e.g., "*.txt", "backup_*")
- Include/exclude hidden files control

#### Delete
- Delete files or directories
- Recursive directory deletion
- Ignore missing files option

#### Rename/Move
- Rename or move files and directories
- Overwrite destination control

#### Create Directory
- Create directories on remote server
- Recursive creation (create parent directories)
- Ignore if exists option

#### Get File Info
- Retrieve file or directory metadata
- File size, modification time, permissions, type

### Connection Management

#### Connection Pooling
- Automatic connection reuse across operations
- Connection keyed by (host, port, username, protocol)
- Per-connection locking for thread safety

#### Retry Logic
- Automatic retry on transient errors
- Configurable max retries (default: 3)
- Exponential backoff strategy
- Configurable retry delay (default: 1 second)
- Smart error classification (transient vs permanent)

#### Timeout Configuration
- Connection timeout (default: 30 seconds)
- Keepalive interval (default: 30 seconds)
- Custom encoding support (default: UTF-8)

### Security Features

- **SSL/TLS Encryption** for FTPS
  - Certificate verification (configurable)
  - Custom SSL versions
  - Client certificates support

- **SSH Encryption** for SFTP
  - Private key authentication
  - Key passphrase support
  - Full session encryption

- **Credential Protection**
  - No credential logging
  - Secure password handling
  - Environment variable support

## Usage Examples

### Basic FTP File Upload

```python
from openjiuwen.core.workflow import (
    FTPComponent,
    FTPComponentConfig,
    FTPCredentials,
    FTPConnectionConfig,
    FTPOperationConfig,
    FTPOperationType,
    FTPProtocol,
    FTPUploadConfig
)

# Create configuration for FTP upload
config = FTPComponentConfig(
    credentials=FTPCredentials(
        host="ftp.example.com",
        port=21,
        username="ftpuser",
        password="ftppass",
        protocol=FTPProtocol.FTP
    ),
    connection=FTPConnectionConfig(
        timeout=60,
        passive_mode=True,
        max_retries=3
    ),
    operations=[
        FTPOperationConfig(
            operation_type=FTPOperationType.UPLOAD,
            upload=FTPUploadConfig(
                local_path="/tmp/backup.tar.gz",
                remote_path="/backups/backup_2026-04-08.tar.gz",
                create_directories=True,
                overwrite=True
            )
        )
    ]
)

# Create the FTP component
ftp_component = FTPComponent(config=config)
```

### FTPS Secure File Transfer

```python
from openjiuwen.core.workflow import FTPSSLConfig

# Create configuration for FTPS (FTP over SSL/TLS)
config = FTPComponentConfig(
    credentials=FTPCredentials(
        host="ftps.secure-server.com",
        port=21,
        username="secure_user",
        password="secure_pass",
        protocol=FTPProtocol.FTPS
    ),
    ssl_config=FTPSSLConfig(
        verify_ssl=True,
        ssl_version="TLSv1.3"
    ),
    operations=[
        FTPOperationConfig(
            operation_type=FTPOperationType.UPLOAD,
            upload=FTPUploadConfig(
                local_path="/tmp/sensitive_data.json",
                remote_path="/secure_uploads/data.json",
                overwrite=True
            )
        )
    ]
)

ftp_component = FTPComponent(config=config)
```

### SFTP with SSH Key Authentication

```python
# Create configuration for SFTP with SSH key
config = FTPComponentConfig(
    credentials=FTPCredentials(
        host="sftp.example.com",
        port=22,
        username="deploy_user",
        private_key="/home/user/.ssh/id_rsa",
        private_key_passphrase="key_passphrase",  # Optional
        protocol=FTPProtocol.SFTP
    ),
    operations=[
        FTPOperationConfig(
            operation_type=FTPOperationType.UPLOAD,
            upload=FTPUploadConfig(
                local_path="/tmp/app-v2.0.tar.gz",
                remote_path="/var/www/app/app-v2.0.tar.gz"
            )
        )
    ]
)

ftp_component = FTPComponent(config=config)
```

### Download and List Operations

```python
from openjiuwen.core.workflow import (
    FTPDownloadConfig,
    FTPListConfig
)

config = FTPComponentConfig(
    credentials=FTPCredentials(
        host="ftp.example.com",
        username="user",
        password="pass"
    ),
    operations=[
        # List CSV files
        FTPOperationConfig(
            operation_type=FTPOperationType.LIST,
            list=FTPListConfig(
                remote_path="/data",
                pattern="*.csv",
                recursive=False,
                include_hidden=False
            )
        ),
        # Download specific file
        FTPOperationConfig(
            operation_type=FTPOperationType.DOWNLOAD,
            download=FTPDownloadConfig(
                remote_path="/data/report.csv",
                local_path="/tmp/downloaded_report.csv",
                overwrite=True
            )
        )
    ]
)

ftp_component = FTPComponent(config=config)
```

### Directory Management

```python
from openjiuwen.core.workflow import (
    FTPCreateDirectoryConfig,
    FTPDeleteConfig,
    FTPRenameConfig
)

config = FTPComponentConfig(
    credentials=FTPCredentials(
        host="ftp.example.com",
        username="user",
        password="pass"
    ),
    operations=[
        # Create directory
        FTPOperationConfig(
            operation_type=FTPOperationType.CREATE_DIRECTORY,
            create_directory=FTPCreateDirectoryConfig(
                remote_path="/backups/2026/april",
                recursive=True,
                ignore_exists=True
            )
        ),
        # Rename file
        FTPOperationConfig(
            operation_type=FTPOperationType.RENAME,
            rename=FTPRenameConfig(
                old_path="/backups/current.tar.gz",
                new_path="/backups/2026/april/backup.tar.gz"
            )
        ),
        # Delete old file
        FTPOperationConfig(
            operation_type=FTPOperationType.DELETE,
            delete=FTPDeleteConfig(
                remote_path="/backups/old_backup.tar.gz",
                ignore_missing=True
            )
        )
    ]
)

ftp_component = FTPComponent(config=config)
```

### Direct Content Upload

```python
# Upload string content directly without a local file
config = FTPComponentConfig(
    credentials=FTPCredentials(
        host="ftp.example.com",
        username="user",
        password="pass"
    ),
    operations=[
        FTPOperationConfig(
            operation_type=FTPOperationType.UPLOAD,
            upload=FTPUploadConfig(
                content="Log entry: Operation completed successfully at 2026-04-08",
                remote_path="/logs/operation.log",
                create_directories=True
            )
        )
    ]
)

ftp_component = FTPComponent(config=config)
```

### Get File Information

```python
from openjiuwen.core.workflow import FTPGetInfoConfig

config = FTPComponentConfig(
    credentials=FTPCredentials(
        host="ftp.example.com",
        username="user",
        password="pass"
    ),
    operations=[
        FTPOperationConfig(
            operation_type=FTPOperationType.GET_INFO,
            get_info=FTPGetInfoConfig(
                remote_path="/data/large_file.dat"
            )
        )
    ]
)

ftp_component = FTPComponent(config=config)
```

## Integration with Workflows

The FTP Component follows the openJiuwen component architecture and can be seamlessly integrated into workflows with template variable support:

```python
from openjiuwen.core.workflow import (
    Workflow,
    WorkflowCard,
    Start,
    End,
    create_workflow_session
)

# Create workflow
workflow = Workflow(card=WorkflowCard(name="ftp_backup_workflow"))

# Define FTP component with template variables
config = FTPComponentConfig(
    credentials=FTPCredentials(
        host="${env.FTP_HOST}",  # From environment variable
        username="${env.FTP_USERNAME}",
        password="${env.FTP_PASSWORD}",
        protocol=FTPProtocol.FTPS
    ),
    operations=[
        FTPOperationConfig(
            operation_type=FTPOperationType.UPLOAD,
            upload=FTPUploadConfig(
                local_path="${start.backup_file}",  # From workflow input
                remote_path="/backups/${start.backup_filename}",
                create_directories=True
            )
        )
    ]
)

ftp_component = FTPComponent(config=config)

# Build workflow graph
workflow.set_start_comp("start", Start())
workflow.add_workflow_comp(
    comp_id="ftp_upload",
    workflow_comp=ftp_component,
    inputs_schema={}
)
workflow.set_end_comp(
    "end",
    End(),
    inputs_schema={
        "upload_result": "${ftp_upload.operations[0]}",
        "success": "${ftp_upload.ok}"
    }
)

# Connect nodes
workflow.add_connection("start", "ftp_upload")
workflow.add_connection("ftp_upload", "end")

# Execute workflow
session = create_workflow_session(workflow_name="ftp_backup")
result = await workflow.invoke(
    inputs={
        "backup_file": "/tmp/backup.tar.gz",
        "backup_filename": "backup_2026-04-08.tar.gz"
    },
    session=session
)

# Access results
print(f"Upload successful: {result.outputs['success']}")
print(f"Bytes transferred: {result.outputs['upload_result']['data']['bytes_transferred']}")
```

## Output Format

The FTP component returns a standardized output format:

```python
{
    "ok": True,  # Overall success
    "operations": [  # Individual operation results
        {
            "ok": True,
            "operation": "upload",
            "data": {
                "bytes_transferred": 1024000,
                "duration_ms": 1500
            },
            "error": None
        }
    ],
    "connection": {
        "host": "ftp.example.com",
        "protocol": "ftps",
        "connected": True
    },
    "error": None
}
```

### Operation-Specific Data Formats

**Upload:**
```python
{
    "bytes_transferred": 1024,
    "duration_ms": 500
}
```

**Download:**
```python
{
    "bytes_transferred": 2048,
    "content": b"...",  # If no local_path specified
    "local_path": "/tmp/file.txt"  # If local_path specified
}
```

**List:**
```python
{
    "items": [
        {
            "name": "file.txt",
            "type": "file",
            "size": 1024,
            "modified": "2026-04-08T10:30:00",
            "path": "/data/file.txt"
        }
    ]
}
```

**Delete:**
```python
{
    "deleted": True
}
```

**Rename:**
```python
{
    "old_path": "/old/path",
    "new_path": "/new/path"
}
```

**Create Directory:**
```python
{
    "created": True,
    "path": "/new/directory"
}
```

**Get Info:**
```python
{
    "type": "file",
    "size": 1024000,
    "modified": "2026-04-08T10:30:00",
    "permissions": "0644"
}
```

## Configuration Reference

### FTPComponentConfig

Main configuration class for the FTP component.

**Fields:**
- `credentials: FTPCredentials` - Server connection credentials (required)
- `connection: FTPConnectionConfig` - Connection settings (optional, defaults provided)
- `ssl_config: FTPSSLConfig` - SSL/TLS configuration for FTPS (optional)
- `operations: List[FTPOperationConfig]` - List of operations to execute (required)
- `metadata: WorkflowComponentMetadata` - Component metadata (optional)

### FTPCredentials

Server credentials and protocol configuration.

**Fields:**
- `host: str` - FTP server hostname or IP address (required)
- `port: int` - Server port (default: 21 for FTP/FTPS, 22 for SFTP)
- `username: str` - FTP username (required)
- `password: str` - FTP password (optional for SFTP with key auth)
- `private_key: str` - SSH private key path for SFTP (optional)
- `private_key_passphrase: str` - SSH key passphrase (optional)
- `protocol: FTPProtocol` - Protocol type: FTP, FTPS, or SFTP (default: FTP)

### FTPConnectionConfig

Connection behavior configuration.

**Fields:**
- `timeout: int` - Connection timeout in seconds (default: 30)
- `passive_mode: bool` - Use passive mode for FTP/FTPS (default: True)
- `encoding: str` - Character encoding (default: "utf-8")
- `keepalive_interval: int` - Keepalive interval in seconds (default: 30)
- `max_retries: int` - Maximum retry attempts (default: 3)
- `retry_delay: float` - Initial retry delay in seconds (default: 1.0)

### FTPSSLConfig

SSL/TLS configuration for FTPS.

**Fields:**
- `verify_ssl: bool` - Verify SSL certificates (default: True)
- `ssl_version: str` - SSL/TLS version (optional, e.g., "TLSv1.2", "TLSv1.3")
- `cert_file: str` - Client certificate file path (optional)
- `key_file: str` - Client key file path (optional)

## Best Practices

### Security

1. **Use FTPS or SFTP** for sensitive data transfers
2. **Enable SSL verification** in production environments
3. **Use environment variables** for credentials: `${env.FTP_PASSWORD}`
4. **Use SSH keys** instead of passwords for SFTP when possible
5. **Never hardcode credentials** in workflow definitions

### Performance

1. **Enable connection pooling** by reusing the same FTP component
2. **Use passive mode** for firewall-friendly connections
3. **Adjust timeouts** based on network conditions and file sizes
4. **Batch operations** in a single component to reuse connections

### Reliability

1. **Configure retry logic** for unstable connections
2. **Set appropriate timeouts** to avoid hanging operations
3. **Use ignore_missing** for delete operations to handle race conditions
4. **Verify uploads** with GET_INFO operation after upload

### Error Handling

1. **Check operation results**: Each operation has an `ok` field
2. **Handle partial failures**: Some operations may succeed while others fail
3. **Log errors**: Use the `error` field in operation results
4. **Implement fallbacks**: Use workflow branching based on FTP operation results

## Common Use Cases

### Automated Backups
Upload backup files to remote FTP server on a schedule.

### Log Collection
Download log files from remote servers for analysis.

### Deployment Workflows
Upload application files to web servers via SFTP.

### Data Synchronization
Keep files synchronized between systems using list and upload operations.

### Legacy System Integration
Integrate with legacy systems that only support FTP.

### File Processing Pipelines
Download files, process them, and upload results back to FTP server.

## Troubleshooting

### Connection Timeout
- Increase `timeout` in `FTPConnectionConfig`
- Check firewall settings
- Verify server is accessible

### Authentication Failed
- Verify credentials (username, password, or SSH key)
- Check SSH key file permissions (should be 600)
- Ensure SSH key passphrase is correct

### SSL Certificate Verification Failed
- For testing: Set `verify_ssl=False` in `FTPSSLConfig`
- For production: Obtain valid SSL certificate or add server certificate to trust store

### File Not Found
- Verify remote path is correct
- Check file permissions on server
- Ensure directory exists (use `create_directories=True` for uploads)

### Permission Denied
- Check user permissions on remote server
- Verify directory permissions allow read/write operations
- Contact server administrator if needed

## See Also

- [HTTP Request Component](./http_request_comp.md) - For HTTP/HTTPS requests
- [Workflow Components](../components.md) - Overview of all components
- [Example Code](../../../../../examples/ftp_workflow_example.py) - Complete usage examples
- [Unit Tests](../../../../../tests/unit_tests/core/component/test_ftp_component.py) - Test examples
