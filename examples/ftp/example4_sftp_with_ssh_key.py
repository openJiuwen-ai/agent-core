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
    FTPOperationConfig,
    FTPOperationType,
    FTPProtocol,
    FTPUploadConfig,
    FTPListConfig,
)

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
