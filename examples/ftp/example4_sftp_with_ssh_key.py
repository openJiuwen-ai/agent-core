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
    create_workflow_session
)
from ._print_result_summary import _print_result_summary
from ._save_run import _save_run


async def example_4_sftp_with_ssh_key(input_data: {} = {}):
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

    ftp_credentials = FTPCredentials(
        host=os.getenv("SFTP_HOST", "eu-central-1.sftpcloud.io"),
        port=22,
        username=os.getenv("SFTP_USERNAME", "some_username"),
        private_key=os.getenv("SSH_KEY_PATH", "/home/user/.ssh/id_rsa"),
        private_key_passphrase=os.getenv("SSH_KEY_PASSPHRASE"),
        protocol=FTPProtocol.SFTP,
    )

    ftp_connection = FTPConnectionConfig(
        timeout=60,
        encoding="utf-8",
    )

    # List deployment directory
    operation_list = FTPOperationConfig(
        operation_type=FTPOperationType.LIST,
        list=FTPListConfig(
            remote_path="/var/www/app",
            recursive=False,
        ),
    )

    # Upload a new version
    operation_upload = FTPOperationConfig(
        operation_type=FTPOperationType.UPLOAD,
        upload=FTPUploadConfig(
            local_path="/tmp/app-v2.0.tar.gz",
            remote_path="/var/www/app/app-v2.0.tar.gz",
        ),
    )

    ftp_operations = [operation_list, operation_upload]

    ftp_config = FTPComponentConfig(credentials=ftp_credentials, connection=ftp_connection, operations=ftp_operations)

    workflow = Workflow(card=WorkflowCard(name="sftp_deployment"))
    workflow.set_start_comp("start", Start())
    workflow.add_workflow_comp("sftp_deploy", FTPComponent(config=ftp_config))
    workflow.set_end_comp("end", End())
    workflow.add_connection("start", "sftp_deploy")
    workflow.add_connection("sftp_deploy", "end")

    # Execute workflow
    session = create_workflow_session()
    result = await workflow.invoke(input_data, session=session)

    run_dir = _save_run("example_4_sftp_with_ssh_key", result)
    _print_result_summary("Example 4: SFTP with ssh key", result, run_dir)

    print("\nWorkflow created successfully!")
    print(f"Protocol: SFTP (SSH File Transfer)")
    print(f"Authentication: SSH Key")
    print(f"Key Path: {ftp_config.credentials.private_key}")
