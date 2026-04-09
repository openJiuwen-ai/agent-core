import os

from openjiuwen.core.workflow import (
    Workflow,
    WorkflowCard,
    Start,
    End,
    FTPComponent,
    FTPComponentConfig,
    FTPCredentials,
    FTPOperationConfig,
    FTPOperationType,
    FTPUploadConfig,
    create_workflow_session
)
from ._print_result_summary import _print_result_summary
from ._save_run import _save_run



async def example_6_content_upload(input_data: {} = {}):
    """
    Example 6: Direct Content Upload

    Demonstrates:
    - Uploading content directly without a file
    - Creating files from strings
    """
    print("\n" + "=" * 60)
    print("Example 6: Direct Content Upload")
    print("=" * 60)

    ftp_credentials = FTPCredentials(
        host=os.getenv("FTP_HOST", "ftp.example.com"),
        username=os.getenv("FTP_USERNAME", "user"),
        password=os.getenv("FTP_PASSWORD", "pass"),
    )

    # Upload text content directly
    operation_upload = FTPOperationConfig(
        operation_type=FTPOperationType.UPLOAD,
        upload=FTPUploadConfig(
            content="This is a test file created by FTP component.\nGenerated at: 2026-04-08",
            remote_path="/logs/test_log.txt",
            create_directories=True,
        ),
    )

    ftp_operations = [operation_upload]

    ftp_config = FTPComponentConfig(credentials=ftp_credentials, operations=ftp_operations)

    workflow = Workflow(card=WorkflowCard(name="content_upload"))
    workflow.set_start_comp("start", Start())
    workflow.add_workflow_comp("ftp_upload", FTPComponent(config=ftp_config))
    workflow.set_end_comp("end", End())
    workflow.add_connection("start", "ftp_upload")
    workflow.add_connection("ftp_upload", "end")

    # Execute workflow
    session = create_workflow_session()
    result = await workflow.invoke(input_data, session=session)

    run_dir = _save_run("example_6_content_upload", result)
    _print_result_summary("Example 2: Content Upload", result, run_dir)

    print("\nWorkflow created successfully!")
    print("Operation: Upload text content directly to /logs/test_log.txt")
