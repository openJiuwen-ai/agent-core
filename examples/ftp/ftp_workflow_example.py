#!/usr/bin/env python3
# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
FTP Component Workflow Examples

This example demonstrates how to use the FTP component in workflows for various
file transfer scenarios including:
1. File backup to FTP server
2. File download from FTP server
3. Directory synchronization
4. FTPS (FTP over SSL) secure transfer
5. SFTP (SSH File Transfer) with key authentication
"""

import asyncio
import os
from openjiuwen.core.workflow import (
    Workflow,
    WorkflowCard,
    Start,
    End,
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
    FTPCreateDirectoryConfig,
    FTPGetInfoConfig,
    create_workflow_session,
)


async def example_1_basic_file_backup():
    """
    Example 1: Basic File Backup to FTP Server

    Demonstrates uploading a file to an FTP server with:
    - Connection configuration
    - Upload operation
    - Directory auto-creation
    """
    print("\n" + "=" * 60)
    print("Example 1: Basic File Backup to FTP Server")
    print("=" * 60)

    # Create FTP component configuration
    ftp_config = FTPComponentConfig(
        credentials=FTPCredentials(
            host=os.getenv("FTP_HOST", "ftp.example.com"),
            port=int(os.getenv("FTP_PORT", "21")),
            username=os.getenv("FTP_USERNAME", "backup_user"),
            password=os.getenv("FTP_PASSWORD", "secure_password"),
            protocol=FTPProtocol.FTP,
        ),
        connection=FTPConnectionConfig(
            timeout=60,
            passive_mode=True,
            max_retries=3,
            retry_delay=2.0,
        ),
        operations=[
            # Upload backup file
            FTPOperationConfig(
                operation_type=FTPOperationType.UPLOAD,
                upload=FTPUploadConfig(
                    local_path="/tmp/backup.tar.gz",
                    remote_path="/backups/backup_2026-04-08.tar.gz",
                    create_directories=True,
                    overwrite=True,
                ),
            ),
        ],
    )

    # Create workflow
    workflow = Workflow(card=WorkflowCard(name="ftp_backup_workflow"))
    workflow.set_start_comp("start", Start())
    workflow.add_workflow_comp(
        comp_id="ftp_upload",
        workflow_comp=FTPComponent(config=ftp_config),
        inputs_schema={},
    )
    workflow.set_end_comp(
        "end",
        End(),
        inputs_schema={
            "upload_result": "${ftp_upload.operations[0]}",
        },
    )
    workflow.add_connection("start", "ftp_upload")
    workflow.add_connection("ftp_upload", "end")

    # Execute workflow
    session = create_workflow_session(workflow_name="ftp_backup")

    print("\nWorkflow created successfully!")
    print(f"FTP Host: {ftp_config.credentials.host}")
    print(f"Protocol: {ftp_config.credentials.protocol.value}")
    print("\nNote: To run this example, set FTP credentials in environment variables:")
    print("  export FTP_HOST=your-ftp-server.com")
    print("  export FTP_USERNAME=your-username")
    print("  export FTP_PASSWORD=your-password")


async def example_2_file_download_and_verify():
    """
    Example 2: Download File and Verify

    Demonstrates:
    - Listing directory contents
    - Downloading specific file
    - Getting file information
    """
    print("\n" + "=" * 60)
    print("Example 2: File Download and Verify")
    print("=" * 60)

    ftp_config = FTPComponentConfig(
        credentials=FTPCredentials(
            host=os.getenv("FTP_HOST", "ftp.example.com"),
            username=os.getenv("FTP_USERNAME", "user"),
            password=os.getenv("FTP_PASSWORD", "pass"),
            protocol=FTPProtocol.FTP,
        ),
        operations=[
            # List available files
            FTPOperationConfig(
                operation_type=FTPOperationType.LIST,
                list=FTPListConfig(
                    remote_path="/data",
                    pattern="*.csv",
                    recursive=False,
                ),
            ),
            # Download specific file
            FTPOperationConfig(
                operation_type=FTPOperationType.DOWNLOAD,
                download=FTPDownloadConfig(
                    remote_path="/data/report.csv",
                    local_path="/tmp/downloaded_report.csv",
                    overwrite=True,
                ),
            ),
            # Verify downloaded file
            FTPOperationConfig(
                operation_type=FTPOperationType.GET_INFO,
                get_info=FTPGetInfoConfig(
                    remote_path="/data/report.csv",
                ),
            ),
        ],
    )

    workflow = Workflow(card=WorkflowCard(name="ftp_download_workflow"))
    workflow.set_start_comp("start", Start())
    workflow.add_workflow_comp("ftp_download", FTPComponent(config=ftp_config))
    workflow.set_end_comp(
        "end",
        End(),
        inputs_schema={
            "files_list": "${ftp_download.operations[0].data.items}",
            "downloaded_bytes": "${ftp_download.operations[1].data.bytes_transferred}",
            "file_info": "${ftp_download.operations[2].data}",
        },
    )
    workflow.add_connection("start", "ftp_download")
    workflow.add_connection("ftp_download", "end")

    print("\nWorkflow created successfully!")
    print("Operations:")
    print("  1. List CSV files in /data directory")
    print("  2. Download report.csv")
    print("  3. Get file information")


async def example_3_ftps_secure_transfer():
    """
    Example 3: FTPS Secure File Transfer

    Demonstrates:
    - FTPS (FTP over SSL/TLS) connection
    - SSL certificate verification
    - Secure file upload
    """
    print("\n" + "=" * 60)
    print("Example 3: FTPS Secure File Transfer")
    print("=" * 60)

    ftp_config = FTPComponentConfig(
        credentials=FTPCredentials(
            host=os.getenv("FTPS_HOST", "ftps.secure-server.com"),
            port=21,
            username=os.getenv("FTPS_USERNAME", "secure_user"),
            password=os.getenv("FTPS_PASSWORD", "secure_pass"),
            protocol=FTPProtocol.FTPS,
        ),
        ssl_config=FTPSSLConfig(
            verify_ssl=True,
            ssl_version="TLSv1.3",
            # cert_file="/path/to/client.crt",  # Optional client certificate
            # key_file="/path/to/client.key",   # Optional client key
        ),
        connection=FTPConnectionConfig(
            timeout=30,
            max_retries=3,
        ),
        operations=[
            # Create secure directory
            FTPOperationConfig(
                operation_type=FTPOperationType.CREATE_DIRECTORY,
                create_directory=FTPCreateDirectoryConfig(
                    remote_path="/secure_uploads",
                    recursive=True,
                    ignore_exists=True,
                ),
            ),
            # Upload sensitive file
            FTPOperationConfig(
                operation_type=FTPOperationType.UPLOAD,
                upload=FTPUploadConfig(
                    local_path="/tmp/sensitive_data.json",
                    remote_path="/secure_uploads/sensitive_data.json",
                    overwrite=True,
                ),
            ),
        ],
    )

    workflow = Workflow(card=WorkflowCard(name="ftps_secure_transfer"))
    workflow.set_start_comp("start", Start())
    workflow.add_workflow_comp("ftps_upload", FTPComponent(config=ftp_config))
    workflow.set_end_comp("end", End())
    workflow.add_connection("start", "ftps_upload")
    workflow.add_connection("ftps_upload", "end")

    print("\nWorkflow created successfully!")
    print(f"Protocol: FTPS (FTP over SSL/TLS)")
    print(f"SSL Version: {ftp_config.ssl_config.ssl_version}")
    print(f"SSL Verification: {ftp_config.ssl_config.verify_ssl}")


async def example_4_sftp_with_ssh_key():
    """
    Example 4: SFTP with SSH Key Authentication

    Demonstrates:
    - SFTP (SSH File Transfer Protocol)
    - SSH key-based authentication
    - Secure file operations
    """
    print("\n" + "=" * 60)
    print("Example 4: SFTP with SSH Key Authentication")
    print("=" * 60)

    ftp_config = FTPComponentConfig(
        credentials=FTPCredentials(
            host=os.getenv("SFTP_HOST", "sftp.example.com"),
            port=22,
            username=os.getenv("SFTP_USERNAME", "deploy_user"),
            private_key=os.getenv("SSH_KEY_PATH", "/home/user/.ssh/id_rsa"),
            private_key_passphrase=os.getenv("SSH_KEY_PASSPHRASE"),
            protocol=FTPProtocol.SFTP,
        ),
        connection=FTPConnectionConfig(
            timeout=60,
            encoding="utf-8",
        ),
        operations=[
            # List deployment directory
            FTPOperationConfig(
                operation_type=FTPOperationType.LIST,
                list=FTPListConfig(
                    remote_path="/var/www/app",
                    recursive=False,
                ),
            ),
            # Upload new version
            FTPOperationConfig(
                operation_type=FTPOperationType.UPLOAD,
                upload=FTPUploadConfig(
                    local_path="/tmp/app-v2.0.tar.gz",
                    remote_path="/var/www/app/app-v2.0.tar.gz",
                ),
            ),
        ],
    )

    workflow = Workflow(card=WorkflowCard(name="sftp_deployment"))
    workflow.set_start_comp("start", Start())
    workflow.add_workflow_comp("sftp_deploy", FTPComponent(config=ftp_config))
    workflow.set_end_comp("end", End())
    workflow.add_connection("start", "sftp_deploy")
    workflow.add_connection("sftp_deploy", "end")

    print("\nWorkflow created successfully!")
    print(f"Protocol: SFTP (SSH File Transfer)")
    print(f"Authentication: SSH Key")
    print(f"Key Path: {ftp_config.credentials.private_key}")


async def example_5_directory_cleanup():
    """
    Example 5: Directory Cleanup

    Demonstrates:
    - Listing files with pattern matching
    - Selective file deletion
    - Directory operations
    """
    print("\n" + "=" * 60)
    print("Example 5: Directory Cleanup")
    print("=" * 60)

    ftp_config = FTPComponentConfig(
        credentials=FTPCredentials(
            host=os.getenv("FTP_HOST", "ftp.example.com"),
            username=os.getenv("FTP_USERNAME", "user"),
            password=os.getenv("FTP_PASSWORD", "pass"),
        ),
        operations=[
            # List old backup files
            FTPOperationConfig(
                operation_type=FTPOperationType.LIST,
                list=FTPListConfig(
                    remote_path="/backups",
                    pattern="backup_old_*.tar.gz",
                ),
            ),
            # Delete old backup (example)
            FTPOperationConfig(
                operation_type=FTPOperationType.DELETE,
                delete=FTPDeleteConfig(
                    remote_path="/backups/backup_old_2026-01-01.tar.gz",
                    ignore_missing=True,
                ),
            ),
            # Rename current backup
            FTPOperationConfig(
                operation_type=FTPOperationType.RENAME,
                rename=FTPRenameConfig(
                    old_path="/backups/backup_current.tar.gz",
                    new_path="/backups/backup_2026-04-08.tar.gz",
                ),
            ),
        ],
    )

    workflow = Workflow(card=WorkflowCard(name="ftp_cleanup"))
    workflow.set_start_comp("start", Start())
    workflow.add_workflow_comp("ftp_cleanup", FTPComponent(config=ftp_config))
    workflow.set_end_comp("end", End())
    workflow.add_connection("start", "ftp_cleanup")
    workflow.add_connection("ftp_cleanup", "end")

    print("\nWorkflow created successfully!")
    print("Operations:")
    print("  1. List old backup files")
    print("  2. Delete old backup")
    print("  3. Rename current backup")


async def example_6_content_upload():
    """
    Example 6: Direct Content Upload

    Demonstrates:
    - Uploading content directly without a file
    - Creating files from strings
    """
    print("\n" + "=" * 60)
    print("Example 6: Direct Content Upload")
    print("=" * 60)

    ftp_config = FTPComponentConfig(
        credentials=FTPCredentials(
            host=os.getenv("FTP_HOST", "ftp.example.com"),
            username=os.getenv("FTP_USERNAME", "user"),
            password=os.getenv("FTP_PASSWORD", "pass"),
        ),
        operations=[
            # Upload text content directly
            FTPOperationConfig(
                operation_type=FTPOperationType.UPLOAD,
                upload=FTPUploadConfig(
                    content="This is a test file created by FTP component.\nGenerated at: 2026-04-08",
                    remote_path="/logs/test_log.txt",
                    create_directories=True,
                ),
            ),
        ],
    )

    workflow = Workflow(card=WorkflowCard(name="content_upload"))
    workflow.set_start_comp("start", Start())
    workflow.add_workflow_comp("ftp_upload", FTPComponent(config=ftp_config))
    workflow.set_end_comp("end", End())
    workflow.add_connection("start", "ftp_upload")
    workflow.add_connection("ftp_upload", "end")

    print("\nWorkflow created successfully!")
    print("Operation: Upload text content directly to /logs/test_log.txt")


async def main():
    """Run all examples"""
    print("\n" + "=" * 60)
    print("FTP Component Workflow Examples")
    print("=" * 60)
    print("\nThese examples demonstrate various FTP component use cases.")
    print("To run with real FTP servers, set environment variables:")
    print("  - FTP_HOST, FTP_USERNAME, FTP_PASSWORD (for FTP)")
    print("  - FTPS_HOST, FTPS_USERNAME, FTPS_PASSWORD (for FTPS)")
    print("  - SFTP_HOST, SFTP_USERNAME, SSH_KEY_PATH (for SFTP)")
    print("\n")

    # Run all examples
    await example_1_basic_file_backup()
    await example_2_file_download_and_verify()
    await example_3_ftps_secure_transfer()
    await example_4_sftp_with_ssh_key()
    await example_5_directory_cleanup()
    await example_6_content_upload()

    print("\n" + "=" * 60)
    print("All examples created successfully!")
    print("=" * 60)
    print("\nNote: These are workflow definitions. To actually execute them,")
    print("you would call workflow.invoke() with a proper session and inputs.")
    print("\nFor more information, see:")
    print("  - FTP Component documentation")
    print("  - tests/unit_tests/core/component/test_ftp_component.py")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
