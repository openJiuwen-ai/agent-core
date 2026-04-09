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

from examples.ftp.example1_basic_file_backup import example_1_basic_file_backup
from examples.ftp.example2_file_download_and_verify import example_2_file_download_and_verify
from examples.ftp.example3_ftp_secure_transfer import example_3_ftps_secure_transfer
from examples.ftp.example4_sftp_with_ssh_key import example_4_sftp_with_ssh_key
from examples.ftp.example5_directory_cleanup import example_5_directory_cleanup
from examples.ftp.example6_content_upload import example_6_content_upload


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
