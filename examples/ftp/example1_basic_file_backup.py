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
    create_workflow_session,
)

from ._print_result_summary import _print_result_summary
from ._save_run import _save_run


async def example_1_basic_file_backup(input_data: {} = {}):
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

    ftp_credentials = FTPCredentials(
        host=os.getenv("FTP_HOST", "ftp.example.com"),
        port=int(os.getenv("FTP_PORT", "21")),
        username=os.getenv("FTP_USERNAME", "backup_user"),
        password=os.getenv("FTP_PASSWORD", "secure_password"),
        protocol=FTPProtocol.FTP,
    )

    ftp_connection = FTPConnectionConfig(
        timeout=60,
        passive_mode=True,
        max_retries=3,
        retry_delay=2.0,
    )

    # Upload backup file
    operation_upload = FTPOperationConfig(
        operation_type=FTPOperationType.UPLOAD,
        upload=FTPUploadConfig(
            local_path="/tmp/backup.tar.gz",
            remote_path="/backups/backup_2026-04-08.tar.gz",
            create_directories=True,
            overwrite=True,
        ),
    )

    ftp_operations = [operation_upload]

    # Create FTP component configuration
    ftp_config = FTPComponentConfig(credentials=ftp_credentials, connection=ftp_connection, operations=ftp_operations)

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
    session = create_workflow_session()
    result = await workflow.invoke(input_data, session=session)

    run_dir = _save_run("example_1_basic_file_backup", result)
    _print_result_summary("Example 1: Basic File Backup", result, run_dir)

    print("\nWorkflow created successfully!")
    print(f"FTP Host: {ftp_config.credentials.host}")
    print(f"Protocol: {ftp_config.credentials.protocol.value}")
    print("\nNote: To run this example, set FTP credentials in environment variables:")
    print("  export FTP_HOST=your-ftp-server.com")
    print("  export FTP_USERNAME=your-username")
    print("  export FTP_PASSWORD=your-password")
