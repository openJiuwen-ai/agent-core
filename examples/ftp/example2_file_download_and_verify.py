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
    FTPProtocol,
    FTPDownloadConfig,
    FTPListConfig,
    FTPGetInfoConfig,
    create_workflow_session
)
from ._print_result_summary import _print_result_summary
from ._save_run import _save_run


async def example_2_file_download_and_verify(input_data: {} = {}):
    """
    Example 2: Download File and Verify

    Demonstrates:
    - Listing directory contents
    - Downloading a specific file
    - Getting file information
    """
    print("\n" + "=" * 60)
    print("Example 2: File Download and Verify")
    print("=" * 60)

    ftp_credentials = FTPCredentials(
        host=os.getenv("FTP_HOST", "eu-central-1.sftpcloud.io"),
        username=os.getenv("FTP_USERNAME", "some_username"),
        password=os.getenv("FTP_PASSWORD", "some_password"),
        protocol=FTPProtocol.FTP,
    )

    # List available files
    operation_list = FTPOperationConfig(
        operation_type=FTPOperationType.LIST,
        list=FTPListConfig(
            remote_path="/",
            pattern="*.*",
            recursive=False,
        ),
    )

    # Download the specific file
    file_name = "test_file.txt"
    base_dir = os.path.dirname(os.path.abspath(__file__))
    local_save_file = os.path.join(base_dir, "results")

    operation_download = FTPOperationConfig(
        operation_type=FTPOperationType.DOWNLOAD,
        download=FTPDownloadConfig(
            remote_path="/" + file_name,
            local_path=local_save_file,
            overwrite=True,
        ),
    )

    # Verify the downloaded file
    operation_get_info = FTPOperationConfig(
        operation_type=FTPOperationType.GET_INFO,
        get_info=FTPGetInfoConfig(
            remote_path="/" + file_name,
        ),
    )

    ftp_operations = [operation_list, operation_download, operation_get_info]

    ftp_config = FTPComponentConfig(credentials=ftp_credentials, operations=ftp_operations)

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

    # Execute workflow
    session = create_workflow_session()
    result = await workflow.invoke(input_data, session=session)

    run_dir = _save_run("example_2_file_download_and_verify", result)
    _print_result_summary("Example 2: File Download And Verify", result, run_dir)

    print("\nWorkflow created successfully!")
    print("Operations:")
    print("  1. List CSV files in /data directory")
    print("  2. Download report.csv")
    print("  3. Get file information")
