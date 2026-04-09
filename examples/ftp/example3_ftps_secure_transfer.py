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
    FTPCreateDirectoryConfig,
)


async def example_3_ftps_secure_transfer(input_data: {} = {}):
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

    ftp_credentials = FTPCredentials(
        host=os.getenv("FTPS_HOST", "ftps.secure-server.com"),
        port=21,
        username=os.getenv("FTPS_USERNAME", "secure_user"),
        password=os.getenv("FTPS_PASSWORD", "secure_pass"),
        protocol=FTPProtocol.FTPS,
    )

    ftp_ssl_config = FTPSSLConfig(
        verify_ssl=True,
        ssl_version="TLSv1.3",
        # cert_file="/path/to/client.crt",  # Optional client certificate
        # key_file="/path/to/client.key",   # Optional client key
    )

    ftp_connection = FTPConnectionConfig(
        timeout=30,
        max_retries=3,
    )

    # Create secure directory
    operation_create_directory = FTPOperationConfig(
        operation_type=FTPOperationType.CREATE_DIRECTORY,
        create_directory=FTPCreateDirectoryConfig(
            remote_path="/secure_uploads",
            recursive=True,
            ignore_exists=True,
        ),
    )

    # Upload sensitive file
    operation_upload = FTPOperationConfig(
        operation_type=FTPOperationType.UPLOAD,
        upload=FTPUploadConfig(
            local_path="/tmp/sensitive_data.json",
            remote_path="/secure_uploads/sensitive_data.json",
            overwrite=True,
        ),
    )

    ftp_operations = [operation_create_directory, operation_upload]

    ftp_config = FTPComponentConfig(credentials=ftp_credentials, ssl_config=ftp_ssl_config, connection=ftp_connection,
                                    operations=ftp_operations)

    workflow = Workflow(card=WorkflowCard(name="ftps_secure_transfer"))
    workflow.set_start_comp("start", Start())
    workflow.add_workflow_comp("ftps_upload", FTPComponent(config=ftp_config))
    workflow.set_end_comp("end", End())
    workflow.add_connection("start", "ftps_upload")
    workflow.add_connection("ftps_upload", "end")

    # Execute workflow
    session = create_workflow_session()
    result = await workflow.invoke(input_data, session=session)

    run_dir = _save_run("example_3_ftps_secure_transfer", result)
    _print_result_summary("Example 3: FTPS Secure Transfer", result, run_dir)

    print("\nWorkflow created successfully!")
    print(f"Protocol: FTPS (FTP over SSL/TLS)")
    print(f"SSL Version: {ftp_config.ssl_config.ssl_version}")
    print(f"SSL Verification: {ftp_config.ssl_config.verify_ssl}")
