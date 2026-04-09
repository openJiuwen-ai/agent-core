#!/usr/bin/env python3
# coding=utf-8
"""
FTP Component Workflow Examples Runner

Allows:
  - Running ALL FTP examples
  - Running a SINGLE example by number (1–6)

Matches the structure of browser_automation_example.py
"""

import asyncio

from examples.ftp.example1_basic_file_backup import example_1_basic_file_backup
from examples.ftp.example2_file_download_and_verify import example_2_file_download_and_verify
from examples.ftp.example3_ftp_secure_transfer import example_3_ftps_secure_transfer
from examples.ftp.example4_sftp_with_ssh_key import example_4_sftp_with_ssh_key
from examples.ftp.example5_directory_cleanup import example_5_directory_cleanup
from examples.ftp.example6_content_upload import example_6_content_upload


EXAMPLES = {
    1: ("Basic File Backup (FTP)", example_1_basic_file_backup),
    2: ("File Download + Verify", example_2_file_download_and_verify),
    3: ("FTPS Secure Transfer", example_3_ftps_secure_transfer),
    4: ("SFTP with SSH Key", example_4_sftp_with_ssh_key),
    5: ("Directory Cleanup", example_5_directory_cleanup),
    6: ("Content Upload", example_6_content_upload),
}


async def run_all_examples():
    print("\n" + "=" * 70)
    print("  FTP COMPONENT WORKFLOW EXAMPLES")
    print("=" * 70)

    for num, (name, fn) in EXAMPLES.items():
        print(f"\n--- Running Example {num}: {name} ---")
        await fn()

    print("\n" + "=" * 70)
    print("  All FTP examples executed")
    print("=" * 70)


async def run_single_example(example_num: int):
    if example_num not in EXAMPLES:
        print(f"Invalid example number: {example_num}. Choose 1–6.")
        return

    name, fn = EXAMPLES[example_num]
    print(f"\nRunning Example {example_num}: {name}")
    await fn()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        asyncio.run(run_single_example(int(sys.argv[1])))
    else:
        asyncio.run(run_all_examples())
