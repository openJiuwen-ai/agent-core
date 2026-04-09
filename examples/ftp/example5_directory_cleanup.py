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
    FTPListConfig,
    FTPDeleteConfig,
    FTPRenameConfig,
)

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
