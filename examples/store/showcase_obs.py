# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Example script demonstrating object storage (OBS) operations with AioBotoClient
"""

import asyncio
import logging
import os
from pathlib import Path

from configs import BUCKET_NAME
from utils.output import write_output

from openjiuwen.core.common.logging import logger as openjiuwen_logger
from openjiuwen.core.foundation.store.object.aioboto_storage_client import AioBotoClient

EXAMPLE_ROOT = Path(__file__).resolve().parent
DATA_DIR = EXAMPLE_ROOT / "data"

# Test file configuration (feel free to edit)
TEST_OBJECT_NAME = "test/test.txt"
TEST_FILE_PATH = DATA_DIR / "test.txt"
DOWNLOAD_FILE_PATH = DATA_DIR / "download_test.txt"
if DOWNLOAD_FILE_PATH.exists():
    os.remove(DOWNLOAD_FILE_PATH)


async def main():
    """Main example demonstrating object storage operations"""
    # Omit all INFO level logs
    openjiuwen_logger.set_level(logging.WARNING)

    # Initialize OBS client
    write_output("Initializing AioBotoClient...")
    aclient = AioBotoClient()
    write_output("Bucket name: %s", BUCKET_NAME)
    write_output("")

    # Step 1: List objects before operations
    write_output("=" * 60)
    write_output("Step 1: Listing objects in bucket (before operations)")
    write_output("=" * 60)
    objects_before = await aclient.list_objects(bucket_name=BUCKET_NAME, object_prefix="")
    object_count_before = len(objects_before) if objects_before else 0
    write_output("Found %d object(s) in bucket", object_count_before)
    write_output("")

    # Step 2: Delete object if it exists (cleanup)
    write_output("=" * 60)
    write_output("Step 2: Cleaning up existing object (if any)")
    write_output("=" * 60)
    write_output("Deleting object: %s", TEST_OBJECT_NAME)
    await aclient.delete_object(bucket_name=BUCKET_NAME, object_name=TEST_OBJECT_NAME)
    write_output("Cleanup completed")
    write_output("")

    # Step 3: Upload file
    write_output("=" * 60)
    write_output("Step 3: Uploading file to object storage")
    write_output("=" * 60)
    if not TEST_FILE_PATH.exists():
        write_output("ERROR: Test file not found at %s", TEST_FILE_PATH)
        write_output("Please create the test file first")
        return

    write_output("Uploading file: %s", TEST_FILE_PATH)
    write_output("Object name: %s", TEST_OBJECT_NAME)
    await aclient.upload_file(bucket_name=BUCKET_NAME, object_name=TEST_OBJECT_NAME, file_path=TEST_FILE_PATH)
    write_output("Upload completed successfully")
    write_output("")

    # Step 4: Download file
    write_output("=" * 60)
    write_output("Step 4: Downloading file from object storage")
    write_output("=" * 60)
    write_output("Downloading object: %s", TEST_OBJECT_NAME)
    write_output("Saving to: %s", DOWNLOAD_FILE_PATH)
    await aclient.download_file(bucket_name=BUCKET_NAME, object_name=TEST_OBJECT_NAME, file_path=DOWNLOAD_FILE_PATH)
    write_output("Download completed successfully")
    write_output("")

    # Step 5: Verify download
    write_output("=" * 60)
    write_output("Step 5: Verifying downloaded file")
    write_output("=" * 60)
    if DOWNLOAD_FILE_PATH.exists():
        original_size = TEST_FILE_PATH.stat().st_size
        downloaded_size = DOWNLOAD_FILE_PATH.stat().st_size
        write_output("Original file size: %d bytes", original_size)
        write_output("Downloaded file size: %d bytes", downloaded_size)
        if original_size == downloaded_size:
            write_output("✓ PASS: File sizes match")
        else:
            write_output("✗ FAIL: File sizes do not match")
    else:
        write_output("✗ FAIL: Downloaded file not found")
    write_output("")

    # Step 6: List objects after operations
    write_output("=" * 60)
    write_output("Step 6: Listing objects in bucket (after operations)")
    write_output("=" * 60)
    objects_after = await aclient.list_objects(bucket_name=BUCKET_NAME, object_prefix="")
    object_count_after = len(objects_after) if objects_after else 0
    write_output("Found %d object(s) in bucket", object_count_after)
    if object_count_after > object_count_before:
        write_output("✓ PASS: Object count increased (upload successful)")
    write_output("")

    # Step 7: Cleanup - delete uploaded object
    write_output("=" * 60)
    write_output("Step 7: Cleaning up uploaded object")
    write_output("=" * 60)
    write_output("Deleting object: %s", TEST_OBJECT_NAME)
    await aclient.delete_object(bucket_name=BUCKET_NAME, object_name=TEST_OBJECT_NAME)
    write_output("Cleanup completed")
    write_output("")

    # Summary
    write_output("=" * 60)
    write_output("Summary:")
    write_output("=" * 60)
    write_output("✓ Upload operation: Completed")
    write_output("✓ Download operation: Completed")
    write_output("✓ File verification: %s", "Passed" if DOWNLOAD_FILE_PATH.exists() else "Failed")
    write_output("✓ Cleanup operation: Completed")
    write_output("")
    write_output("All object storage operations completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
