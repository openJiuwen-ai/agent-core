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
)


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

    ftp_credentials = FTPCredentials(
        host=os.getenv("FTP_HOST", "ftp.example.com"),
        username=os.getenv("FTP_USERNAME", "user"),
        password=os.getenv("FTP_PASSWORD", "pass"),
        protocol=FTPProtocol.FTP,
    )

    # List available files
    operation_list = FTPOperationConfig(
        operation_type=FTPOperationType.LIST,
        list=FTPListConfig(
            remote_path="/data",
            pattern="*.csv",
            recursive=False,
        ),
    )

    # Download the specific file
    operation_download = FTPOperationConfig(
        operation_type=FTPOperationType.DOWNLOAD,
        download=FTPDownloadConfig(
            remote_path="/data/report.csv",
            local_path="/tmp/downloaded_report.csv",
            overwrite=True,
        ),
    )

    # Verify the downloaded file
    operation_get_info = FTPOperationConfig(
        operation_type=FTPOperationType.GET_INFO,
        get_info=FTPGetInfoConfig(
            remote_path="/data/report.csv",
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

    print("\nWorkflow created successfully!")
    print("Operations:")
    print("  1. List CSV files in /data directory")
    print("  2. Download report.csv")
    print("  3. Get file information")
